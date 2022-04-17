import ast
import json
import math
import re
from copy import deepcopy
from functools import partial
from types import FunctionType
from typing import Type, Tuple, Sequence, Dict, Callable, Any, Optional, List

import ujson
from django.apps import apps
from django.conf.urls import url
from django.db.models import QuerySet, Model
from rest_framework.authentication import BaseAuthentication
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet
from rest_framework_json_api.filters import QueryParameterValidationFilter, OrderingFilter
from rest_framework_json_api.metadata import JSONAPIMetadata
from rest_framework_json_api.parsers import JSONParser
from rest_framework_json_api.renderers import JSONRenderer
from rest_framework_json_api.views import RelationshipView, ModelViewSet

from drf_json_api_utils.common import DEFAULT_PAGE_SIZE, exception_handler
from . import json_api_spec_http_methods
from . import lookups as filter_lookups
from . import plugins
from .common import LimitedJsonApiPageNumberPagination, JsonApiSearchFilter, LOGGER, JsonApiGlobalSettings
from .constructors import _construct_serializer, _construct_filter_backend
from .json_api_spec_http_methods import HTTP_GET, HTTP_POST, HTTP_PATCH, HTTP_DELETE
from .namespace import _append_to_namespace, _RESOURCE_NAME_TO_SPICE, _MODEL_TO_SERIALIZERS
from .types import CustomField, Filter, Relation, GenericRelation, ComputedFilter, RelatedResource

FILTER_REGEX = re.compile(r'filter\[(?P<field>[\w_\-]+)[\.]*(?P<op>[\w_\-]+)?\]', re.IGNORECASE)
FILTER_MAP = {
    'is_null': 'is_null',
    'is_not_null': 'is_not_null',
    'eq': '==',
    'ne': '!=',
    'gt': '>',
    'lt': '<',
    'gte': '>=',
    'gle': '<=',
    'contains': 'like',
    'icontains': 'ilike',
    'not_icontains': 'not_ilike',
    'not_contains': 'not_like',
    'in': 'in',
    'not_in': 'not_in',
    'any': 'any',
    'not_any': 'not_any',
}


def get_dict_by_methods(view_type, allowed_http_methods):
    out = {}
    if view_type == 'get':
        if HTTP_GET in allowed_http_methods:
            out['get'] = 'retrieve'
        if HTTP_PATCH in allowed_http_methods:
            out['patch'] = 'update'
        if HTTP_DELETE in allowed_http_methods:
            out['delete'] = 'destroy'
    elif view_type == 'list':
        if HTTP_GET in allowed_http_methods:
            out['get'] = 'list'
        if HTTP_POST in allowed_http_methods:
            out['post'] = 'create'
    elif view_type == 'relation':
        if HTTP_GET in allowed_http_methods:
            out['get'] = 'retrieve_related'
    return out


DEFAULT_VOID_DECORATOR = lambda func: func


