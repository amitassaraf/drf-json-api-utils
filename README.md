# DRF Json Api Utils

Utilities to massivly reduce the boiler-plating of [django-rest-framework][drf].

This project currenly only supports and is specific to [django-rest-framework-json-api][drfjapi], if requested will extend it to general  [django-rest-framework][drf] support.

[The source for this project is available here][src].

Example Usage
----

By just doing this:

```python
user_urls = JsonApiViewBuilder(model=MyUser, 
                               resource_name='users',
                               allowed_methods=[json_api_spec_http_methods.HTTP_GET]) \
.fields(['email', 'first_name', 'last_name']) \
.add_filter(name='email', lookups=(lookups.EXACT, lookups.IN)) \
.add_filter(name='order', field='orders__id', lookups=(lookups.EXACT, lookups.IN)) \
.add_relation(field='orders', many=True) \
.get_urls()

order_urls = JsonApiViewBuilder(model=Order, 
                               resource_name='orders',
                               allowed_methods=[json_api_spec_http_methods.HTTP_GET]) \
.fields(['product', 'date', 'price']) \
.add_relation(field='user', resource_name='users') \
.get_urls()
```

You can get this:

`GET /api/users?filter[email]=amit.assaraf@gmail.com`
```json
{
    "links": {
        "first": "http://localhost:8000/api/users?filter%5Bemail%5D=soit48%40gmail.com&page%5Bnumber%5D=1",
        "last": "http://localhost:8000/api/users?filter%5Bemail%5D=soit48%40gmail.com&page%5Bnumber%5D=1",
        "next": null,
        "prev": null
    },
    "data": [
        {
            "type": "users",
            "id": "76f7c463-b6a8-4b20-917a-ef98c546eec4",
            "attributes": {
                "first-name": "Amit",
                "last-name": "Assaraf",
                "email": "amit.assaraf@gmail.com",
            },
            "relationships": {
                "orders": {
                    "meta": {
                        "count": 2
                    },
                    "data": [
                        {
                            "type": "orders",
                            "id": "6b99e044-462f-472e-9fed-307436b73549"
                        },
                        {
                            "type": "orders",
                            "id": "304f7f60-7e24-494e-98e2-e9786a3eb588"
                        }
                    ],
                    "links": {
                        "self": "http://localhost:8000/api/users/76f7c463-b6a8-4b20-917a-ef98c546eec4/relationships/orders",
                        "related": "http://localhost:8000/api/orders/76f7c463-b6a8-4b20-917a-ef98c546eec4/"
                    }
                }
            }
        }
    ],
    "meta": {
        "pagination": {
            "page": 1,
            "pages": 1,
            "count": 1
        }
    }
}
```

[src]: https://github.com/amitassaraf/drf-json-api-utils
[drfjapi]: https://github.com/django-json-api/django-rest-framework-json-api
[drf]: https://www.django-rest-framework.org/
