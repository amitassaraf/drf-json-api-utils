from recordclass import recordclass

Filter = recordclass('Filter', 'field lookups transform_value')
ComputedFilter = recordclass('ComputedFilter', 'field filter_type filter_func')
Relation = recordclass('Relation', 'field resource_name many primary_key_name required api_version')
GenericRelation = recordclass('GenericRelation', 'field related many required api_version')
CustomField = recordclass('CustomField', 'name callback')
