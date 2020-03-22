from typing import List, Type, Dict, Tuple

from django.db.models import QuerySet, Model
from django_filters.filterset import BaseFilterSet
from django_filters.rest_framework import FilterSet
from django_filters.utils import get_model_field
from rest_framework.relations import ManyRelatedField, MANY_RELATION_KWARGS
from rest_framework_json_api import serializers
from rest_framework_json_api.django_filters import DjangoFilterBackend
from rest_framework_json_api.relations import ResourceRelatedField

from .types import CustomField, Relation, Filter


def _construct_serializer(serializer_prefix: str, model: Type[Model], resource_name: str, fields: List[str],
                          custom_fields: List[CustomField], relations: List[Relation], related_limit: int,
                          primary_key_name: str) -> Type:
    def to_representation(self, iterable):
        if isinstance(iterable, QuerySet):
            real_count = iterable.count()
        else:
            real_count = len(iterable)
        data = [
            self.child_relation.to_representation(value)
            for value in iterable[:related_limit]
        ]

        # Create a liar list that when we query it's length we get the real amount of items in the db but
        # when displaying it, limit to the related_limit provided.
        class LiarList(list):
            def __len__(self):
                return real_count

        return LiarList(data)

    many_related = type(f'{resource_name}ManyRelatedField', (ManyRelatedField,), {
        'to_representation': to_representation
    })

    def many_init(*args, **kwargs):
        list_kwargs = {'child_relation': resource_related_field(*args, **kwargs)}
        for key in kwargs:
            if key in MANY_RELATION_KWARGS:
                list_kwargs[key] = kwargs[key]
        return many_related(**list_kwargs)

    resource_related_field = type(f'{resource_name}ManyRelatedField', (ResourceRelatedField,), {
        'many_init': many_init
    })

    serializer_type_name = f'{serializer_prefix}{resource_name}Serializer'

    return type(serializer_type_name, (serializers.HyperlinkedModelSerializer,), {
        **{custom_field.name: serializers.SerializerMethodField(read_only=True) for custom_field in
           custom_fields},
        **{f'get_{custom_field.name}': staticmethod(custom_field.callback) for custom_field in custom_fields},
        **{relation.field: resource_related_field(
            many=relation.many,
            read_only=True,
            related_link_view_name=f'{relation.resource_name}-detail',
            related_link_lookup_field=primary_key_name,
            related_link_url_kwarg=relation.primary_key_name or 'id',
            self_link_view_name=f'{resource_name}-relationships'
        ) for relation in relations},
        'Meta': type('Meta', (), {'model': model, 'fields': [*fields, *list(
            map(lambda custom_field: custom_field.name, custom_fields))],
                                  'resource_name': resource_name}),
        'included_serializers': {
            relation.field: f'drf_json_api_utils.namespace.{serializer_type_name}'
            for relation in relations}
    })


def _construct_filter_backend(model: Type[Model], resource_name: str, filters: Dict[str, Filter]) -> Tuple[Type, Type]:
    constructed_filters_transform_callbacks = {}
    constructed_filters = {}
    for key, filter in filters.items():
        for lookup_expr in filter.lookups:
            field = get_model_field(model, filter.field)
            filter_name = BaseFilterSet.get_filter_name(key, lookup_expr)
            # If the filter is explicitly declared on the class, skip generation
            if field is not None:
                constructed_filters[filter_name] = BaseFilterSet.filter_for_field(field, filter.field, lookup_expr)

        if filter.transform_value:
            constructed_filters_transform_callbacks[key] = filter.transform_value

    filter_set = type(f'{resource_name}FilterSet', (FilterSet,), {
        **constructed_filters,
        'Meta': type('Meta', (), {
            'model': model,
            'fields': []
        })
    })

    def _get_filterset_kwargs(self, request, queryset, view):
        result = super(self.__class__, self)._get_filterset_kwargs(request, queryset, view)
        queryset = result['queryset']
        for field, value in result['data'].items():
            if field in constructed_filters_transform_callbacks:
                new_value, new_queryset = constructed_filters_transform_callbacks[field](value, queryset)
                queryset = new_queryset
                result['data'][field] = new_value

        result['queryset'] = queryset
        return result

    filter_backend = type(f'{resource_name}FilterBackend', (DjangoFilterBackend,), {
        'get_filterset_kwargs': _get_filterset_kwargs
    })

    return filter_set, filter_backend