class JsonApiModelViewBuilder:
    DEFAULT_RELATED_LIMIT = 100

    ATTRIBUTES = {
        '_model': {'kwarg': 'model', 'default': None},
        '_fields': {'kwarg': None, 'default': {}},
        '_filters': {'kwarg': None, 'default': {}},
        '_computed_filters': {'kwarg': None, 'default': {}},
        '_relations': {'kwarg': None, 'default': {}},
        '_generic_relations': {'kwarg': None, 'default': {}},
        '_custom_fields': {'kwarg': None, 'default': {}},
        '_api_version': {'kwarg': 'api_version', 'default': ''},
        '_related_limit': {'kwarg': None, 'default': DEFAULT_RELATED_LIMIT},
        '_permission_classes': {'kwarg': 'permission_classes', 'default': []},
        '_authentication_classes': {'kwarg': 'authentication_classes', 'default': []},
        '_before_create_callback': {'kwarg': None, 'default': None},
        '_after_create_callback': {'kwarg': None, 'default': None},
        '_create_decorator': {'kwarg': None, 'default': DEFAULT_VOID_DECORATOR},
        '_after_get_callback': {'kwarg': None, 'default': None},
        '_get_decorator': {'kwarg': None, 'default': DEFAULT_VOID_DECORATOR},
        '_before_update_callback': {'kwarg': None, 'default': None},
        '_after_update_callback': {'kwarg': None, 'default': None},
        '_update_decorator': {'kwarg': None, 'default': DEFAULT_VOID_DECORATOR},
        '_before_delete_callback': {'kwarg': None, 'default': None},
        '_after_delete_callback': {'kwarg': None, 'default': None},
        '_delete_decorator': {'kwarg': None, 'default': DEFAULT_VOID_DECORATOR},
        '_before_list_callback': {'kwarg': None, 'default': None},
        '_after_list_callback': {'kwarg': None, 'default': None},
        '_list_decorator': {'kwarg': None, 'default': DEFAULT_VOID_DECORATOR},
        '_before_raw_response': {'kwarg': None, 'default': None},
        '_expose_related_views': {'kwarg': 'expose_related_views', 'default': None},
        '_is_admin': {'kwarg': 'is_admin', 'default': None},
        '_always_include': {'kwarg': 'always_include', 'default': None},
        '_spice_queryset': {'kwarg': 'permitted_objects', 'default': None},
        '_include_plugins': {'kwarg': 'include_plugins', 'default': []},
        '_plugin_options': {'kwarg': 'plugin_options', 'default': {}},
        '_allowed_methods': {'kwarg': 'allowed_methods', 'default': []},
        '_primary_key_name': {'kwarg': 'primary_key_name', 'default': 'id'},
        '_resource_name': {'kwarg': 'resource_name', 'default': None},
        '_page_size': {'kwarg': 'page_size', 'default': None},
        '_dummy_includes': {'kwarg': 'dummy_includes', 'default': []},
    }

    def __init__(self, model: Type[Model],
                 primary_key_name: Optional[str] = None,
                 resource_name: Optional[str] = None,
                 api_version: Optional[str] = '',
                 allowed_methods: Optional[List[str]] = json_api_spec_http_methods.HTTP_ALL,
                 permission_classes: Optional[Sequence[Type[BasePermission]]] = None,
                 authentication_classes: Optional[Sequence[Type[BaseAuthentication]]] = None,
                 queryset: Optional[QuerySet] = None,
                 permitted_objects: Optional[Callable[[Request, QuerySet], QuerySet]] = None,
                 include_plugins: Optional[Sequence[str]] = None,
                 plugin_options: Optional[Dict[str, Any]] = None,
                 expose_related_views: Optional[bool] = False,
                 is_admin: Optional[bool] = False,
                 always_include: Optional[bool] = False,
                 page_size: Optional[int] = DEFAULT_PAGE_SIZE):
        self.override(
            model=model,
            primary_key_name=primary_key_name,
            resource_name=resource_name,
            api_version=api_version,
            allowed_methods=allowed_methods,
            permission_classes=permission_classes,
            authentication_classes=authentication_classes,
            queryset=queryset,
            permitted_objects=permitted_objects,
            include_plugins=include_plugins,
            plugin_options=plugin_options,
            expose_related_views=expose_related_views,
            is_admin=is_admin,
            always_include=always_include,
            page_size=page_size
        )
        self.settings = JsonApiGlobalSettings()

    @staticmethod
    def from_view_builder(view_builder: 'JsonApiModelViewBuilder') -> 'JsonApiModelViewBuilder':
        return deepcopy(view_builder)

    def override(self, model: Optional[Type[Model]] = None,
                 primary_key_name: Optional[str] = None,
                 resource_name: Optional[str] = None,
                 api_version: Optional[str] = None,
                 allowed_methods: Optional[List[str]] = None,
                 permission_classes: Optional[Sequence[Type[BasePermission]]] = None,
                 authentication_classes: Optional[Sequence[Type[BaseAuthentication]]] = None,
                 queryset: Optional[QuerySet] = None,
                 permitted_objects: Optional[Callable[[Request, QuerySet], QuerySet]] = None,
                 include_plugins: Optional[Sequence[str]] = None,
                 plugin_options: Optional[Dict[str, Any]] = None,
                 expose_related_views: Optional[bool] = None,
                 is_admin: Optional[bool] = None,
                 always_include: Optional[bool] = False,
                 page_size: Optional[int] = None) -> 'JsonApiModelViewBuilder':
        if allowed_methods is not None:
            self.__validate_http_methods(allowed_methods)

        _locals = locals()
        for attribute, settings in self.ATTRIBUTES.items():
            _arg = _locals.get(settings.get("kwarg"))
            if _arg is not None:
                setattr(self, attribute, _arg)
            elif not hasattr(self, attribute):
                setattr(self, attribute, deepcopy(settings.get("default")))

        self._api_version = self._api_version.replace('.', '').replace('-', '')
        self._url_api_version = f'v{self._api_version}'
        self._allowed_methods = [*self._allowed_methods]
        if self._resource_name is None:
            self._resource_name = self._model.objects.model._meta.db_table.split('_')[-1]

        if queryset is None:
            self._queryset = self._model.objects
        else:
            self._queryset = queryset

        return self

    @property
    def is_admin(self) -> bool:
        return self._is_admin

    @property
    def always_include(self) -> bool:
        return self._always_include

    @staticmethod
    def __validate_http_methods(limit_to_http_methods: Sequence[str] = json_api_spec_http_methods.HTTP_ALL):
        if any(map(lambda method: method not in json_api_spec_http_methods.HTTP_ALL, limit_to_http_methods)):
            raise Exception(
                f'Cannot limit fields to HTTP Method of types: '
                f'{list(filter(lambda method: method not in json_api_spec_http_methods.HTTP_ALL, limit_to_http_methods))}')

    def __warn_if_method_not_available(self, method: str):
        if method not in self._allowed_methods:
            LOGGER.warning(
                f'You\'ve set a lifecycle callback for resource {self._resource_name}, '
                f'which doesn\'t allow it\'s respective HTTP method through `allowed_methods`.')

    def fields(self, fields: Sequence[str],
               limit_to_on_retrieve: bool = False) -> 'JsonApiModelViewBuilder':
        if limit_to_on_retrieve not in self._fields:
            self._fields[limit_to_on_retrieve] = []
        self._fields[limit_to_on_retrieve].extend(fields)
        return self

    def dummy_fields(self, fields: Sequence[str]) -> 'JsonApiModelViewBuilder':
        self.fields(fields=fields)
        _f = lambda instance: ''
        self.custom_fields(fields=[(field, _f,) for field in fields])
        return self

    def add_field(self, name: str, limit_to_on_retrieve: bool = False) -> 'JsonApiModelViewBuilder':
        if limit_to_on_retrieve not in self._fields:
            self._fields[limit_to_on_retrieve] = []
        self._fields[limit_to_on_retrieve].append(name)
        return self

    def add_dummy_field(self, name: str) -> 'JsonApiModelViewBuilder':
        self.add_field(name=name)
        self.add_custom_field(name=name, instance_callback=lambda instance: '')
        return self

    def add_filter(self, name: str, field: str = None, lookups: Sequence[str] = None,
                   transform_value: Callable[
                       [str, QuerySet], Tuple[str, QuerySet]] = None) -> 'JsonApiModelViewBuilder':
        if lookups is None:
            lookups = (filter_lookups.EXACT,)
        if any(map(lambda lookup: lookup not in filter_lookups.ALL, lookups)):
            raise Exception(
                f'Filter lookups are invalid: '
                f'{list(filter(lambda lookup: lookup not in filter_lookups.ALL, lookups))}')
        self._filters[name] = Filter(field=field or name, lookups=lookups, transform_value=transform_value)
        return self

    def add_computed_filter(self, name: str, filter_type: Filter,
                            filter_func: Callable[[QuerySet, str], Any],
                            field: str = None) -> 'JsonApiModelViewBuilder':
        self._computed_filters[name] = ComputedFilter(field=field or name, filter_func=filter_func,
                                                      filter_type=filter_type)
        return self

    def add_dummy_include(self, field_name):
        self._dummy_includes.append(field_name)
        return self

    def add_relation(self, field: str, many: bool = False, resource_name: str = None,
                     primary_key_name: str = None,
                     limit_to_on_retrieve: bool = False,
                     required: bool = False, api_version: Optional[str] = '') -> 'JsonApiModelViewBuilder':
        if limit_to_on_retrieve not in self._relations:
            self._relations[limit_to_on_retrieve] = []
        self._relations[limit_to_on_retrieve].append(
            Relation(field=field, resource_name=resource_name or field, many=many,
                     primary_key_name=primary_key_name, required=required,
                     api_version=api_version.replace('.', '').replace('-', '')))
        return self

    def rl(self, field: str, many: bool = False, resource_name: str = None,
           primary_key_name: str = None,
           limit_to_on_retrieve: bool = False,
           required: bool = False, api_version: Optional[str] = '') -> 'JsonApiModelViewBuilder':
        return self.add_relation(field=field, many=many, resource_name=resource_name, primary_key_name=primary_key_name,
                                 limit_to_on_retrieve=limit_to_on_retrieve, required=required,
                                 api_version=api_version.replace('.', '').replace('-', ''))

    def add_generic_relation(self, field: str,
                             related: Sequence[RelatedResource],
                             many: bool = False,
                             limit_to_on_retrieve: bool = False,
                             required: bool = False) -> 'JsonApiModelViewBuilder':
        if limit_to_on_retrieve not in self._generic_relations:
            self._generic_relations[limit_to_on_retrieve] = []
        api_fixed_related = []
        for rel in related:
            rel.api_version = rel.api_version.replace('.', '').replace('-', '')
            api_fixed_related.append(rel)

        self._generic_relations[limit_to_on_retrieve].append(
            GenericRelation(field=field, related=api_fixed_related, many=many, required=required))
        return self

    def add_custom_field(self, name: str, instance_callback: Callable[[Any], Any] = None,
                         limit_to_on_retrieve: bool = False) -> 'JsonApiModelViewBuilder':
        if limit_to_on_retrieve not in self._custom_fields:
            self._custom_fields[limit_to_on_retrieve] = []
        self._custom_fields[limit_to_on_retrieve].append(CustomField(name=name, callback=instance_callback))
        return self

    def custom_fields(self, fields: Sequence[Tuple[str, Callable[[Any], Any]]] = None,
                      limit_to_on_retrieve: bool = False) -> 'JsonApiModelViewBuilder':
        if limit_to_on_retrieve not in self._custom_fields:
            self._custom_fields[limit_to_on_retrieve] = []
        for name, instance_callback in fields:
            self._custom_fields[limit_to_on_retrieve].append(CustomField(name=name, callback=instance_callback))
        return self

    def set_related_limit(self, limit: int = DEFAULT_RELATED_LIMIT) -> 'JsonApiModelViewBuilder':
        self._related_limit = limit
        return self

    def before_create(self, before_create_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._before_create_callback = before_create_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_POST)
        return self

    def after_create(self, after_create_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._after_create_callback = after_create_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_POST)
        return self

    def set_create_decorator(self,
                             create_decorator: Callable[[Callable], Callable] = None) -> 'JsonApiModelViewBuilder':
        self._create_decorator = create_decorator
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def after_get(self, after_get_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._after_get_callback = after_get_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def set_get_decorator(self, get_decorator: Callable[[Callable], Callable] = None) -> 'JsonApiModelViewBuilder':
        self._get_decorator = get_decorator
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def before_update(self, before_update_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._before_update_callback = before_update_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_PATCH)
        return self

    def after_update(self, after_update_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._after_update_callback = after_update_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_PATCH)
        return self

    def set_update_decorator(self,
                             update_decorator: Callable[[Callable], Callable] = None) -> 'JsonApiModelViewBuilder':
        self._update_decorator = update_decorator
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def before_delete(self, before_delete_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._before_delete_callback = before_delete_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_DELETE)
        return self

    def after_delete(self, after_delete_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._after_delete_callback = after_delete_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_DELETE)
        return self

    def set_delete_decorator(self,
                             delete_decorator: Callable[[Callable], Callable] = None) -> 'JsonApiModelViewBuilder':
        self._delete_decorator = delete_decorator
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def before_list(self,
                    before_list_callback: Callable[[Request, QuerySet], QuerySet] = None) -> 'JsonApiModelViewBuilder':
        self._before_list_callback = before_list_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def after_list(self, after_list_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._after_list_callback = after_list_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def set_list_decorator(self, list_decorator: Callable[[Callable], Callable] = None) -> 'JsonApiModelViewBuilder':
        self._list_decorator = list_decorator
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def before_response(self, before_raw_response: Callable[[str], str] = None) -> 'JsonApiModelViewBuilder':
        self._before_raw_response = before_raw_response
        return self

    def _get_history_urls(self) -> Sequence[partial]:
        history_builder = deepcopy(self)
        if plugins.DJANGO_SIMPLE_HISTORY in self._include_plugins:
            history_builder._include_plugins = []
        history_builder._model = apps.get_model(self._model.objects.model._meta.db_table.split('_')[0],
                                                f'Historical{self._model.__name__}')
        history_builder._queryset = self._model.history
        history_builder._resource_name = f'historical_{self._resource_name}'

        history_builder._custom_fields = []
        history_urls = history_builder.fields(['history_date', 'history_change_reason', 'history_id', 'history_type']) \
            .add_filter(name='history_date', lookups=(
            filter_lookups.EXACT, filter_lookups.IN, filter_lookups.LT, filter_lookups.LTE, filter_lookups.GT,
            filter_lookups.GTE)) \
            .add_filter(name='history_id', lookups=(filter_lookups.EXACT, filter_lookups.IN)) \
            .add_filter(name='history_change_reason', lookups=(filter_lookups.EXACT, filter_lookups.IN)) \
            .add_filter(name='history_type', lookups=(filter_lookups.EXACT, filter_lookups.IN)) \
            .get_urls(urls_prefix='history/', url_resource_name=self._resource_name, ignore_swagger=True)

        return history_urls

    def _build(self, url_resource_name: str = None, urls_prefix: str = None, ignore_serializer: bool = False,
               ignore_swagger: bool = False) -> Sequence[partial]:
        if url_resource_name is None:
            url_resource_name = ''

        if urls_prefix is None:
            urls_prefix = 'admin/' if self._is_admin else ''

        method_to_serializer = {}
        if not ignore_serializer:
            for limit_to_on_retrieve in [False, True]:
                fields = self._fields[limit_to_on_retrieve] if limit_to_on_retrieve in self._fields else []
                if limit_to_on_retrieve is True:
                    fields.extend(self._fields[False] if False in self._fields else [])
                custom_fields = self._custom_fields[
                    limit_to_on_retrieve] if limit_to_on_retrieve in self._custom_fields else []
                if limit_to_on_retrieve is True:
                    custom_fields.extend(self._custom_fields[False] if False in self._custom_fields else [])
                relations = self._relations[limit_to_on_retrieve] if limit_to_on_retrieve in self._relations else []
                if limit_to_on_retrieve is True:
                    relations.extend(self._relations[False] if False in self._relations else [])
                generic_relations = self._generic_relations[
                    limit_to_on_retrieve] if limit_to_on_retrieve in self._generic_relations else []
                if limit_to_on_retrieve is True:
                    generic_relations.extend(self._generic_relations[False] if False in self._generic_relations else [])

                method_to_serializer[limit_to_on_retrieve] = \
                    _construct_serializer('Retrieve' if limit_to_on_retrieve else 'List',
                                          self._api_version,
                                          self._model,
                                          self._resource_name,
                                          fields,
                                          custom_fields,
                                          relations,
                                          generic_relations,
                                          self._related_limit,
                                          self._primary_key_name,
                                          self._before_update_callback if limit_to_on_retrieve else self._before_create_callback,
                                          self._after_list_callback,
                                          self._is_admin,
                                          self._dummy_includes)
                _append_to_namespace(method_to_serializer[limit_to_on_retrieve])
        else:

            method_to_serializer[False] = list(
                filter(lambda serializer: serializer.__class__.__name__.startswith('List'),
                       _MODEL_TO_SERIALIZERS[self._model]))
            method_to_serializer[True] = list(
                filter(lambda serializer: serializer.__class__.__name__.startswith('Retrieve'),
                       _MODEL_TO_SERIALIZERS[self._model]))

        filter_set, filter_backend = _construct_filter_backend(self._model, self._resource_name, self._filters,
                                                               self._computed_filters)

        @exception_handler
        @self._create_decorator
        def perform_create(view, serializer):
            instance = serializer.save()
            if self._after_create_callback is not None:
                self._after_create_callback(view.request, instance, serializer)

        @exception_handler
        @self._delete_decorator
        def perform_destroy(view, instance):
            if self._before_delete_callback is not None:
                self._before_delete_callback(instance, view.get_serializer())
            instance.delete()
            if self._after_delete_callback is not None:
                self._after_delete_callback(instance, view.get_serializer())

        def _retrieve(view, instance):
            instance = view.get_object()
            serializer = view.get_serializer(instance)
            return Response(serializer.data)

        @exception_handler
        @self._get_decorator
        def perform_get(view, instance, *args, **kwargs):
            response = _retrieve(view, instance)
            if self._after_get_callback is not None:
                response.data = self._after_get_callback(view.request, response.data)
            return response

        @exception_handler
        @self._update_decorator
        def perform_update(view, serializer):
            instance = serializer.save()
            if self._after_update_callback is not None:
                self._after_update_callback(view.request, instance, serializer)

        @exception_handler
        @self._list_decorator
        def perform_list(view, request, *args, **kwargs):
            queryset = view.filter_queryset(view.get_queryset())

            if self._before_list_callback is not None:
                queryset = self._before_list_callback(request, queryset)

            page = view.paginate_queryset(queryset)

            serializer = view.get_serializer(page if page is not None else queryset, many=True)
            if page is not None:
                response = view.get_paginated_response(serializer.data)
            else:
                response = Response(serializer.data)

            if self._after_list_callback is not None:
                response.data = self._after_list_callback(view.request, response.data)

            return response

        class Renderer(JSONRenderer):
            def render(inner_self, data, accepted_media_type=None, renderer_context=None):
                response = super(Renderer, inner_self).render(
                    data, accepted_media_type, renderer_context
                )

                try:
                    res = ujson.loads(response)
                    if res.get('data', {}).get('included'):
                        res['included'] = res['data']['included']
                        # del res['data']['included']
                    response = ujson.dumps(res)
                except Exception as exc:
                    pass

                if self._before_raw_response:
                    response = self._before_raw_response(response, renderer_context)
                if not isinstance(response, (bytes, bytearray)):
                    return str.encode(response)
                return response

        custom_paginator = type(f'PaginatorOf{self._page_size or DEFAULT_PAGE_SIZE}Pages',
                                (LimitedJsonApiPageNumberPagination,), {
                                    'page_size': self._page_size or DEFAULT_PAGE_SIZE
                                })

        base_model_view_set = type(f'{self._resource_name}JSONApiModelViewSet{self._api_version}', (ModelViewSet,), {
            'renderer_classes': (Renderer,),
            'parser_classes': (JSONParser, FormParser, MultiPartParser),
            'metadata_class': JSONAPIMetadata,
            'pagination_class': custom_paginator,
            'filter_backends': (
                QueryParameterValidationFilter, OrderingFilter, filter_backend, JsonApiSearchFilter),
            'resource_name': self._resource_name,
        })

        if ignore_swagger:
            base_model_view_set.swagger_schema = None

        def get_queryset(view):
            if self._queryset is None:
                queryset = super(view.__class__, view).get_queryset()
            else:
                queryset = self._queryset
            request = view.request
            if self._spice_queryset is not None:
                return self._spice_queryset(request, queryset)
            return queryset

        if self._spice_queryset is not None:
            _RESOURCE_NAME_TO_SPICE[self._resource_name] = self._spice_queryset

        urls = []
        for pk_name in ['pk', self._primary_key_name]:
            relationship_view = type(f'{self._resource_name}RelationshipsView{self._api_version}', (RelationshipView,),
                                     {
                                         'http_method_names': list(map(lambda method: method.lower(),
                                                                       filter(lambda method: method in [
                                                                           json_api_spec_http_methods.HTTP_GET,
                                                                           json_api_spec_http_methods.HTTP_PATCH,
                                                                           json_api_spec_http_methods.HTTP_DELETE],
                                                                              self._allowed_methods))) + ['head',
                                                                                                          'options'],
                                         'get_queryset': get_queryset,
                                         'lookup_field': pk_name
                                     })

            if ignore_swagger:
                relationship_view.swagger_schema = None

            list_method_view_set = type(f'List{self._resource_name}ViewSet{self._api_version}', (base_model_view_set,),
                                        {
                                            'get_queryset': get_queryset,
                                            'serializer_class': method_to_serializer[False],
                                            'http_method_names': list(map(lambda method: method.lower(),
                                                                          filter(lambda method: method in [
                                                                              json_api_spec_http_methods.HTTP_GET,
                                                                              json_api_spec_http_methods.HTTP_POST],
                                                                                 self._allowed_methods))) + ['head',
                                                                                                             'options'],
                                            'permission_classes': self._permission_classes,
                                            'authentication_classes': self._authentication_classes,
                                            'filterset_class': filter_set,
                                            'lookup_field': pk_name,
                                            'perform_create': perform_create,
                                            'name': f'list {self._resource_name}',
                                            'list': perform_list
                                        })

            get_method_view_set = type(f'Get{self._resource_name}ViewSet{self._api_version}', (base_model_view_set,), {
                'get_queryset': get_queryset,
                'serializer_class': method_to_serializer[True],
                'http_method_names': list(map(lambda method: method.lower(),
                                              filter(lambda method: method in [json_api_spec_http_methods.HTTP_GET,
                                                                               json_api_spec_http_methods.HTTP_PATCH,
                                                                               json_api_spec_http_methods.HTTP_DELETE],
                                                     self._allowed_methods))) + ['head', 'options'],
                'permission_classes': self._permission_classes,
                'authentication_classes': self._authentication_classes,
                'filterset_class': filter_set,
                'lookup_field': pk_name,
                'perform_update': perform_update,
                'perform_destroy': perform_destroy,
                'retrieve': perform_get
            })

            if len(urls_prefix) > 0:
                if len(self._url_api_version) > 1 and self._url_api_version not in urls_prefix:
                    urls_prefix = f'{urls_prefix.rstrip("/")}/{self._url_api_version}'
            elif len(self._url_api_version) > 1:
                urls_prefix = self._url_api_version

            if len(urls_prefix) > 0 and urls_prefix[-1] != '/':
                urls_prefix = f'{urls_prefix}/'

            if len(url_resource_name) == 0:
                url_resource_name = self._resource_name

            urls.extend([
                url(rf'^{urls_prefix}{url_resource_name}$',
                    list_method_view_set.as_view(get_dict_by_methods('list', self._allowed_methods),
                                                 name=f'list_{self._resource_name}'),
                    name=f'list-{"admin_view_" if self._is_admin else ""}{self._resource_name}{self._api_version}'),
                url(rf'^{urls_prefix}{url_resource_name}/(?P<{pk_name}>[^/.]+)/$',
                    get_method_view_set.as_view(get_dict_by_methods('get', self._allowed_methods),
                                                name=f'get_{self._resource_name}'),
                    name=f'{"admin_view_" if self._is_admin else ""}{self._resource_name}{self._api_version}-detail'),
                url(rf'^{urls_prefix}{url_resource_name}/(?P<{pk_name}>[^/.]+)/relationships/(?P<related_field>[^/.]+)$',
                    view=relationship_view.as_view(),
                    name=f'{"admin_view_" if self._is_admin else ""}{self._resource_name}{self._api_version}-relationships'),
            ])

            if self._expose_related_views:
                relation_view_dict = get_dict_by_methods('relation', self._allowed_methods)
                if relation_view_dict:
                    urls.extend([
                        url(rf'^{urls_prefix}{url_resource_name}/(?P<{pk_name}>[^/.]+)/(?P<related_field>\w+)/$',
                            list_method_view_set.as_view(relation_view_dict, name=f'related_{self._resource_name}'),
                            name=f'related-{"admin_view_" if self._is_admin else ""}{self._resource_name}{self._api_version}'),
                    ])

        if plugins.DJANGO_SIMPLE_HISTORY in self._include_plugins:
            try:
                import simple_history

                urls.extend(self._get_history_urls())
            except Exception as e:
                pass

        return urls

    def get_urls(self,
                 url_resource_name: str = None,
                 urls_prefix: str = None,
                 ignore_swagger: bool = False) -> Sequence[partial]:
        return self._build(url_resource_name=url_resource_name, urls_prefix=urls_prefix, ignore_swagger=ignore_swagger)


class JsonApiResourceViewBuilder:
    def __init__(self,
                 action_name: str = None,
                 api_version: Optional[str] = '',
                 unique_identifier: Optional[str] = 'id',
                 allowed_methods=json_api_spec_http_methods.HTTP_ACTIONS,
                 permission_classes: Sequence[Type[BasePermission]] = None,
                 authentication_classes: Sequence[Type[BaseAuthentication]] = None,
                 raw_items=False,
                 is_admin: Optional[bool] = False,
                 always_include: Optional[bool] = False,
                 only_callbacks: Optional[bool] = False,
                 create_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                 update_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                 get_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                 list_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                 delete_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                 page_size: int = DEFAULT_PAGE_SIZE):
        self._allowed_methods = [*allowed_methods]
        self._resource_name = action_name
        self._raw_items = not raw_items
        self._api_version = api_version.replace('.', '').replace('-', '')
        self._url_api_version = f'v{api_version}'
        self._unique_identifier = unique_identifier
        self._permission_classes = permission_classes or []
        self._authentication_classes = authentication_classes or []
        self._on_create_callback = None
        self._on_update_callback = None
        self._on_delete_callback = None
        self._on_list_callback = None
        self._on_get_callback = None
        self._before_raw_response = None
        self._create_decorator = create_decorator
        self._update_decorator = update_decorator
        self._get_decorator = get_decorator
        self._list_decorator = list_decorator
        self._delete_decorator = delete_decorator
        self._always_include = always_include
        self._is_admin = is_admin \
                         or (len(self._permission_classes) > 0 and any(
            getattr(pc, 'admin', False) for pc in self._permission_classes))
        self._page_size = page_size
        self._only_callbacks = only_callbacks
        self.settings = JsonApiGlobalSettings()

    @property
    def is_admin(self) -> bool:
        return self._is_admin

    @property
    def always_include(self) -> bool:
        return self._always_include

    def __warn_if_method_not_available(self, method: str):
        if method not in self._allowed_methods:
            LOGGER.warning(
                f'You\'ve set a lifecycle callback for resource {self._resource_name}, '
                f'which doesn\'t allow it\'s respective HTTP method through `allowed_methods`.')

    def on_create(self,
                  create_callback: Callable[[Request], Tuple[Dict, str, int]] = None) -> 'JsonApiResourceViewBuilder':
        self._on_create_callback = create_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_POST)
        return self

    def on_update(self, update_callback: Callable[[Request], Tuple[Dict, int]] = None) -> 'JsonApiResourceViewBuilder':
        self._on_update_callback = update_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_PATCH)
        return self

    def on_delete(self, delete_callback: Callable[[Request], Tuple[int]] = None) -> 'JsonApiResourceViewBuilder':
        self._on_delete_callback = delete_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_DELETE)
        return self

    def on_list(self,
                list_callback: Callable[[Request], Tuple[List, List, int, int]] = None) -> 'JsonApiResourceViewBuilder':
        self._on_list_callback = list_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def on_get(self, get_callback: Callable[[Request], Tuple[Dict, List, int]] = None) -> 'JsonApiResourceViewBuilder':
        self._on_get_callback = get_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def before_response(self, before_raw_response: Callable[[str], str] = None) -> 'JsonApiResourceViewBuilder':
        self._before_raw_response = before_raw_response
        return self

    def _build(self,
               url_resource_name: Optional[str] = None,
               urls_prefix: Optional[str] = None,
               urls_suffix: Optional[str] = None,
               ignore_swagger: Optional[bool] = False) -> Sequence[partial]:
        if url_resource_name is None:
            url_resource_name = ''

        if urls_prefix is None:
            urls_prefix = 'admin/' if self._is_admin else ''

        if urls_suffix is None:
            urls_suffix = ''

        @exception_handler
        @self._delete_decorator
        def destroy(view, request, *args, **kwargs):
            identifier = kwargs.get(self._unique_identifier, None)

            if self._on_delete_callback is not None:
                status = self._on_delete_callback(request, identifier, *args, **kwargs)
                return Response(data={}, status=status)

        @exception_handler
        @self._update_decorator
        def update(view, request, *args, **kwargs):
            data = json.loads(request.body).get('data', {})
            identifier = kwargs.get(self._unique_identifier, None)

            if self._on_update_callback is not None:
                data, status = self._on_update_callback(request, identifier, data, *args, **kwargs)
                if self._raw_items:
                    return Response(data={'id': identifier, 'type': self._resource_name, 'attributes': data},
                                    status=status)
                return Response(data={"data": data}, status=status)

        @exception_handler
        @self._create_decorator
        def create(view, request, *args, **kwargs):
            data = json.loads(request.body).get('data', {}) \
                if 'multipart' not in request.content_type else request.body
            if self._on_create_callback is not None:
                data, identifier, status = self._on_create_callback(request, data, *args, **kwargs)
                if self._raw_items:
                    return Response(data={'id': identifier, 'type': self._resource_name, 'attributes': data},
                                    status=status)
                return Response(data={"data": data}, status=status)

        @exception_handler
        @self._list_decorator
        def _list(view, request, *args, **kwargs):
            params = request.query_params
            filters = []
            for key, value in params.items():
                match = FILTER_REGEX.match(key)
                try:
                    value = ast.literal_eval(value)
                except:
                    pass
                if match:
                    op = FILTER_MAP.get(match.groupdict()['op'], '==')
                    multiple_values = op in ['in', 'not_in', 'any', 'not_any']
                    filters.append({'field': match.groupdict()['field'],
                                    'op': op,
                                    'value': value.split(',') if multiple_values else value})
            page = int(params.get('page_number', 1))
            include = params.get('include', '')
            includes = include.split(',') if include else []
            if self._on_list_callback is not None:
                data, included, count, status = self._on_list_callback(request, page, filters, includes, *args,
                                                                       **kwargs)
                pages = math.ceil(count / self._page_size)
                return Response(data={'links': {
                    "first": f"/api/{self._resource_name}?page_number=1",
                    "last": f"/api/{self._resource_name}?page_number={pages}",
                    "next": None if page == pages or pages <= 1 else f"/api/{self._resource_name}?page_number={page + 1}",
                    "previous": None if page <= 1 else f"/api/{self._resource_name}?page_number={page - 1}",
                }, 'data': [
                    {'id': item.get(self._unique_identifier, None), 'type': self._resource_name,
                     'attributes': item} if self._raw_items else item for
                    item in data],
                    'included': included if included else [],
                    'meta': {
                        'pagination': {
                            "page": page,
                            "pages": pages,
                            "count": count
                        }
                    }
                }, status=status)

        @exception_handler
        @self._get_decorator
        def get(view, request, *args, **kwargs):
            identifier = kwargs.get(self._unique_identifier, None)

            if self._on_get_callback is not None:
                params = request.query_params
                include = params.get('include', '')
                includes = include.split(',') if include else []

                data, included, status = self._on_get_callback(request, identifier, includes, *args, **kwargs)
                if self._raw_items:
                    return Response(data={'id': identifier, 'type': self._resource_name, 'attributes': data},
                                    status=status)
                data.update({'included': included})
                return Response(data=data, status=status)

        class Renderer(JSONRenderer):
            def render(inner_self, data, accepted_media_type=None, renderer_context=None):
                response = super(Renderer, inner_self).render(
                    data, accepted_media_type, renderer_context
                )

                try:
                    res = ujson.loads(response)
                    if res.get('data', {}).get('included'):
                        res['included'] = res['data']['included']
                        # del res['data']['included']
                    response = ujson.dumps(res)
                except Exception as exc:
                    pass

                if self._before_raw_response:
                    response = self._before_raw_response(response, renderer_context)
                if not isinstance(response, (bytes, bytearray)):
                    return str.encode(response)
                return response

        custom_paginator = type(f'PaginatorOf{self._page_size or DEFAULT_PAGE_SIZE}Pages',
                                (LimitedJsonApiPageNumberPagination,), {
                                    'page_size': self._page_size or DEFAULT_PAGE_SIZE
                                })

        patch_view_set = None
        if any([self._on_update_callback, self._on_delete_callback, self._on_get_callback]) or not self._only_callbacks:
            patch_view_set = type(f'{self._resource_name}ChangeJSONApiActionViewSet{self._api_version}', (ViewSet,), {
                'renderer_classes': (Renderer,),
                'parser_classes': (JSONParser, FormParser, MultiPartParser),
                'metadata_class': JSONAPIMetadata,
                'pagination_class': custom_paginator,
                'filter_backends': (
                    QueryParameterValidationFilter, OrderingFilter, JsonApiSearchFilter),
                'resource_name': self._resource_name,
                'http_method_names': list(map(lambda method: method.lower(), self._allowed_methods)) + ['head',
                                                                                                        'options'],
                'permission_classes': self._permission_classes,
                'authentication_classes': self._authentication_classes,
                'update': update if self._on_update_callback else None,
                'destroy': destroy if self._on_delete_callback else None,
                'get': get if self._on_get_callback else None
            })
            if ignore_swagger:
                patch_view_set.swagger_schema = None

        get_view_set = None
        if any([self._on_list_callback, self._on_create_callback]) or not self._only_callbacks:
            get_view_set = type(f'{self._resource_name}RetrieveJSONApiActionViewSet{self._api_version}', (ViewSet,), {
                'renderer_classes': (Renderer,),
                'parser_classes': (JSONParser, FormParser, MultiPartParser),
                'metadata_class': JSONAPIMetadata,
                'pagination_class': custom_paginator,
                'filter_backends': (
                    QueryParameterValidationFilter, OrderingFilter, JsonApiSearchFilter),
                'resource_name': None,
                'http_method_names': list(map(lambda method: method.lower(), self._allowed_methods)) + ['head',
                                                                                                        'options'],
                'permission_classes': self._permission_classes,
                'authentication_classes': self._authentication_classes,
                'list': _list if self._on_list_callback else None,
                'create': create if self._on_create_callback else None,
            })
            if ignore_swagger:
                get_view_set.swagger_schema = None

        urls = []

        if len(urls_prefix) > 0:
            if len(self._url_api_version) > 1 and self._url_api_version not in urls_prefix:
                urls_prefix = f'{urls_prefix.rstrip("/")}/{self._url_api_version}'
        elif len(self._url_api_version) > 1:
            urls_prefix = self._url_api_version

        if len(urls_prefix) > 0 and urls_prefix[-1] != '/':
            urls_prefix = f'{urls_prefix}/'

        if len(url_resource_name) == 0:
            url_resource_name = self._resource_name

        if get_view_set is not None:
            urls.extend([
                url(rf'^{urls_prefix}{url_resource_name}{urls_suffix}$',
                    get_view_set.as_view(get_dict_by_methods('list', self._allowed_methods),
                                         name=f'list_{self._resource_name}'),
                    name=f'list-{"admin_view_" if self._is_admin else ""}{self._resource_name}{self._api_version}')
            ])
        if patch_view_set is not None:
            view_dict = get_dict_by_methods('get', self._allowed_methods)
            if 'get' in view_dict:
                view_dict['get'] = 'get'
            urls.extend([
                url(rf'^{urls_prefix}{url_resource_name}{urls_suffix}/(?P<{self._unique_identifier}>[^/.]+)/$',
                    patch_view_set.as_view(view_dict,
                                           name=f'get_{self._resource_name}'),
                    name=f'{"admin_view_" if self._is_admin else ""}{self._resource_name}{self._api_version}-detail')
            ])
        return urls

    def get_urls(self,
                 url_resource_name: Optional[str] = None,
                 urls_prefix: Optional[str] = None,
                 urls_suffix: Optional[str] = None,
                 ignore_swagger: Optional[bool] = False) -> Sequence[partial]:
        return self._build(url_resource_name=url_resource_name, urls_prefix=urls_prefix, urls_suffix=urls_suffix,
                           ignore_swagger=ignore_swagger)


