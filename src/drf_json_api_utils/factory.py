from functools import partial
from types import FunctionType
from typing import Type, Tuple, Sequence

from django.conf.urls import url
from django.db.models import QuerySet, Model
from rest_framework.authentication import BaseAuthentication
from rest_framework.filters import SearchFilter
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.renderers import BrowsableAPIRenderer
from rest_framework_json_api.filters import QueryParameterValidationFilter, OrderingFilter
from rest_framework_json_api.metadata import JSONAPIMetadata
from rest_framework_json_api.pagination import JsonApiPageNumberPagination
from rest_framework_json_api.parsers import JSONParser
from rest_framework_json_api.renderers import JSONRenderer
from rest_framework_json_api.views import RelationshipView, ModelViewSet

from drf_json_api_utils.constructors import _construct_serializer, _construct_filter_backend
from drf_json_api_utils.namespace import _append_to_namespace
from . import json_api_spec_http_methods
from . import lookups as filter_lookups
from .types import CustomField, Filter, Relation


class JsonApiViewBuilder:
    DEFAULT_RELATED_LIMIT = 100

    def __init__(self, model: Type[Model], primary_key_name: str = None, resource_name: str = None,
                 allowed_methods=json_api_spec_http_methods.HTTP_ALL,
                 permission_classes: Sequence[Type[BasePermission]] = None,
                 authentication_classes: Sequence[Type[BaseAuthentication]] = None,
                 queryset: QuerySet = None):
        self.__validate_http_methods(allowed_methods)
        self._model = model
        self._fields = {}
        self._filters = {}
        self._relations = {}
        self._custom_fields = {}
        self._primary_key_name = primary_key_name or 'id'
        self._allowed_methods = [*allowed_methods]
        self._resource_name = resource_name or self._model.objects.model._meta.db_table.split('_')[-1]
        self._related_limit = self.DEFAULT_RELATED_LIMIT
        self._permission_classes = permission_classes or []
        self._authentication_classes = authentication_classes or []
        self._queryset = queryset or self._model.objects

    @staticmethod
    def __validate_http_methods(limit_to_http_methods: Sequence[str] = json_api_spec_http_methods.HTTP_ALL):
        if any(map(lambda method: method not in json_api_spec_http_methods.HTTP_ALL, limit_to_http_methods)):
            raise Exception(
                f'Cannot limit fields to HTTP Method of types: '
                f'{list(filter(lambda method: method not in json_api_spec_http_methods.HTTP_ALL, limit_to_http_methods))}')

    def fields(self, fields: Sequence[str],
               limit_to_on_retrieve: bool = False) -> 'JsonApiViewBuilder':
        if limit_to_on_retrieve not in self._fields:
            self._fields[limit_to_on_retrieve] = []
        self._fields[limit_to_on_retrieve].extend(fields)
        return self

    def add_field(self, name: str, limit_to_on_retrieve: bool = False) -> 'JsonApiViewBuilder':
        if limit_to_on_retrieve not in self._fields:
            self._fields[limit_to_on_retrieve] = []
        self._fields[limit_to_on_retrieve].append(name)
        return self

    def add_filter(self, name: str, field: str = None, lookups: Sequence[str] = None,
                   transform_value: FunctionType = None) -> 'JsonApiViewBuilder':
        if lookups is None:
            lookups = (filter_lookups.EXACT,)
        if any(map(lambda lookup: lookup not in filter_lookups.ALL, lookups)):
            raise Exception(
                f'Filter lookups are invalid: '
                f'{list(filter(lambda lookup: lookup not in filter_lookups.ALL, lookups))}')
        self._filters[name] = Filter(field=field or name, lookups=lookups, transform_value=transform_value)
        return self

    def add_relation(self, field: str, many: bool = False, resource_name: str = None,
                     primary_key_name: str = None,
                     limit_to_on_retrieve: bool = False) -> 'JsonApiViewBuilder':
        if limit_to_on_retrieve not in self._relations:
            self._relations[limit_to_on_retrieve] = []
        self._relations[limit_to_on_retrieve].append(
            Relation(field=field, resource_name=resource_name or field, many=many,
                     primary_key_name=primary_key_name))
        return self

    def rl(self, field: str, many: bool = False, resource_name: str = None,
           primary_key_name: str = None,
           limit_to_on_retrieve: bool = False) -> 'JsonApiViewBuilder':
        return self.add_relation(field=field, many=many, resource_name=resource_name, primary_key_name=primary_key_name,
                                 limit_to_on_retrieve=limit_to_on_retrieve)

    def add_custom_field(self, name: str, instance_callback: FunctionType = None,
                         limit_to_on_retrieve: bool = False) -> 'JsonApiViewBuilder':
        if limit_to_on_retrieve not in self._custom_fields:
            self._custom_fields[limit_to_on_retrieve] = []
        self._custom_fields[limit_to_on_retrieve].append(CustomField(name=name, callback=instance_callback))
        return self

    def custom_fields(self, fields: Sequence[Tuple[str, FunctionType]] = None,
                      limit_to_on_retrieve: bool = False) -> 'JsonApiViewBuilder':
        if limit_to_on_retrieve not in self._custom_fields:
            self._custom_fields[limit_to_on_retrieve] = []
        for name, instance_callback in fields:
            self._custom_fields[limit_to_on_retrieve].append(CustomField(name=name, callback=instance_callback))
        return self

    def set_related_limit(self, limit: int = DEFAULT_RELATED_LIMIT) -> 'JsonApiViewBuilder':
        self._related_limit = limit
        return self

    def _build(self) -> Sequence[partial]:
        method_to_serializer = {}
        for limit_to_on_retrieve in [False, True]:
            method_to_serializer[limit_to_on_retrieve] = \
                _construct_serializer('Retrieve' if limit_to_on_retrieve else 'List', self._model, self._resource_name,
                                      self._fields[
                                          limit_to_on_retrieve] if limit_to_on_retrieve in self._fields else [],
                                      self._custom_fields[
                                          limit_to_on_retrieve] if limit_to_on_retrieve in self._custom_fields else [],
                                      self._relations[
                                          limit_to_on_retrieve] if limit_to_on_retrieve in self._relations else [],
                                      self._related_limit,
                                      self._primary_key_name)
            _append_to_namespace(method_to_serializer[limit_to_on_retrieve])

        filter_set, filter_backend = _construct_filter_backend(self._model, self._resource_name, self._filters)

        base_model_view_set = type(f'{self._resource_name}JSONApiModelViewSet', (ModelViewSet,), {
            'renderer_classes': (JSONRenderer, BrowsableAPIRenderer),
            'parser_classes': (JSONParser, FormParser, MultiPartParser),
            'metadata_class': JSONAPIMetadata,
            'pagination_class': LimitedJsonApiPageNumberPagination,
            'filter_backends': (
                QueryParameterValidationFilter, OrderingFilter, filter_backend, JsonApiSearchFilter),
            'resource_name': self._resource_name
        })

        urls = []
        for pk_name in ['pk', self._primary_key_name]:
            relationship_view = type(f'{self._resource_name}RelationshipsView', (RelationshipView,), {
                'queryset': self._queryset,
                'lookup_field': pk_name
            })

            list_method_view_set = type(f'List{self._resource_name}ViewSet', (base_model_view_set,), {
                'queryset': self._queryset,
                'serializer_class': method_to_serializer[False],
                'allowed_methods': [json_api_spec_http_methods.HTTP_GET],
                'permission_classes': self._permission_classes,
                'authentication_classes': self._authentication_classes,
                'filterset_class': filter_set,
                'lookup_field': pk_name
            })

            get_method_view_set = type(f'Get{self._resource_name}ViewSet', (base_model_view_set,), {
                'queryset': self._queryset,
                'serializer_class': method_to_serializer[True],
                'allowed_methods': self._allowed_methods,
                'permission_classes': self._permission_classes,
                'authentication_classes': self._authentication_classes,
                'filterset_class': filter_set,
                'lookup_field': pk_name
            })

            urls.extend([
                url(rf'^{self._resource_name}$', list_method_view_set.as_view({'get': 'list'}),
                    name=f'list-{self._resource_name}'),
                url(rf'^{self._resource_name}/(?P<{pk_name}>[^/.]+)/$',
                    get_method_view_set.as_view({'get': 'retrieve'}),
                    name=f'{self._resource_name}-detail'),
                url(rf'^{self._resource_name}/(?P<{pk_name}>[^/.]+)/(?P<related_field>\w+)/$',
                    list_method_view_set.as_view({'get': 'retrieve_related'}),
                    name=f'related-{self._resource_name}'),
                url(rf'^{self._resource_name}/(?P<{pk_name}>[^/.]+)/relationships/(?P<related_field>[^/.]+)$',
                    view=relationship_view.as_view(), name=f'{self._resource_name}-relationships'),
            ])
        return urls

    def get_urls(self) -> Sequence[partial]:
        return self._build()


class LimitedJsonApiPageNumberPagination(JsonApiPageNumberPagination):
    page_size = 50


class JsonApiSearchFilter(SearchFilter):
    search_param = 'filter[search]'