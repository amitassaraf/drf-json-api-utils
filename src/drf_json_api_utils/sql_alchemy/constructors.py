from typing import Type, Optional, List, Sequence, Callable, Dict

import marshmallow
from marshmallow.fields import Function
from marshmallow_sqlalchemy import ModelConverter, auto_field, SQLAlchemySchema
from sqlalchemy import Enum

from drf_json_api_utils import CustomField
from drf_json_api_utils.sql_alchemy.types import AlchemyRelation
from .namespace import _TYPE_TO_SCHEMA


class EnumField(marshmallow.fields.Field):

    def __init__(self, *args, **kwargs):
        self.column = kwargs.get('column')
        super(EnumField, self).__init__(*args, **kwargs)

    def _serialize(self, value, attr, obj, **kwargs):
        field = super(EnumField, self)._serialize(value, attr, obj)
        return field.value if field else field

    def deserialize(self, value, attr=None, data=None, **kwargs):
        return self.column.type.python_type(value) if isinstance(value, str) and self.column is not None else None


class ExtendModelConverter(ModelConverter):
    SQLA_TYPE_MAPPING = {
        **ModelConverter.SQLA_TYPE_MAPPING,
        Enum: EnumField,
    }

    def _add_column_kwargs(self, kwargs, column):
        super()._add_column_kwargs(kwargs, column)
        if hasattr(column.type, 'enums'):
            kwargs['column'] = column


def auto_construct_schema(alchemy_model: Type,
                          resource_name: str,
                          fields: Sequence[str],
                          support_relations: Optional[List[AlchemyRelation]] = None,
                          custom_field_handlers: Optional[Dict[Type, Callable]] = None,
                          custom_fields: Optional[Dict[str, CustomField]] = None):
    if custom_field_handlers is None:
        custom_field_handlers = {}

    if custom_fields is None:
        custom_fields = {}

    if support_relations is None:
        support_relations = []

    def json_api_dump(schema, objects, resource_type):
        result = schema.dump(objects)
        for item in result.data:
            if isinstance(item, (dict,)):
                relations = item.pop('relationships')
                id = item.pop('id')
                attributes = dict(item)
                item.clear()
                item['type'] = resource_type
                item['id'] = id
                item['attributes'] = attributes
                item['relationships'] = relations
        return result

    def custom_dump(self, obj, many=None, update_fields=True, **kwargs):
        result = SQLAlchemySchema.dump(self, obj, many=many, **kwargs)
        for key, item in result.items():
            relationships = {}
            if isinstance(item, (dict,)):
                for relation in support_relations:
                    if relation.field_name in item:
                        id_or_ids = item[relation.field_name]
                        if isinstance(id_or_ids, (list, tuple,)):
                            relationships[relation.field_name] = [{
                                'type': relation.resource_name or relation.model.__tablename__, 'id': item} for item in
                                id_or_ids]
                        else:
                            relationships[relation.field_name] = {
                                'type': relation.resource_name or relation.model.__tablename__, 'id': id_or_ids} if \
                                item[
                                    relation.field_name] else None
                        del item[relation.field_name]
                item['relationships'] = relationships
        return result

    generated_fields = {}
    additional = []
    for field in fields:
        model_field = getattr(alchemy_model, field, None)
        composite_class = getattr(getattr(model_field, 'property', None), 'composite_class',
                                  None)
        if composite_class is not None and composite_class in custom_field_handlers:
            generated_fields[field] = custom_field_handlers[composite_class](field)
        else:
            if not isinstance(model_field, (property,)) and not model_field.__class__.__name__ == 'hybrid_propertyProxy':
                generated_fields[field] = auto_field()
            else:
                additional.append(field)

    generated_custom_fields = {}
    for name, custom_field in custom_fields.items():
        generated_custom_fields[name] = Function(custom_field.callback)

    new_serializer = type(f'{alchemy_model.__tablename__}Serializer', (SQLAlchemySchema,), {
        'id': marshmallow.fields.String(dump_only=True),
        **generated_fields,
        **generated_custom_fields,
        'Meta': type('Meta', (), {
            'load_instance': True,
            'include_fk': True,
            'include_relationships': True,
            'model': alchemy_model,
            'model_converter': ExtendModelConverter,
            'additional': additional
        }),
        'dump': custom_dump,
        'json_api_dump': json_api_dump,
        'db': alchemy_model.db
    })
    _TYPE_TO_SCHEMA[alchemy_model] = {'serializer': new_serializer, 'resource_name': resource_name}
    return new_serializer
