import logging

from rest_framework.filters import SearchFilter
from rest_framework_json_api.pagination import JsonApiPageNumberPagination
from collections import namedtuple


class LimitedJsonApiPageNumberPagination(JsonApiPageNumberPagination):
    page_size = 50


class JsonApiSearchFilter(SearchFilter):
    search_param = 'filter[search]'


LOGGER = logging.getLogger(__name__)


UrlItem = namedtuple("UrlItem", ["django_url_object", "is_admin"])
