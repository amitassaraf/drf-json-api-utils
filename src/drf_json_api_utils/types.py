from dataclasses import dataclass
from typing import Type

from recordclass import recordclass

Filter = recordclass('Filter', 'field lookups transform_value')
ComputedFilter = recordclass('ComputedFilter', 'field filter_type filter_func')
Relation = recordclass('Relation', 'field resource_name many primary_key_name required api_version')
GenericRelation = recordclass('GenericRelation', 'field related many required')
CustomField = recordclass('CustomField', 'name callback')


@dataclass
class RelatedResource:
    resource_name: str
    model: Type
    api_version: str = ''