def json_api_view(resource_name: str,
                  api_version: Optional[str] = '',
                  method: Optional[str] = json_api_spec_http_methods.HTTP_GET,
                  permission_classes: Optional[Sequence[Type[BasePermission]]] = None,
                  authentication_classes: Optional[Sequence[Type[BaseAuthentication]]] = None,
                  multiple_resource: Optional[bool] = True,
                  raw_items: Optional[bool] = False,
                  page_size: Optional[int] = DEFAULT_PAGE_SIZE,
                  is_admin: Optional[bool] = False,
                  always_include: Optional[bool] = False,
                  create_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                  update_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                  get_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                  list_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                  delete_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                  *args, **kwargs) -> FunctionType:
    def decorator(func: Callable[[Request], Tuple[Dict, int]]):
        builder = JsonApiResourceViewBuilder(action_name=resource_name,
                                             api_version=api_version,
                                             allowed_methods=[method],
                                             permission_classes=permission_classes,
                                             authentication_classes=authentication_classes,
                                             raw_items=raw_items,
                                             page_size=page_size,
                                             only_callbacks=True,
                                             is_admin=is_admin,
                                             always_include=always_include,
                                             get_decorator=get_decorator,
                                             update_decorator=update_decorator,
                                             create_decorator=create_decorator,
                                             list_decorator=list_decorator,
                                             delete_decorator=delete_decorator)
        if method == json_api_spec_http_methods.HTTP_GET and multiple_resource:
            return builder.on_list(list_callback=func)
        elif method == json_api_spec_http_methods.HTTP_GET:
            return builder.on_get(get_callback=func)
        elif method == json_api_spec_http_methods.HTTP_POST:
            return builder.on_create(create_callback=func)
        elif method == json_api_spec_http_methods.HTTP_DELETE:
            return builder.on_delete(delete_callback=func)
        elif method == json_api_spec_http_methods.HTTP_PATCH:
            return builder.on_update(update_callback=func)
        else:
            raise Exception(f'Does not support method {method}')

    return decorator


