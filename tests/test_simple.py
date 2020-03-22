# the inclusion of the tests module is not meant to offer best practices for
# testing in general, but rather to support the `find_packages` example in
# setup.py that excludes installing the "tests" package
from drf_json_api_utils.factory import JsonApiViewBuilder


def test_success():
    JsonApiViewBuilder(model=None)
    assert True
