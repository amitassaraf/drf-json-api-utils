import logging

from rest_framework.filters import SearchFilter
from rest_framework_json_api.pagination import JsonApiPageNumberPagination

DEFAULT_PAGE_SIZE = 50


class LimitedJsonApiPageNumberPagination(JsonApiPageNumberPagination):
    page_size = DEFAULT_PAGE_SIZE


class JsonApiSearchFilter(SearchFilter):
    search_param = 'filter[search]'


LOGGER = logging.getLogger(__name__)
