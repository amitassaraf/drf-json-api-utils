import copy
import json
from collections import OrderedDict
from types import FunctionType
from typing import Type, Dict, Tuple, Sequence, Callable

import inflection
from django.db.models import QuerySet, Model, ForeignKey
from django.utils.module_loading import import_string as import_class_from_dotted_path
from django_filters.filterset import BaseFilterSet
from django_filters.rest_framework import FilterSet
from django_filters.utils import get_model_field
from rest_framework.exceptions import ValidationError
from rest_framework.fields import get_attribute
from rest_framework.relations import ManyRelatedField, MANY_RELATION_KWARGS
from rest_framework.utils.serializer_helpers import ReturnDict
from rest_framework_json_api import serializers
from rest_framework_json_api.django_filters import DjangoFilterBackend
from rest_framework_json_api.relations import ResourceRelatedField
from rest_framework_json_api.utils import get_resource_type_from_serializer, get_resource_type_from_instance, \
    get_resource_type_from_queryset

from .generic_relation import GenericRelatedField
from .namespace import _RESOURCE_NAME_TO_SPICE, _MODEL_TO_SERIALIZERS, _append_to_namespace
from .types import CustomField, Relation, Filter, GenericRelation, ComputedFilter


# Create a liar list that when we query it's length we get the real amount of items in the db but
# when displaying it, limit to the related_limit provided.
class LiarList(list):

    @property
    def real_count(self):
        return self._real_count

    @real_count.setter
    def real_count(self, count):
        self._real_count = count

    def __len__(self):
        return self.real_count


