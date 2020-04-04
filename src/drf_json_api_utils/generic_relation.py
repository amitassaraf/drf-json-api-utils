from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.urls import resolve, Resolver404
from rest_framework.fields import Field
from rest_framework.reverse import reverse
from rest_framework_json_api import serializers


class GenericRelatedField(Field):
    """
    A custom field that expect object URL as input and transforms it
    to django model instance.
    """
    read_only = False
    _default_view_name = '%(model_name)s-detail'
    lookup_field = 'pk'

    def __init__(self, related_models=(), custom_resource_names=None, **kwargs):
        super(GenericRelatedField, self).__init__(**kwargs)
        # related models - list of models that should be acceptable by
        # field. Note that all this models should have corresponding
        # endpoint.
        self.related_models = related_models
        self._custom_resource_names = custom_resource_names or {}

    def _get_url_basename(self, obj):
        """ Get object URL basename """
        format_kwargs = {
            'app_label': obj._meta.app_label,
            'model_name': self._custom_resource_names[
                type(obj)] if type(obj) in self._custom_resource_names else obj._meta.object_name.lower()
        }
        print(obj)
        print( self._custom_resource_names)
        print(self._default_view_name % format_kwargs)
        return self._default_view_name % format_kwargs

    def _get_request(self):
        try:
            return self.context['request']
        except KeyError:
            raise AttributeError('GenericRelatedField have to be initialized with `request` in context')

    def to_representation(self, obj):
        """ Serializes any object to its URL representation """
        kwargs = {self.lookup_field: getattr(obj, self.lookup_field)}
        request = self._get_request()
        return request.build_absolute_uri(reverse(self._get_url_basename(obj), kwargs=kwargs))

    def clear_url(self, url):
        """ Removes domain and protocol from url """
        if url.startswith('http'):
            return '/' + url.split('/', 3)[-1]
        return url

    def get_model_from_resolve_match(self, match):
        queryset = match.func.cls.queryset
        if queryset is not None:
            return queryset.model
        else:
            return match.func.cls.model

    def instance_from_url(self, url):
        url = self.clear_url(url)
        match = resolve(url)
        model = self.get_model_from_resolve_match(match)
        return model.objects.get(**match.kwargs)

    def to_internal_value(self, data):
        """ Restores model instance from its URL """
        if not data:
            return None
        request = self._get_request()
        user = request.user
        try:
            obj = self.instance_from_url(data)
            model = obj.__class__
        except (Resolver404, AttributeError, MultipleObjectsReturned, ObjectDoesNotExist):
            raise serializers.ValidationError("Can`t restore object from url: %s" % data)
        if model not in self.related_models:
            raise serializers.ValidationError('%s object does not support such relationship' % str(obj))
        return obj
