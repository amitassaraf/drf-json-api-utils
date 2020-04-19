# the inclusion of the tests module is not meant to offer best practices for
# testing in general, but rather to support the `find_packages` example in
# setup.py that excludes installing the "tests" package
from drf_json_api_utils.factory import JsonApiModelViewBuilder


def test_success():
    assert True
