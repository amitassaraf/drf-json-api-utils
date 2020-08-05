import json
import math
from copy import deepcopy
from functools import partial
from types import FunctionType
from typing import Type, Tuple, Sequence, Dict, Callable, Any, Optional, List

from django.apps import apps
from django.conf.urls import url
from django.db.models import QuerySet, Model
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.renderers import BrowsableAPIRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet
from rest_framework_json_api.filters import QueryParameterValidationFilter, OrderingFilter
from rest_framework_json_api.metadata import JSONAPIMetadata
from rest_framework_json_api.parsers import JSONParser
from rest_framework_json_api.renderers import JSONRenderer
from rest_framework_json_api.views import RelationshipView, ModelViewSet

from . import json_api_spec_http_methods
from . import lookups as filter_lookups
from . import plugins
from .common import LimitedJsonApiPageNumberPagination, JsonApiSearchFilter, LOGGER
from .constructors import _construct_serializer, _construct_filter_backend
from .namespace import _append_to_namespace, _RESOURCE_NAME_TO_SPICE, _MODEL_TO_SERIALIZERS
from .types import CustomField, Filter, Relation, GenericRelation, ComputedFilter


class JsonApiModelViewBuilder:
    DEFAULT_RELATED_LIMIT = 100

    def __init__(self, model: Type[Model], primary_key_name: Optional[str] = None, resource_name: Optional[str] = None,
                 allowed_methods=json_api_spec_http_methods.HTTP_ALL,
                 permission_classes: Optional[Sequence[Type[BasePermission]]] = None,
                 authentication_classes: Optional[Sequence[Type[BaseAuthentication]]] = None,
                 queryset: Optional[QuerySet] = None,
                 permitted_objects: Optional[Callable[[Request, QuerySet], QuerySet]] = None,
                 skip_plugins: Optional[Sequence[str]] = None,
                 include_plugins: Optional[Sequence[str]] = None,
                 plugin_options: Optional[Dict[str, Any]] = None):
        self.__validate_http_methods(allowed_methods)
        self._model = model
        self._fields = {}
        self._filters = {}
        self._computed_filters = {}
        self._relations = {}
        self._generic_relations = {}
        self._custom_fields = {}
        self._primary_key_name = primary_key_name or 'id'
        self._allowed_methods = [*allowed_methods]
        self._resource_name = resource_name or self._model.objects.model._meta.db_table.split('_')[-1]
        self._related_limit = self.DEFAULT_RELATED_LIMIT
        self._permission_classes = permission_classes or []
        self._authentication_classes = authentication_classes or []
        self._before_create_callback = None
        self._after_create_callback = None
        self._after_get_callback = None
        self._before_update_callback = None
        self._after_update_callback = None
        self._before_delete_callback = None
        self._after_delete_callback = None
        self._before_list_callback = None
        self._after_list_callback = None
        if queryset is None:
            self._queryset = self._model.objects
        else:
            self._queryset = queryset
        self._spice_queryset = permitted_objects
        self._skip_plugins = skip_plugins if skip_plugins is not None else [plugins.AUTO_ADMIN_VIEWS]
        include_plugins = include_plugins or []
        for item in include_plugins:
            if item in self._skip_plugins:
                self._skip_plugins.remove(item)
        self._plugin_options = plugin_options or {}

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
                            filter_func: Callable[[QuerySet], str],
                            field: str = None) -> 'JsonApiModelViewBuilder':
        self._computed_filters[name] = ComputedFilter(field=field or name, filter_func=filter_func,
                                                      filter_type=filter_type)
        return self

    def add_relation(self, field: str, many: bool = False, resource_name: str = None,
                     primary_key_name: str = None,
                     limit_to_on_retrieve: bool = False,
                     required: bool = False) -> 'JsonApiModelViewBuilder':
        if limit_to_on_retrieve not in self._relations:
            self._relations[limit_to_on_retrieve] = []
        self._relations[limit_to_on_retrieve].append(
            Relation(field=field, resource_name=resource_name or field, many=many,
                     primary_key_name=primary_key_name, required=required))
        return self

    def rl(self, field: str, many: bool = False, resource_name: str = None,
           primary_key_name: str = None,
           limit_to_on_retrieve: bool = False,
           required: bool = False) -> 'JsonApiModelViewBuilder':
        return self.add_relation(field=field, many=many, resource_name=resource_name, primary_key_name=primary_key_name,
                                 limit_to_on_retrieve=limit_to_on_retrieve, required=required)

    def add_generic_relation(self, field: str,
                             related: Dict[Type[Model], str],
                             many: bool = False,
                             limit_to_on_retrieve: bool = False,
                             required: bool = False) -> 'JsonApiModelViewBuilder':
        if limit_to_on_retrieve not in self._generic_relations:
            self._generic_relations[limit_to_on_retrieve] = []
        self._generic_relations[limit_to_on_retrieve].append(
            GenericRelation(field=field, related=related, many=many, required=required))
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

    def after_get(self, after_get_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._after_get_callback = after_get_callback
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

    def before_delete(self, before_delete_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._before_delete_callback = before_delete_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_DELETE)
        return self

    def after_delete(self, after_delete_callback: Callable[[Any], Any] = None) -> 'JsonApiModelViewBuilder':
        self._after_delete_callback = after_delete_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_DELETE)
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

    def _get_history_urls(self) -> Sequence[partial]:
        history_builder = deepcopy(self)
        history_builder._skip_plugins = [plugins.DJANGO_SIMPLE_HISTORY, plugins.AUTO_ADMIN_VIEWS]
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
            .get_urls(urls_prefix='history/', url_resource_name=self._resource_name)

        return history_urls

    def _get_admin_urls(self) -> Sequence[partial]:
        admin_builder = deepcopy(self)
        admin_builder._skip_plugins = [plugins.AUTO_ADMIN_VIEWS]
        admin_builder._spice_queryset = None
        admin_builder._resource_name = f'admin_view_{admin_builder._resource_name}'
        admin_permission_class = admin_builder._plugin_options.get(plugins.AUTO_ADMIN_VIEWS, {}).get(
            'ADMIN_PERMISSION_CLASS')

        if admin_permission_class is not None:
            admin_builder._permission_classes = [*admin_builder._permission_classes, admin_permission_class]

        admin_urls = admin_builder._build(url_resource_name=self._resource_name, urls_prefix='admin/',
                                          ignore_serializer=True)

        return admin_urls

    def _build(self, url_resource_name: str = '', urls_prefix: str = '', ignore_serializer: bool = False) -> Sequence[
        partial]:
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
                    _construct_serializer('Retrieve' if limit_to_on_retrieve else 'List', self._model,
                                          self._resource_name,
                                          fields,
                                          custom_fields,
                                          relations,
                                          generic_relations,
                                          self._related_limit,
                                          self._primary_key_name,
                                          self._before_update_callback if limit_to_on_retrieve else self._before_create_callback,
                                          self._after_list_callback)
                _append_to_namespace(method_to_serializer[limit_to_on_retrieve])
        else:
            method_to_serializer[False] = _MODEL_TO_SERIALIZERS[self._model][0]
            method_to_serializer[True] = _MODEL_TO_SERIALIZERS[self._model][1]

        filter_set, filter_backend = _construct_filter_backend(self._model, self._resource_name, self._filters,
                                                               self._computed_filters)

        def perform_create(view, serializer):
            instance = serializer.save()
            if self._after_create_callback is not None:
                self._after_create_callback(view.request, instance, serializer)

        def perform_destroy(view, instance):
            if self._before_delete_callback is not None:
                self._before_delete_callback(instance, view.get_serializer())
            instance.delete()
            if self._after_delete_callback is not None:
                self._after_delete_callback(instance, view.get_serializer())

        def perform_get(view, instance, *args, **kwargs):
            response = super(view.__class__, view).retrieve(instance, *args, **kwargs)
            if self._after_get_callback is not None:
                response.data = self._after_get_callback(response.data)
            return response

        def perform_update(view, serializer):
            instance = serializer.save()
            if self._after_update_callback is not None:
                self._after_update_callback(view.request, instance, serializer)

        def perform_list(view, request, *args, **kwargs):
            queryset = view.filter_queryset(view.get_queryset())

            if self._before_list_callback is not None:
                queryset = self._before_list_callback(request, queryset)

            page = view.paginate_queryset(queryset)
            if page is not None:
                serializer = view.get_serializer(page, many=True)
                response = view.get_paginated_response(serializer.data)
            else:
                serializer = view.get_serializer(queryset, many=True)
                response = Response(serializer.data)

            if self._after_list_callback is not None:
                response.data = self._after_list_callback(response.data)
            return response

        base_model_view_set = type(f'{self._resource_name}JSONApiModelViewSet', (ModelViewSet,), {
            'renderer_classes': (JSONRenderer, BrowsableAPIRenderer),
            'parser_classes': (JSONParser, FormParser, MultiPartParser),
            'metadata_class': JSONAPIMetadata,
            'pagination_class': LimitedJsonApiPageNumberPagination,
            'filter_backends': (
                QueryParameterValidationFilter, OrderingFilter, filter_backend, JsonApiSearchFilter),
            'resource_name': self._resource_name,
        })

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
            relationship_view = type(f'{self._resource_name}RelationshipsView', (RelationshipView,), {
                'get_queryset': get_queryset,
                'lookup_field': pk_name
            })

            list_method_view_set = type(f'List{self._resource_name}ViewSet', (base_model_view_set,), {
                'get_queryset': get_queryset,
                'serializer_class': method_to_serializer[False],
                'http_method_names': list(map(lambda method: method.lower(),
                                              filter(lambda method: method in [json_api_spec_http_methods.HTTP_GET,
                                                                               json_api_spec_http_methods.HTTP_POST],
                                                     self._allowed_methods))) + ['head', 'options'],
                'permission_classes': self._permission_classes,
                'authentication_classes': self._authentication_classes,
                'filterset_class': filter_set,
                'lookup_field': pk_name,
                'perform_create': perform_create,
                'name': f'list {self._resource_name}',
                'list': perform_list
            })

            get_method_view_set = type(f'Get{self._resource_name}ViewSet', (base_model_view_set,), {
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

            if len(urls_prefix) > 0 and urls_prefix[-1] != '/':
                urls_prefix = f'{urls_prefix}/'

            if len(url_resource_name) == 0:
                url_resource_name = self._resource_name

            urls.extend([
                url(rf'^{urls_prefix}{url_resource_name}$',
                    list_method_view_set.as_view({'get': 'list', 'post': 'create'}, name=f'list_{self._resource_name}'),
                    name=f'list-{self._resource_name}'),
                url(rf'^{urls_prefix}{url_resource_name}/(?P<{pk_name}>[^/.]+)/$',
                    get_method_view_set.as_view({'get': 'retrieve', 'patch': 'update', 'delete': 'destroy'},
                                                name=f'get_{self._resource_name}'),
                    name=f'{self._resource_name}-detail'),
                url(rf'^{urls_prefix}{url_resource_name}/(?P<{pk_name}>[^/.]+)/(?P<related_field>\w+)/$',
                    list_method_view_set.as_view({'get': 'retrieve_related'}, name=f'related_{self._resource_name}'),
                    name=f'related-{self._resource_name}'),
                url(
                    rf'^{urls_prefix}{url_resource_name}/(?P<{pk_name}>[^/.]+)/relationships/(?P<related_field>[^/.]+)$',
                    view=relationship_view.as_view(),
                    name=f'{self._resource_name}-relationships'),
            ])

        if plugins.DJANGO_SIMPLE_HISTORY not in self._skip_plugins:
            try:
                import simple_history

                urls.extend(self._get_history_urls())
            except Exception as e:
                pass

        if plugins.AUTO_ADMIN_VIEWS not in self._skip_plugins:
            urls.extend(self._get_admin_urls())

        return urls

    def get_urls(self, url_resource_name: str = '', urls_prefix: str = '') -> Sequence[partial]:
        return self._build(url_resource_name=url_resource_name, urls_prefix=urls_prefix)


class JsonApiResourceViewBuilder:
    def __init__(self,
                 action_name: str = None,
                 unique_identifier: Optional[str] = 'id',
                 allowed_methods=json_api_spec_http_methods.HTTP_ACTIONS,
                 permission_classes: Sequence[Type[BasePermission]] = None,
                 authentication_classes: Sequence[Type[BaseAuthentication]] = None,
                 raw_items=False):
        self._allowed_methods = [*allowed_methods]
        self._resource_name = action_name
        self._raw_items = not raw_items
        self._unique_identifier = unique_identifier
        self._permission_classes = permission_classes or []
        self._authentication_classes = authentication_classes or []
        self._on_create_callback = None
        self._on_update_callback = None
        self._on_delete_callback = None
        self._on_list_callback = None
        self._on_get_callback = None

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

    def on_list(self, list_callback: Callable[[Request], Tuple[List, int, int]] = None) -> 'JsonApiResourceViewBuilder':
        self._on_list_callback = list_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def on_get(self, get_callback: Callable[[Request], Tuple[Dict, int]] = None) -> 'JsonApiResourceViewBuilder':
        self._on_get_callback = get_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def _build(self, url_resource_name: str = '', urls_prefix: str = '', urls_suffix: str = '') -> Sequence[partial]:
        def destroy(view, request, *args, **kwargs):
            identifier = kwargs.get(self._unique_identifier, None)

            if self._on_delete_callback is not None:
                status = self._on_delete_callback(request, identifier, *args, **kwargs)
                return Response(data={}, status=status)

        def update(view, request, *args, **kwargs):
            data = json.loads(request.body).get('data', {})
            identifier = kwargs.get(self._unique_identifier, None)

            if self._on_update_callback is not None:
                data, status = self._on_update_callback(request, identifier, data, *args, **kwargs)
                if self._raw_items:
                    return Response(data={'id': identifier, 'type': self._resource_name, 'attributes': data},
                                    status=status)
                return Response(data=data, status=status)

        def create(view, request, *args, **kwargs):
            data = json.loads(request.body).get('data', {})
            if self._on_create_callback is not None:
                data, identifier, status = self._on_create_callback(request, data, *args, **kwargs)
                if self._raw_items:
                    return Response(data={'id': identifier, 'type': self._resource_name, 'attributes': data},
                                    status=status)
                return Response(data=data, status=status)

        def _list(view, request, *args, **kwargs):
            params = request.query_params
            page = params.get('page_number', 1)
            if self._on_list_callback is not None:
                data, count, status = self._on_list_callback(request, page, *args, **kwargs)
                pages = math.ceil(count / 50)
                return Response(data={'links': {
                    "first": f"/api/{self._resource_name}?page_number=1",
                    "last": f"/api/{self._resource_name}?page_number={pages}",
                    "next": None if page == pages or pages <= 1 else f"/api/{self._resource_name}?page_number={page + 1}",
                    "previous": None if page <= 1 else f"/api/{self._resource_name}?page_number={page - 1}",
                }, 'data': [
                    {'id': item.get(self._unique_identifier, None), 'type': self._resource_name,
                     'attributes': item} if self._raw_items else item for
                    item in data], 'meta': {
                    'pagination': {
                        "page": page,
                        "pages": pages,
                        "count": count
                    }
                }
                }, status=status)

        def get(view, request, *args, **kwargs):
            identifier = kwargs.get(self._unique_identifier, None)

            if self._on_get_callback is not None:
                data, status = self._on_get_callback(request, identifier, *args, **kwargs)
                if self._raw_items:
                    return Response(data={'id': identifier, 'type': self._resource_name, 'attributes': data},
                                    status=status)
                return Response(data=data, status=status)

        patch_view_set = type(f'{self._resource_name}ChangeJSONApiActionViewSet', (ViewSet,), {
            'renderer_classes': (JSONRenderer, BrowsableAPIRenderer),
            'parser_classes': (JSONParser, FormParser, MultiPartParser),
            'metadata_class': JSONAPIMetadata,
            'pagination_class': LimitedJsonApiPageNumberPagination,
            'filter_backends': (
                QueryParameterValidationFilter, OrderingFilter, JsonApiSearchFilter),
            'resource_name': self._resource_name,
            'http_method_names': list(map(lambda method: method.lower(), self._allowed_methods)) + ['head', 'options'],
            'permission_classes': self._permission_classes,
            'authentication_classes': self._authentication_classes,
            'update': update,
            'destroy': destroy,
            'get': get
        })

        get_view_set = type(f'{self._resource_name}RetrieveJSONApiActionViewSet', (ViewSet,), {
            'renderer_classes': (JSONRenderer, BrowsableAPIRenderer),
            'parser_classes': (JSONParser, FormParser, MultiPartParser),
            'metadata_class': JSONAPIMetadata,
            'pagination_class': LimitedJsonApiPageNumberPagination,
            'filter_backends': (
                QueryParameterValidationFilter, OrderingFilter, JsonApiSearchFilter),
            'resource_name': None,
            'http_method_names': list(map(lambda method: method.lower(), self._allowed_methods)) + ['head', 'options'],
            'permission_classes': self._permission_classes,
            'authentication_classes': self._authentication_classes,
            'list': _list,
            'create': create,
        })

        urls = []

        if len(urls_prefix) > 0 and urls_prefix[-1] != '/':
            urls_prefix = f'{urls_prefix}/'

        if len(url_resource_name) == 0:
            url_resource_name = self._resource_name

        urls.extend([
            url(rf'^{urls_prefix}{url_resource_name}{urls_suffix}$',
                get_view_set.as_view({'get': 'list', 'post': 'create'}, name=f'list_{self._resource_name}'),
                name=f'list-{self._resource_name}'),
            url(rf'^{urls_prefix}{url_resource_name}{urls_suffix}/(?P<{self._unique_identifier}>[^/.]+)/$',
                patch_view_set.as_view({'get': 'get', 'patch': 'update', 'delete': 'destroy'},
                                       name=f'get_{self._resource_name}'),
                name=f'{self._resource_name}-detail')
        ])
        return urls

    def get_urls(self, url_resource_name: str = '', urls_prefix: str = '', urls_suffix: str = '') -> Sequence[partial]:
        return self._build(url_resource_name=url_resource_name, urls_prefix=urls_prefix, urls_suffix=urls_suffix)


def json_api_view(resource_name: str,
                  method: str = json_api_spec_http_methods.HTTP_GET,
                  permission_classes: Optional[Sequence[Type[BasePermission]]] = None,
                  authentication_classes: Optional[Sequence[Type[BaseAuthentication]]] = None,
                  urls_prefix: str = '',
                  urls_suffix: str = '',
                  multiple_resource = True) -> FunctionType:
    def decorator(func: Callable[[Request], Tuple[Dict, int]]):
        builder = JsonApiResourceViewBuilder(action_name=resource_name,
                                             allowed_methods=[method],
                                             permission_classes=permission_classes,
                                             authentication_classes=authentication_classes)
        if method == json_api_spec_http_methods.HTTP_GET and multiple_resource:
            return builder.on_list(list_callback=func) \
                .get_urls(urls_prefix=urls_prefix, urls_suffix=urls_suffix)
        elif method == json_api_spec_http_methods.HTTP_GET:
            return builder.on_get(get_callback=func) \
                .get_urls(urls_prefix=urls_prefix, urls_suffix=urls_suffix)
        elif method == json_api_spec_http_methods.HTTP_POST:
            return builder.on_create(create_callback=func) \
                .get_urls(urls_prefix=urls_prefix, urls_suffix=urls_suffix)
        elif method == json_api_spec_http_methods.HTTP_DELETE:
            return builder.on_delete(delete_callback=func) \
                .get_urls(urls_prefix=urls_prefix, urls_suffix=urls_suffix)
        elif method == json_api_spec_http_methods.HTTP_PATCH:
            return builder.on_update(update_callback=func) \
                .get_urls(urls_prefix=urls_prefix, urls_suffix=urls_suffix)
        else:
            raise Exception(f'Does not support method {method}')

    return decorator


# Backwards Compatibility Support
JsonApiViewBuilder = JsonApiModelViewBuilder