def _construct_serializer(serializer_prefix: str, serializer_suffix: str,
                          model: Type[Model], resource_name: str, fields: Sequence[str],
                          custom_fields: Sequence[CustomField], relations: Sequence[Relation],
                          generic_relations: Sequence[GenericRelation], related_limit: int,
                          primary_key_name: str, on_validate: FunctionType = None,
                          after_list_callback: Callable = None, is_admin: bool = False, dummy_includes=[]) -> Type:
    serializer_name = f'{"Admin" if is_admin else ""}{serializer_prefix}{resource_name}Serializer{serializer_suffix}'
    if model in _MODEL_TO_SERIALIZERS:
        found_serializer = list(
            filter(lambda serializer: serializer.__name__ == serializer_name, _MODEL_TO_SERIALIZERS[model]))
        if found_serializer:
            return found_serializer[0]

    def to_representation(self, iterable):
        if isinstance(iterable, QuerySet):
            real_count = iterable.count()
        else:
            real_count = len(iterable)
        data = [
            self.child_relation.to_representation(value)
            for value in iterable[:related_limit]
        ]

        liar = LiarList(data)
        liar.real_count = real_count
        return liar

    def generate_relation_field(relation):
        def filter_relationship(self, instance, relationship):
            relationship_field = getattr(relationship, 'field', None)
            if isinstance(relationship_field, ForeignKey):
                relationship = relationship.model.objects.filter(**{relationship_field.name: instance})

            if relation.resource_name in _RESOURCE_NAME_TO_SPICE and not is_admin:
                return _RESOURCE_NAME_TO_SPICE[relation.resource_name](self.context['request'], relationship)
            return relationship

        def get_attribute_override(self, instance):
            # Override the default implementation of get_attribute
            # Can't have any relationships if not created
            if hasattr(instance, 'pk') and instance.pk is None:
                return []

            relationship = get_attribute(instance, self.source_attrs)
            queryset = filter_relationship(self, instance, relationship)
            return queryset.all() if (hasattr(queryset, 'all')) else queryset

        many_related = type(f'{"Admin" if is_admin else ""}{resource_name}ManyRelatedField', (ManyRelatedField,), {
            'to_representation': to_representation,
            'get_attribute': get_attribute_override
        })

        def many_init(*args, **kwargs):
            list_kwargs = {'child_relation': resource_related_field(*args, **kwargs)}
            for key in kwargs:
                if key in MANY_RELATION_KWARGS:
                    list_kwargs[key] = kwargs[key]
            return many_related(**list_kwargs)

        resource_related_field = type(f'{"Admin" if is_admin else ""}{resource_name}ManyRelatedField',
                                      (ResourceRelatedField,), {
                                          'many_init': many_init
                                      })

        return resource_related_field

    def validate_data(self, data):
        if on_validate is not None:
            try:
                return on_validate(self.context['request'], self, data)
            except Exception as e:
                raise ValidationError(detail=str(e))
        return data

    included_serializers = {}
    included_generic_serializers = {}
    for relation in relations:
        included_serializers[
            relation.field] = f'drf_json_api_utils.namespace.{"Admin" if is_admin else ""}{f"{serializer_prefix}{relation.resource_name}Serializer{relation.api_version}"}'

    for include in dummy_includes or []:
        dummy_serializer = type(f'{"Admin" if is_admin else ""}{f"{serializer_prefix}{include}Serializer"}',
                                (serializers.Serializer,), {})
        _append_to_namespace(dummy_serializer)
        included_serializers[include] = f'drf_json_api_utils.namespace.{dummy_serializer.__name__}'

    for relation in generic_relations:
        for related in getattr(relation, 'related', []):
            if relation.field not in included_generic_serializers:
                included_generic_serializers[relation.field] = []
            included_generic_serializers[relation.field].append(
                f'drf_json_api_utils.namespace.{"Admin" if is_admin else ""}{f"{serializer_prefix}{related.resource_name}Serializer{related.api_version}"}')
            included_serializers[
                relation.field] = f'drf_json_api_utils.namespace.{"Admin" if is_admin else ""}{f"{serializer_prefix}{related.resource_name}Serializer{related.api_version}"}'

    def get_generic_included_serializers(serializer):
        included_serializers = copy.copy(getattr(serializer, 'included_generic_serializers', dict()))

        for name, serializers in iter(included_serializers.items()):
            included_serializers[name] = []
            for value in serializers:
                if not isinstance(value, type):
                    if value == 'self':
                        included_serializers[name].append(
                            serializer if isinstance(serializer, type) else serializer.__class__
                        )
                    else:
                        included_serializers[name].append(import_class_from_dotted_path(value))

        return included_serializers

    def generate_generic_resource(related_model):
        class GenericResourceRelatedField(ResourceRelatedField):
            def use_pk_only_optimization(self):
                return True

            def to_internal_value(self, data):
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except ValueError:
                        # show a useful error if they send a `pk` instead of resource object
                        self.fail('incorrect_type', data_type=type(data).__name__)
                if not isinstance(data, dict):
                    self.fail('incorrect_type', data_type=type(data).__name__)

                expected_relation_type = get_resource_type_from_queryset(self.get_queryset())
                serializer_resource_type = self.get_resource_type_from_included_serializer(data)

                if serializer_resource_type is not None:
                    expected_relation_type = serializer_resource_type

                if 'type' not in data:
                    self.fail('missing_type')

                if 'id' not in data:
                    self.fail('missing_id')

                if data['type'] != expected_relation_type:
                    self.conflict(
                        'incorrect_relation_type',
                        relation_type=expected_relation_type,
                        received_type=data['type']
                    )

                return super(ResourceRelatedField, self).to_internal_value(data['id'])

            def to_representation(self, value):
                if getattr(self, 'pk_field', None) is not None:
                    pk = self.pk_field.to_representation(value.pk)
                else:
                    pk = value.pk

                resource_type = self.get_resource_type_from_included_serializer(value)
                if resource_type is None or not self._skip_polymorphic_optimization:
                    resource_type = get_resource_type_from_instance(value)

                return OrderedDict([('type', resource_type), ('id', str(pk))])

            def get_resource_type_from_included_serializer(self, value):
                """
                Check to see it this resource has a different resource_name when
                included and return that name, or None
                """
                field_name = self.field_name or self.parent.field_name
                parent = self.get_parent_serializer()

                if parent is not None:
                    # accept both singular and plural versions of field_name
                    field_names = [
                        inflection.singularize(field_name),
                        inflection.pluralize(field_name)
                    ]
                    includes = get_generic_included_serializers(parent)
                    for field in field_names:
                        if field in includes.keys():
                            for serializer in includes[field]:
                                if isinstance(value, dict) and value.get('type', None) == serializer.Meta.resource_name:
                                    return serializer.Meta.resource_name
                                elif isinstance(value, (serializer.Meta.model,)):
                                    return get_resource_type_from_serializer(serializer)

                return None

            class Meta:
                model = related_model

        return GenericResourceRelatedField

    class GenericSerializer(serializers.HyperlinkedModelSerializer):
        def __new__(cls, instance=None, *args, **kwargs):
            check_instance = instance
            if isinstance(instance, (list, QuerySet)) and len(instance) > 0:
                check_instance = instance[0]
            if check_instance is not None and not isinstance(check_instance, (cls.Meta.model, list, QuerySet)):
                serial = list(filter(lambda serial: serial.__name__.startswith(serializer_prefix),
                                     _MODEL_TO_SERIALIZERS[type(check_instance)]))
                return serial[-1](instance=instance, *args, **kwargs)
            return super(GenericSerializer, cls).__new__(cls, instance=instance, *args, **kwargs)

        @property
        def data(self):
            ret = super().data
            if after_list_callback is not None:
                ret = after_list_callback(self.context['request'], {'results': [ret]})['results'][0]
            return ReturnDict(ret, serializer=self)

    new_serializer = type(serializer_name, (GenericSerializer,), {
        **{custom_field.name: serializers.SerializerMethodField(read_only=True) for custom_field in
           custom_fields},
        **{f'get_{custom_field.name}': staticmethod(custom_field.callback) for custom_field in custom_fields},
        **{relation.field: generate_relation_field(relation)(
            queryset=getattr(model, relation.field).get_queryset()
            if hasattr(getattr(model, relation.field), 'get_queryset')
            else getattr(model, relation.field).field.related_model.objects.all(),
            many=relation.many,
            required=getattr(relation, 'required', False),
            related_link_view_name=f'{"admin_view_" if is_admin else ""}{relation.resource_name}{relation.api_version}-detail',
            related_link_lookup_field=primary_key_name,
            related_link_url_kwarg=relation.primary_key_name or 'id',
            self_link_view_name=f'{"admin_view_" if is_admin else ""}{resource_name}{serializer_suffix}-relationships'
        ) for relation in relations if hasattr(model, relation.field)},
        **{relation.field: GenericRelatedField(
            {
                related.model: generate_generic_resource(related.model)(
                    queryset=getattr(model, relation.field).get_queryset()
                    if hasattr(getattr(model, relation.field), 'get_queryset')
                    else related.model.objects.all(),
                    many=relation.many,
                    required=getattr(relation, 'required', False),
                    related_link_view_name=f'{"admin_view_" if is_admin else ""}{related.resource_name}{related.api_version}-detail',
                    related_link_lookup_field=primary_key_name,
                    related_link_url_kwarg='id',
                    self_link_view_name=f'{"admin_view_" if is_admin else ""}{resource_name}{serializer_suffix}-relationships'
                )
                for related in getattr(relation, 'related', [])
            },
            self_link_view_name=f'{"admin_view_" if is_admin else ""}{resource_name}{serializer_suffix}-relationships',
            related_link_lookup_field=primary_key_name) for relation in generic_relations if
            hasattr(model, relation.field)},
        'validate': validate_data,
        'Meta': type('Meta', (),
                     {'model': model, 'fields': [*[field for field in [*fields] if hasattr(model, field)], *list(
                         map(lambda custom_field: custom_field.name, custom_fields))],
                      'resource_name': resource_name}),
        'included_serializers': included_serializers,
        'included_generic_serializers': included_generic_serializers
    })
    if model not in _MODEL_TO_SERIALIZERS:
        _MODEL_TO_SERIALIZERS[model] = []
    _MODEL_TO_SERIALIZERS[model].append(new_serializer)
    return new_serializer


def construct_new_filters(computed_filter):
    def _generate_new_filters(self, queryset, field_name, value):
        return computed_filter.filter_func(queryset, value)

    return _generate_new_filters


def _construct_filter_backend(model: Type[Model], resource_name: str, filters: Dict[str, Filter],
                              computed_filters: Dict[str, ComputedFilter]) -> Tuple[Type, Type]:
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
                constructed_filters_transform_callbacks[filter_name] = filter.transform_value

    filter_set = type(f'{resource_name}FilterSet', (FilterSet,), {
        **constructed_filters,
        **{field: computed_filter.filter_type(method=f'filter_{field}') for field, computed_filter in
           computed_filters.items()},
        **{f'filter_{field}': construct_new_filters(computed_filter) for
           field, computed_filter in computed_filters.items()},
        'Meta': type('Meta', (), {
            'model': model,
            'fields': []
        })
    })

    def _get_filterset_kwargs(self, request, queryset, view):
        result = super(self.__class__, self).get_filterset_kwargs(request, queryset, view)
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