def json_api_view_multi_endpoint(resource_name: str,
                                 api_version: Optional[str] = '',
                                 allowed_methods: Optional[List[str]] = json_api_spec_http_methods.HTTP_ACTIONS,
                                 permission_classes: Optional[Sequence[Type[BasePermission]]] = None,
                                 authentication_classes: Optional[Sequence[Type[BaseAuthentication]]] = None,
                                 raw_items: Optional[bool] = False,
                                 page_size: Optional[int] = 50,
                                 is_admin: Optional[bool] = False,
                                 always_include: Optional[bool] = False,
                                 create_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                                 update_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                                 get_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                                 list_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                                 delete_decorator: Optional[Callable[[Callable], Callable]] = DEFAULT_VOID_DECORATOR,
                                 *args, **kwargs) -> FunctionType:
    def decorator(func: Callable[[], Tuple[Callable, Callable, Callable, Callable, Callable]]):
        on_list, on_get, on_create, on_delete, on_update = func()
        builder = JsonApiResourceViewBuilder(action_name=resource_name,
                                             api_version=api_version,
                                             allowed_methods=allowed_methods,
                                             permission_classes=permission_classes,
                                             authentication_classes=authentication_classes,
                                             raw_items=raw_items,
                                             page_size=page_size,
                                             only_callbacks=True,
                                             is_admin=is_admin,
                                             always_include=always_include,
                                             get_decorator=get_decorator,
                                             update_decorator=update_decorator,
                                             create_decorator=create_decorator,
                                             list_decorator=list_decorator,
                                             delete_decorator=delete_decorator)
        if json_api_spec_http_methods.HTTP_GET in allowed_methods and on_list:
            builder = builder.on_list(list_callback=on_list)
        if json_api_spec_http_methods.HTTP_GET in allowed_methods and on_get:
            builder = builder.on_get(get_callback=on_get)
        if json_api_spec_http_methods.HTTP_POST in allowed_methods and on_create:
            builder = builder.on_create(create_callback=on_create)
        if json_api_spec_http_methods.HTTP_DELETE in allowed_methods and on_delete:
            builder = builder.on_delete(delete_callback=on_delete)
        if json_api_spec_http_methods.HTTP_PATCH in allowed_methods and on_update:
            builder = builder.on_update(update_callback=on_update)

        return builder

    return decorator


# Backwards Compatibility Support
JsonApiViewBuilder = JsonApiModelViewBuilder
