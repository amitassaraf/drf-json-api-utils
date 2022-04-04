from copy import deepcopy
from typing import Type, Sequence, Tuple, Dict, List, Optional, Callable, Any

import traceback
from marshmallow import INCLUDE
from marshmallow import ValidationError
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_204_NO_CONTENT, HTTP_404_NOT_FOUND, \
    HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR
from sqlalchemy.exc import StatementError
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Query
from sqlalchemy_filters import apply_filters
from sqlalchemy_filters import apply_pagination
from django.conf.urls import url

from drf_json_api_utils import json_api_spec_http_methods, JsonApiResourceViewBuilder, CustomField
from drf_json_api_utils.sql_alchemy.constructors import auto_construct_schema, AlchemyRelation
from drf_json_api_utils.sql_alchemy.types import AlchemyComputedFilter, OKAlreadyExists
from .namespace import _TYPE_TO_SCHEMA
from ..common import LOGGER, JsonApiGlobalSettings


class AlchemyJsonApiViewBuilder:
    """
    This class assumes use of Landa's ManagerBase and AlchemyBase
    """

    ATTRIBUTES = {
        '_custom_field_handlers': {'kwarg': 'custom_field_handlers', 'default': None},
        '_model': {'kwarg': 'alchemy_model', 'default': None},
        '_resource_name': {'kwarg': 'resource_name', 'default': None},
        '_fields': {'kwarg': 'fields', 'default': None},
        '_primary_key': {'kwarg': 'primary_key', 'default': 'id'},
        '_allowed_methods': {'kwarg': 'allowed_methods', 'default': None},
        '_base_query': {'kwarg': 'base_query', 'default': None},
        '_permitted_objects': {'kwarg': 'permitted_objects', 'default': None},
        '_page_size': {'kwarg': 'page_size', 'default': 50},
        '_permission_classes': {'kwarg': 'permission_classes', 'default': []},
        '_authentication_classes': {'kwarg': 'authentication_classes', 'default': []},
        '_before_create_callback': {'kwarg': None, 'default': None},
        '_after_create_callback': {'kwarg': None, 'default': None},
        '_after_get_callback': {'kwarg': None, 'default': None},
        '_before_update_callback': {'kwarg': None, 'default': None},
        '_after_update_callback': {'kwarg': None, 'default': None},
        '_before_delete_callback': {'kwarg': None, 'default': None},
        '_after_delete_callback': {'kwarg': None, 'default': None},
        '_before_list_callback': {'kwarg': None, 'default': None},
        '_after_list_callback': {'kwarg': None, 'default': None},
        '_before_get_response': {'kwarg': None, 'default': None},
        '_after_serialization': {'kwarg': None, 'default': None},
        '_relations': {'kwarg': None, 'default': []},
        '_custom_fields': {'kwarg': None, 'default': {}},
        '_computed_filters': {'kwarg': None, 'default': {}},
        '_api_version': {'kwarg': 'api_version', 'default': ''},
        '_is_admin': {'kwarg': 'is_admin', 'default': False},
        '_always_include': {'kwarg': 'always_include', 'default': False},
        '_skip_plugins': {'kwarg': 'skip_plugins', 'default': []},
        '_plugin_options': {'kwarg': 'plugin_options', 'default': {}},
    }

    def __init__(self,
                 alchemy_model: Type,
                 resource_name: str,
                 fields: Sequence[str],
                 api_version: Optional[str] = '',
                 primary_key: Optional[str] = 'id',
                 allowed_methods: Optional[Sequence[str]] = json_api_spec_http_methods.HTTP_ACTIONS,
                 base_query: Optional[Callable[[Any], Query]] = None,
                 permitted_objects: Optional[Callable[[Request, Query], Query]] = None,
                 permission_classes: Sequence[Type[BasePermission]] = None,
                 authentication_classes: Sequence[Type[BaseAuthentication]] = None,
                 page_size: Optional[int] = 50,
                 skip_plugins: Optional[Sequence[str]] = None,
                 include_plugins: Optional[Sequence[str]] = None,
                 plugin_options: Optional[Dict[str, Any]] = None,
                 custom_field_handlers: Optional[Dict[Type, Callable]] = None,
                 is_admin: Optional[bool] = False,
                 always_include: Optional[bool] = False):
        self.override(
            alchemy_model=alchemy_model,
            resource_name=resource_name,
            fields=fields,
            api_version=api_version,
            primary_key=primary_key,
            allowed_methods=allowed_methods,
            base_query=base_query,
            permitted_objects=permitted_objects,
            permission_classes=permission_classes,
            authentication_classes=authentication_classes,
            page_size=page_size,
            skip_plugins=skip_plugins,
            include_plugins=include_plugins,
            plugin_options=plugin_options,
            custom_field_handlers=custom_field_handlers,
            is_admin=is_admin,
            always_include=always_include,
        )
        self.settings = JsonApiGlobalSettings()

    @staticmethod
    def from_view_builder(view_builder: 'AlchemyJsonApiViewBuilder') -> 'AlchemyJsonApiViewBuilder':
        return deepcopy(view_builder)

    def override(self,
                 alchemy_model: Optional[Type] = None,
                 resource_name: Optional[str] = None,
                 fields: Optional[Sequence[str]] = None,
                 api_version: Optional[str] = None,
                 primary_key: Optional[str] = None,
                 allowed_methods: Optional[Sequence[str]] = None,
                 base_query: Optional[Callable[[Any], Query]] = None,
                 permitted_objects: Optional[Callable[[Request, Query], Query]] = None,
                 permission_classes: Optional[Sequence[Type[BasePermission]]] = None,
                 authentication_classes: Optional[Sequence[Type[BaseAuthentication]]] = None,
                 page_size: Optional[int] = None,
                 skip_plugins: Optional[Sequence[str]] = None,
                 include_plugins: Optional[Sequence[str]] = None,
                 plugin_options: Optional[Dict[str, Any]] = None,
                 custom_field_handlers: Optional[Dict[Type, Callable]] = None,
                 is_admin: Optional[bool] = False,
                 always_include: Optional[bool] = False) -> 'AlchemyJsonApiViewBuilder':

        _locals = locals()
        for attribute, settings in self.ATTRIBUTES.items():
            _arg = _locals.get(settings.get("kwarg"))
            if _arg is not None:
                setattr(self, attribute, _arg)
            elif not hasattr(self, attribute):
                setattr(self, attribute, deepcopy(settings.get("default")))

        if include_plugins is not None:
            for item in include_plugins:
                if item in self._skip_plugins:
                    self._skip_plugins.remove(item)

        return self

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

    def before_get_response(self, before_get_response: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._before_get_response = before_get_response
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

    def after_serialization(self, after_serialization: Callable[[Any, Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._after_serialization = after_serialization
        self.__warn_if_method_not_available(json_api_spec_http_methods.HTTP_GET)
        return self

    def add_field(self, name: str) -> 'AlchemyJsonApiViewBuilder':
        if name not in self._fields:
            self._fields.append(name)
        return self

    def add_relation(self, field: str,
                     model: Type,
                     resource_name: str,
                     many: bool = False,
                     primary_key_name: str = None,
                     api_version: Optional[str] = '') -> 'AlchemyJsonApiViewBuilder':
        self._relations.append(AlchemyRelation(field_name=field, model=model, many=many,
                                               resource_name=resource_name,
                                               primary_key=primary_key_name,
                                               api_version=api_version))
        return self

    def add_computed_filter(self, name: str,
                            filter_func: Callable[[Query, str, str], Query]) -> 'AlchemyJsonApiViewBuilder':
        self._computed_filters[name] = AlchemyComputedFilter(name=name, filter_func=filter_func)
        return self

    def add_custom_field(self, name: str,
                         instance_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._custom_fields[name] = CustomField(name=name, callback=instance_callback)
        return self

    def get_urls(self,
                 url_resource_name: Optional[str] = None,
                 urls_prefix: Optional[str] = None,
                 ignore_serializer: Optional[bool] = False) -> List[url]:
        SchemaType = None
        if ignore_serializer:
            SchemaType = list(
                filter(lambda item: item['api_version'] == self._api_version and item['is_admin'] == self.is_admin,
                       _TYPE_TO_SCHEMA[self._model]))
            if SchemaType:
                SchemaType = SchemaType[0]['serializer']

        if not SchemaType:
            SchemaType = auto_construct_schema(self._model,
                                               resource_name=self._resource_name,
                                               api_version=self._api_version,
                                               fields=self._fields,
                                               is_admin=self._is_admin,
                                               support_relations=self._relations,
                                               custom_field_handlers=self._custom_field_handlers,
                                               custom_fields=self._custom_fields)
        schema = SchemaType()
        schema_many = SchemaType(many=True)

        def default_permitted_objects(request, query):
            return query

        def render_includes(includes, objects, request):

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
                    primary_join = model_property.primaryjoin
                    #  Get the local foreign key column name that connects the relationship
                    local_column = model_property.local_columns.copy().pop()
                    #  Get the Alchemy Model of the target relationship table
                    target_model = model_property.mapper.class_
                    #  Check that the item we are targeting has a JSON:API view
                    if target_model not in _TYPE_TO_SCHEMA:
                        raise Exception(f'No JSON:API view defined for type {target_model}')
                    primary_key = inspect(self._model).primary_key[0].name
                    #  Fetch all the related items to be included in the result
                    to_include = target_model.objects.query().filter(primary_join,
                                                                     getattr(self._model, primary_key).in_(
                                                                         [getattr(item, primary_key) for item in
                                                                          objects]
                                                                     )).all()
                    relevant_relation = next(
                        (relation for relation in self._relations if relation.field_name == include), None)
                    schema = list(filter(lambda item: item['api_version'] == relevant_relation.api_version and item[
                        'is_admin'] == self.is_admin,
                                         _TYPE_TO_SCHEMA[target_model]))[0]
                    target_many = schema['serializer'](many=True)
                    #  Serialize all the included objects to JSON:API
                    target_many.context = {'request': request}
                    include_result = target_many.json_api_dump(to_include, schema['resource_name'],
                                                               with_data=False)
                    rendered_includes.extend(include_result)

            return rendered_includes

        permitted_objects = self._permitted_objects or default_permitted_objects

        def object_get(request, identifier, includes, *args, **kwargs) -> Tuple[Dict, int]:
            permitted_query = permitted_objects(request,
                                                self._base_query() if self._base_query is not None else self._model.objects.query())

            obj = None

            try:
                obj = permitted_query.filter_by(**{self._primary_key or 'id': identifier}).first()
            except StatementError:
                obj = None
            finally:
                if not obj:
                    return {}, [], HTTP_404_NOT_FOUND

            if self._after_get_callback:
                obj = self._after_get_callback(request, obj)

            rendered_includes = render_includes(includes, [obj], request)
            schema.context = {'request': request}
            result = schema.json_api_dump(obj, self._resource_name)

            if self._before_get_response:
                result = self._before_get_response(request, obj, result)

            return result, rendered_includes, HTTP_200_OK

        def object_list(request, page, filters=None, includes=None, *args, **kwargs) -> Tuple[List, List, int, int]:
            permitted_query = permitted_objects(request,
                                                self._base_query() if self._base_query is not None else self._model.objects.query())
            #
            #  Apply all the filters from the URL
            #
            if filters:
                filtered_query = permitted_query
                copied_filters = filters[:]
                for filter in filters:
                    field = filter['field']
                    if field in self._computed_filters:
                        copied_filter = dict(filter)
                        copied_filter.pop('field')
                        filtered_query = self._computed_filters[field].filter_func(filtered_query, **copied_filter)
                        copied_filters.remove(filter)
                filtered_query = apply_filters(filtered_query, copied_filters)
            else:
                filtered_query = permitted_query

            #
            #  Paginate the result
            #
            query, pagination = apply_pagination(filtered_query, page_number=int(page), page_size=self._page_size)

            if self._before_list_callback:
                query = self._before_list_callback(request, query)

            #  Fetch the values from DB
            objects = query.all()
            rendered_includes = render_includes(includes, objects, request)

            try:
                if self._after_list_callback:
                    objects = self._after_list_callback(request, objects)

                schema_many.context = {'request': request}
                result = schema_many.json_api_dump(objects, self._resource_name)

                if self._after_serialization:
                    result, rendered_includes = self._after_serialization(request, result, rendered_includes)

            except Exception as e:
                # Raise to DRF handler
                raise e

            return result, rendered_includes, pagination.total_results, HTTP_200_OK

        def object_create(request, data, *args, **kwargs) -> Tuple[Dict, str, int]:
            if 'multipart' in request.content_type:
                attributes = data
            else:
                attributes = data['attributes']
            if self._before_create_callback:
                try:
                    attributes = self._before_create_callback(request, attributes)
                except OKAlreadyExists:
                    return {'status': 'OK'}, '', HTTP_201_CREATED
                except Exception as e:
                    # Raise to DRF handler
                    raise e

            try:
                schema.context = {'request': request}
                unmarshal_obj = schema.load(attributes, session=schema.db.session, unknown=INCLUDE)
            except ValidationError as err:
                return err.messages, '', HTTP_400_BAD_REQUEST

            obj = unmarshal_obj
            obj.save()

            if self._after_create_callback:
                try:
                    self._after_create_callback(request, attributes, obj)
                except Exception as e:
                    # Raise to DRF handler
                    raise e
                obj.refresh_from_db()

            schema.context = {'request': request}
            result = schema.json_api_dump(obj, self._resource_name)
            return result, obj.id, HTTP_201_CREATED

        def object_update(request, identifier, data, *args, **kwargs) -> Tuple[Dict, int]:
            permitted_query = permitted_objects(request,
                                                self._base_query() if self._base_query is not None else self._model.objects.query())
            obj = None
            try:
                obj = permitted_query.filter_by(**{self._primary_key or 'id': identifier}).first()
            except StatementError:
                obj = None
            finally:
                if not obj:
                    return {}, HTTP_404_NOT_FOUND

            attributes = data['attributes']
            if self._before_update_callback:
                try:
                    attributes = self._before_update_callback(request, attributes, obj)
                except Exception as e:
                    # Raise to DRF handler
                    raise e
                obj.refresh_from_db()

            for field in self._fields:
                if field != identifier and field in attributes:
                    setattr(obj, field, attributes[field])
            obj.save()

            if self._after_update_callback:
                try:
                    self._after_update_callback(request, obj)
                except Exception as e:
                    # Raise to DRF handler
                    raise e
                obj.refresh_from_db()

            schema.context = {'request': request}
            result = schema.json_api_dump(obj, self._resource_name)
            return result, HTTP_200_OK

        def object_delete(request, identifier, *args, **kwargs) -> Tuple[int]:
            permitted_query = permitted_objects(request,
                                                self._base_query() if self._base_query is not None else self._model.objects.query())
            obj = permitted_query.filter_by(**{self._primary_key or 'id': identifier}).first()
            if self._before_delete_callback:
                try:
                    obj = self._before_delete_callback(request, obj)
                except Exception as e:
                    # Raise to DRF handler
                    raise e
            if obj:
                obj.delete()

                if self._after_delete_callback:
                    try:
                        self._after_delete_callback(request, obj)
                    except Exception as e:
                        # Raise to DRF handler
                        raise e
            return HTTP_204_NO_CONTENT

        builder = JsonApiResourceViewBuilder(action_name=self._resource_name,
                                             api_version=self._api_version,
                                             unique_identifier=self._primary_key,
                                             allowed_methods=self._allowed_methods,
                                             permission_classes=self._permission_classes,
                                             authentication_classes=self._authentication_classes,
                                             is_admin=self._is_admin,
                                             always_include=self._always_include,
                                             page_size=self._page_size,
                                             raw_items=True)

        if json_api_spec_http_methods.HTTP_GET in self._allowed_methods:
            builder = builder.on_get(get_callback=object_get).on_list(list_callback=object_list)

        if json_api_spec_http_methods.HTTP_POST in self._allowed_methods:
            builder = builder.on_create(create_callback=object_create)

        if json_api_spec_http_methods.HTTP_PATCH in self._allowed_methods:
            builder = builder.on_update(update_callback=object_update)

        if json_api_spec_http_methods.HTTP_DELETE in self._allowed_methods:
            builder = builder.on_delete(delete_callback=object_delete)

        urls = builder.get_urls(urls_prefix=urls_prefix, url_resource_name=url_resource_name)

        return urls
