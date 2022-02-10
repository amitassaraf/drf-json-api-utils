import logging
from functools import wraps
from rest_framework.response import Response
from rest_framework.filters import SearchFilter
from rest_framework.status import HTTP_500_INTERNAL_SERVER_ERROR
from rest_framework_json_api.pagination import JsonApiPageNumberPagination
import traceback

DEFAULT_PAGE_SIZE = 50


class LimitedJsonApiPageNumberPagination(JsonApiPageNumberPagination):
    page_size = DEFAULT_PAGE_SIZE


class JsonApiSearchFilter(SearchFilter):
    search_param = 'filter[search]'


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class JsonApiGlobalSettings(metaclass=Singleton):
    pass


def exception_handler(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            return handle_exception_gracefully(exc)

    return wrapper


def handle_exception_gracefully(exception: Exception):
    traceback.print_exc()

    settings = JsonApiGlobalSettings()
    exception_response_handler = getattr(settings, 'exception_response_handler', None)
    exception_callback = getattr(settings, 'exception_callback')
    if exception_callback:
        exception_callback(exception)
    if exception_response_handler:
        return exception_response_handler(exception)

    return Response(data={'attributes': {'message': str(exception)}},
                    status=getattr(exception, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR))


LOGGER = logging.getLogger(__name__)
