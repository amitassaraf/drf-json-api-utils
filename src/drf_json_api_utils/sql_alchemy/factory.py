from typing import Type, Sequence, Tuple, Dict, List, Optional, Callable, Any

from drf_json_api_utils import json_api_spec_http_methods, JsonApiResourceViewBuilder
from drf_json_api_utils.sql_alchemy.constructors import auto_construct_schema, AlchemyRelation
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.status import HTTP_200_OK
from sqlalchemy.orm import Query
from sqlalchemy_filters import apply_filters
from sqlalchemy_filters import apply_pagination

from .namespace import _TYPE_TO_SCHEMA
from ..common import LOGGER


class AlchemyJsonApiViewBuilder:
    """
    This class assumes use of Landa's ManagerBase and AlchemyBase
    """

    def __init__(self,
                 alchemy_model: Type,
                 resource_name: str,
                 fields: Sequence[str],
                 primary_key: Optional[str] = 'id',
                 allowed_methods: Optional[Sequence[str]] = json_api_spec_http_methods.HTTP_ACTIONS,
                 base_query: Optional[Query] = None,
                 permitted_objects: Optional[Callable[[Request, Query], Query]] = None,
                 permission_classes: Sequence[Type[BasePermission]] = None,
                 authentication_classes: Sequence[Type[BaseAuthentication]] = None,
                 page_size: Optional[int] = 50):
        self._model = alchemy_model
        self._resource_name = resource_name
        self._fields = fields
        self._primary_key = primary_key
        self._allowed_methods = allowed_methods
        self._base_query = base_query
        self._permitted_objects = permitted_objects
        self._page_size = page_size
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
        self._relations = []

    def __warn_if_method_not_available(self, method: str):
        if method not in self._allowed_methods:
            LOGGER.warning(
                f'You\'ve set a lifecycle callback for resource {self._resource_name}, '
                f'which doesn\'t allow it\'s respective HTTP method through `allowed_methods`.')

    def before_create(self, before_create_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._before_create_callback = before_create_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_POST)
        return self

    def after_create(self, after_create_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._after_create_callback = after_create_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_POST)
        return self

    def after_get(self, after_get_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._after_get_callback = after_get_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def before_update(self, before_update_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._before_update_callback = before_update_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_PATCH)
        return self

    def after_update(self, after_update_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._after_update_callback = after_update_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_PATCH)
        return self

    def before_delete(self, before_delete_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._before_delete_callback = before_delete_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_DELETE)
        return self

    def after_delete(self, after_delete_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._after_delete_callback = after_delete_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_DELETE)
        return self

    def before_list(self,
                    before_list_callback: Callable[[Request, Query], Query] = None) -> 'AlchemyJsonApiViewBuilder':
        self._before_list_callback = before_list_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def after_list(self, after_list_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._after_list_callback = after_list_callback
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def add_relation(self, field: str,
                     model: Type,
                     resource_name: str,
                     many: bool = False,
                     primary_key_name: str = None) -> 'AlchemyJsonApiViewBuilder':
        self._relations.append(AlchemyRelation(field_name=field, model=model, many=many,
                                               resource_name=resource_name,
                                               primary_key=primary_key_name))
        return self

    def get_urls(self):
        SchemaType = auto_construct_schema(self._model,
                                           resource_name=self._resource_name,
                                           fields=self._fields,
                                           support_relations=self._relations)
        schema = SchemaType()
        schema_many = SchemaType(many=True)

        base_query = self._base_query or self._model.objects.query()

        def default_permitted_objects(request, query):
            return query

        permitted_objects = self._permitted_objects or default_permitted_objects

        def object_get(request, identifier, *args, **kwargs) -> Tuple[Dict, int]:
            permitted_query = permitted_objects(request, base_query)

            obj = permitted_query.filter(**{self._primary_key or 'id': identifier}).get()

            if self._after_get_callback:
                obj = self._after_get_callback(request, obj)

            return schema.dump(obj).data, HTTP_200_OK

        def object_list(request, page, filters=None, includes=None, *args, **kwargs) -> Tuple[List, List, int, int]:
            permitted_query = permitted_objects(request, base_query)
            #
            #  Apply all the filters from the URL
            #
            if filters:
                filtered_query = apply_filters(permitted_query, filters)
            else:
                filtered_query = permitted_query

            #
            #  Paginate the result
            #
            query, pagination = apply_pagination(filtered_query, page_number=page, page_size=self._page_size)

            if self._before_list_callback:
                query = self._before_list_callback(request, query)

            #  Fetch the values from DB
            objects = query.all()

            #
            #  Go over all the keys that the user wants to include, and serialize them
            #
            rendered_includes = []
            for include in includes:
                #  Get the column on the model
                include_on_model = getattr(self._model, include, None)
                if include_on_model:
                    model_property = include_on_model.property
                    #  Get the primary key of the target relationship table
                    target_primary_key = model_property.target.primary_key.columns.values()[0].name
                    #  Get the local foreign key column name that connects the relationship
                    local_column = model_property.local_columns.copy().pop()
                    #  Get the Alchemy Model of the target relationship table
                    target_model = model_property.mapper.class_
                    #  Check that the item we are targeting has a JSON:API view
                    if target_model not in _TYPE_TO_SCHEMA:
                        raise Exception(f'No JSON:API view defined for type {target_model}')
                    #  Fetch all the related items to be included in the result
                    to_include = target_model.objects.query().filter(
                        getattr(target_model, target_primary_key).in_(
                            [getattr(item, local_column.name) for item in objects])).all()
                    schema = _TYPE_TO_SCHEMA[target_model]
                    target_many = schema['serializer'](many=True)
                    #  Serialize all the included objects to JSON:API
                    include_result = target_many.json_api_dump(to_include, schema['resource_name'])
                    rendered_includes.extend(include_result.data)
                else:
                    raise Exception(f'Include {include} not supported on type {self._resource_name}')

            if self._after_list_callback:
                objects = self._after_list_callback(request, objects)

            result = schema_many.json_api_dump(objects, self._resource_name)
            return result.data, rendered_includes, pagination.total_results, HTTP_200_OK

        def object_create(request, data, *args, **kwargs) -> Tuple[Dict, str, int]:
            if 'multipart' in request.content_type:
                attributes = data
            else:
                attributes = data['attributes']

            if self._before_create_callback:
                attributes = self._before_create_callback(request, attributes)

            unmarshal_obj = schema.load(attributes, session=schema.session)
            obj = unmarshal_obj.data
            obj.save()
            obj.refresh_from_db()

            if self._after_create_callback:
                self._after_create_callback(request, attributes, obj)

            obj.refresh_from_db()

            return schema.dump(obj).data, obj.id, HTTP_200_OK

        def object_update(request, identifier, data, *args, **kwargs) -> Tuple[Dict, int]:
            permitted_query = permitted_objects(request, base_query)
            obj = permitted_query.filter(**{self._primary_key or 'id': identifier}).get()
            attributes = data['attributes']

            if self._before_update_callback:
                attributes = self._before_update_callback(request, obj, attributes)

            for field in self._fields:
                if field != identifier:
                    setattr(obj, field, attributes[field])

            obj.save()
            obj.refresh_from_db()

            if self._after_update_callback:
                self._after_update_callback(request, obj)

            obj.refresh_from_db()

            return schema.dump(obj).data, HTTP_200_OK

        def object_delete(request, identifier, *args, **kwargs) -> Tuple[int]:
            permitted_query = permitted_objects(request, base_query)
            obj = permitted_query.filter(**{self._primary_key or 'id': identifier}).get()
            if self._before_delete_callback:
                obj = self._before_delete_callback(request, obj)
            if obj:
                obj.delete()

                if self._after_delete_callback:
                    self._after_delete_callback(request, obj)
            return HTTP_200_OK

        builder = JsonApiResourceViewBuilder(action_name=self._resource_name,
                                             unique_identifier=self._primary_key,
                                             allowed_methods=self._allowed_methods,
                                             permission_classes=self._permission_classes,
                                             authentication_classes=self._authentication_classes,
                                             raw_items=True)

        if json_api_spec_http_methods.HTTP_GET in self._allowed_methods:
            builder = builder.on_get(get_callback=object_get).on_list(list_callback=object_list)

        if json_api_spec_http_methods.HTTP_POST in self._allowed_methods:
            builder = builder.on_create(create_callback=object_create)

        if json_api_spec_http_methods.HTTP_PATCH in self._allowed_methods:
            builder = builder.on_update(update_callback=object_update)

        if json_api_spec_http_methods.HTTP_DELETE in self._allowed_methods:
            builder = builder.on_delete(delete_callback=object_delete)

        return builder.get_urls()
