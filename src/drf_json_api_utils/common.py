import logging
from functools import wraps
from rest_framework.response import Response
from rest_framework.filters import SearchFilter
from rest_framework.status import HTTP_500_INTERNAL_SERVER_ERROR
from rest_framework_json_api.pagination import JsonApiPageNumberPagination

DEFAULT_PAGE_SIZE = 50


def exception_handler(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            return Response(data={'attributes': {'message': str(exc)}},
                            status=getattr(exc, 'http_status', HTTP_500_INTERNAL_SERVER_ERROR))
    return wrapper


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


LOGGER = logging.getLogger(__name__)
