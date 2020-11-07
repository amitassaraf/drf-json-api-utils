from collections import OrderedDict

from generic_relations.relations import RenamedMethods
from generic_relations.serializers import GenericSerializerMixin
from rest_framework_json_api.relations import ResourceRelatedField


class UpdatedGenericSerializerMixin(GenericSerializerMixin):
    def get_queryset(self):
        return None


class GenericRelatedField(UpdatedGenericSerializerMixin, ResourceRelatedField, metaclass=RenamedMethods):
    """
    Represents a generic relation / foreign key.
    It's actually more of a wrapper, that delegates the logic to registered
    serializers based on the `Model` class.
    """

    def get_links(self, obj=None, lookup_field='pk'):
        request = self.context.get('request', None)
        view = self.context.get('view', None)
        return_data = OrderedDict()

        kwargs = {lookup_field: getattr(obj, lookup_field) if obj else view.kwargs[lookup_field]}

        self_kwargs = kwargs.copy()
        self_kwargs.update({
            'related_field': self.field_name if self.field_name else self.parent.field_name
        })
        self_link = self.get_url('self', self.self_link_view_name, self_kwargs, request)

        # Assuming RelatedField will be declared in two ways:
        # 1. url(r'^authors/(?P<pk>[^/.]+)/(?P<related_field>\w+)/$',
        #         AuthorViewSet.as_view({'get': 'retrieve_related'}))
        # 2. url(r'^authors/(?P<author_pk>[^/.]+)/bio/$',
        #         AuthorBioViewSet.as_view({'get': 'retrieve'}))
        # So, if related_link_url_kwarg == 'pk' it will add 'related_field' parameter to reverse()
        related_instance = getattr(obj, self.field_name)
        if related_instance is not None:
            serializer = self.get_serializer_for_instance(related_instance)
            if serializer.related_link_url_kwarg == 'pk':
                related_kwargs = self_kwargs
            else:
                related_kwargs = {serializer.related_link_url_kwarg: kwargs[serializer.related_link_lookup_field]}

            related_link = self.get_url('related', serializer.related_link_view_name, related_kwargs, request)

            if self_link:
                return_data.update({'self': self_link})
            if related_link:
                return_data.update({'related': related_link})
        return return_data
