from typing import Type, Optional, List, Sequence, Callable, Dict

import marshmallow
from marshmallow.fields import Function
from marshmallow_sqlalchemy import ModelConverter, auto_field, SQLAlchemySchema
from sqlalchemy import Enum

from drf_json_api_utils import CustomField
from drf_json_api_utils.sql_alchemy.types import AlchemyRelation
from .namespace import _TYPE_TO_SCHEMA
from uuid import UUID

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

    def json_api_dump(schema, objects, resource_type, with_data=True):
        """
        Custom dump for JSON api objects
        Args:
            schema: The model marshmallow schema
            objects: either a list or a single object
            resource_type: the name of the JSON-DRF object
            with_data: Should we wrap the output entry with {"data": entry} key

        Returns: a serialized json-api format list or dict

        """
        # Did we get a list of a single object
        many = isinstance(objects, list)

        # Dumping the objects using the model serializer
        result = schema.dump(objects, with_data=with_data)

        # We need to listify the result in case we received a single object
        if not many:
            result = [result]

        for entry in result:
            # Go over every item in the serialized object list and look for relationships.
            # As relationships need to be extracted outside the attributes
            for key, item in list(entry.items()):
                # look for a key that is a dictionary - that item will contain relationships
                if isinstance(item, (dict,)):
                    relations = entry.pop('relationships')
                    id = entry.pop('id')
                    attributes = dict(entry)
                    entry.clear()
                    entry['type'] = resource_type
                    entry['id'] = id
                    entry['attributes'] = attributes
                    entry['relationships'] = relations
        return result if many else result[0]

    def _custom_dump(data, with_data):
        """
        Gets a serialized object and extracts the relationships from it.
        Args:
            data: serialized object dict
            with_data: Should we wrap the content as {"data": data}

        Returns: serialized dict

        """
        relationships = {}
        for key, item in list(data.items()):
            # Check if we have a key that is included within the supported relationships
            for relation in support_relations:
                if relation.field_name == key:
                    id_or_ids = data[relation.field_name]
                    if isinstance(id_or_ids, (list, tuple,)):
                        relationships[relation.field_name] = [
                            {'type': relation.resource_name or relation.model.__tablename__, 'id': item}
                            for item in id_or_ids
                        ]
                    else:
                        relationships[relation.field_name] = {
                            'type': relation.resource_name or relation.model.__tablename__, 'id': id_or_ids
                        } if id_or_ids else None
                    if with_data:
                        relationships[relation.field_name] = {"data": relationships[relation.field_name]}
                    del data[relation.field_name]

        data['relationships'] = relationships
        return data

    def custom_dump(self, obj, many=None, with_data=True, **kwargs):
        """
        A wrapper for the costum serialized dump
        Args:
            self:
            obj: the object to serialized
            many: is it a list?
            with_data: should we wrap the output dict in {"data": data}
            **kwargs:

        Returns:

        """
        result = SQLAlchemySchema.dump(self, obj, many=many, **kwargs)

        if isinstance(result, list):
            result = [_custom_dump(r, with_data) for r in result]
        else:
            result = _custom_dump(result, with_data)

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
