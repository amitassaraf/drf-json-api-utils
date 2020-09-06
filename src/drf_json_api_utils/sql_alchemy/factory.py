from copy import deepcopy
from functools import partial
from typing import Type, Sequence, Tuple, Dict, List, Optional, Callable, Any

from marshmallow import INCLUDE
from marshmallow import ValidationError
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_204_NO_CONTENT, HTTP_404_NOT_FOUND, \
    HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Query
from sqlalchemy_filters import apply_filters
from sqlalchemy_filters import apply_pagination

from drf_json_api_utils import json_api_spec_http_methods, JsonApiResourceViewBuilder, plugins, CustomField
from drf_json_api_utils.sql_alchemy.constructors import auto_construct_schema, AlchemyRelation
from drf_json_api_utils.sql_alchemy.types import AlchemyComputedFilter
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
                 page_size: Optional[int] = 50,
                 skip_plugins: Optional[Sequence[str]] = None,
                 include_plugins: Optional[Sequence[str]] = None,
                 plugin_options: Optional[Dict[str, Any]] = None,
                 custom_field_handlers: Optional[Dict[Type, Callable]] = None):
        self._custom_field_handlers = custom_field_handlers
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
        self._custom_fields = {}
        self._computed_filters = {}
        self._skip_plugins = skip_plugins if skip_plugins is not None else [plugins.AUTO_ADMIN_VIEWS]
        include_plugins = include_plugins or []
        for item in include_plugins:
            if item in self._skip_plugins:
                self._skip_plugins.remove(item)
        self._plugin_options = plugin_options or {}

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

    def add_computed_filter(self, name: str,
                            filter_func: Callable[[Query, str, str], Query]) -> 'AlchemyJsonApiViewBuilder':
        self._computed_filters[name] = AlchemyComputedFilter(name=name, filter_func=filter_func)
        return self

    def add_custom_field(self, name: str,
                         instance_callback: Callable[[Any], Any] = None) -> 'AlchemyJsonApiViewBuilder':
        self._custom_fields[name] = CustomField(name=name, callback=instance_callback)
        return self

    def _get_admin_urls(self) -> Sequence[partial]:
        admin_builder = deepcopy(self)
        admin_builder._skip_plugins = [plugins.AUTO_ADMIN_VIEWS]
        admin_builder._permitted_objects = None
        admin_builder._resource_name = f'admin_view_{admin_builder._resource_name}'
        admin_permission_class = admin_builder._plugin_options.get(plugins.AUTO_ADMIN_VIEWS, {}).get(
            'ADMIN_PERMISSION_CLASS')

        if admin_permission_class is not None:
            admin_builder._permission_classes = [*admin_builder._permission_classes, admin_permission_class]

        admin_urls = admin_builder.get_urls(url_resource_name=self._resource_name, urls_prefix='admin/',
                                            ignore_serializer=True)

        return admin_urls

    def get_urls(self, url_resource_name: str = '', urls_prefix: str = '', ignore_serializer: bool = False):
        if ignore_serializer:
            SchemaType = _TYPE_TO_SCHEMA[self._model]['serializer']
        else:
            SchemaType = auto_construct_schema(self._model,
                                               resource_name=self._resource_name,
                                               fields=self._fields,
                                               support_relations=self._relations,
                                               custom_field_handlers=self._custom_field_handlers,
                                               custom_fields=self._custom_fields)
        schema = SchemaType()
        schema_many = SchemaType(many=True)

        def default_permitted_objects(request, query):
            return query

        def render_includes(includes, objects):

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
                    include_result = target_many.json_api_dump(to_include, schema['resource_name'],
                                                               with_data=False)
                    rendered_includes.extend(include_result)
                else:
                    raise Exception(f'Include {include} not supported on type {self._resource_name}')
            return rendered_includes

        permitted_objects = self._permitted_objects or default_permitted_objects

        def object_get(request, identifier, *args, **kwargs) -> Tuple[Dict, int]:
            permitted_query = permitted_objects(request, self._base_query or self._model.objects.query())

            obj = None

            try:
                obj = permitted_query.filter_by(**{self._primary_key or 'id': identifier}).first()
            except StatementError:
                obj = None
            finally:
                if not obj:
                    return {}, HTTP_404_NOT_FOUND

            if self._after_get_callback:
                obj = self._after_get_callback(request, obj)

            result = schema.json_api_dump(obj, self._resource_name)
            return result, HTTP_200_OK

        def object_list(request, page, filters=None, includes=None, *args, **kwargs) -> Tuple[List, List, int, int]:
            permitted_query = permitted_objects(request, self._base_query or self._model.objects.query())
            #
            #  Apply all the filters from the URL
            #
            if filters:
                filtered_query = permitted_query
                for filter in filters:
                    field = filter['field']
                    if field in self._computed_filters:
                        copied_filter = dict(filter)
                        copied_filter.pop('field')
                        filtered_query = self._computed_filters[field].filter_func(filtered_query, **copied_filter)
                        filters.remove(filter)
                filtered_query = apply_filters(filtered_query, filters)
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

            rendered_includes = render_includes(includes, objects)

            if self._after_list_callback:
                try:
                    objects = self._after_list_callback(request, objects)
                except Exception as e:
                    return [{'errors': [str(e)]}], [], 0, getattr(e, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR)

            result = schema_many.json_api_dump(objects, self._resource_name)
            return result, rendered_includes, pagination.total_results, HTTP_200_OK

        def object_create(request, data, *args, **kwargs) -> Tuple[Dict, str, int]:
            if 'multipart' in request.content_type:
                attributes = data
            else:
                attributes = data['attributes']
            if self._before_create_callback:
                try:
                    attributes = self._before_create_callback(request, attributes)
                except Exception as e:
                    return {'errors': [str(e)]}, '', getattr(e, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR)

            try:
                unmarshal_obj = schema.load(attributes, session=schema.db.session, unknown=INCLUDE)
            except ValidationError as err:
                return err.messages, '', HTTP_400_BAD_REQUEST

            obj = unmarshal_obj
            obj.save()

            if self._after_create_callback:
                try:
                    self._after_create_callback(request, attributes, obj)
                except Exception as e:
                    return {'errors': [str(e)]}, '', getattr(e, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR)
                obj.refresh_from_db()

            result = schema.json_api_dump(obj, self._resource_name)
            return result, obj.id, HTTP_201_CREATED

        def object_update(request, identifier, data, *args, **kwargs) -> Tuple[Dict, int]:
            permitted_query = permitted_objects(request, self._base_query or self._model.objects.query())
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
                    return {'errors': [str(e)]}, getattr(e, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR)
                obj.refresh_from_db()

            for field in self._fields:
                if field != identifier and field in attributes:
                    setattr(obj, field, attributes[field])
            obj.save()

            if self._after_update_callback:
                try:
                    self._after_update_callback(request, obj)
                except Exception as e:
                    return {'errors': [str(e)]}, getattr(e, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR)
                obj.refresh_from_db()

            result = schema.json_api_dump(obj, self._resource_name)
            return result, HTTP_200_OK

        def object_delete(request, identifier, *args, **kwargs) -> Tuple[int]:
            permitted_query = permitted_objects(request, self._base_query or self._model.objects.query())
            obj = permitted_query.filter_by(**{self._primary_key or 'id': identifier}).first()
            if self._before_delete_callback:
                try:
                    obj = self._before_delete_callback(request, obj)
                except Exception as e:
                    return getattr(e, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR)
            if obj:
                obj.delete()

                if self._after_delete_callback:
                    try:
                        self._after_delete_callback(request, obj)
                    except Exception as e:
                        return getattr(e, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR)
            return HTTP_204_NO_CONTENT

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

        urls = builder.get_urls(urls_prefix=urls_prefix, url_resource_name=url_resource_name)

        if plugins.AUTO_ADMIN_VIEWS not in self._skip_plugins:
            urls.extend(self._get_admin_urls())

        return urls
