from recordclass import recordclass

Filter = recordclass('Filter', 'field lookups transform_value')
ComputedFilter = recordclass('ComputedFilter', 'field filter_type filter_func')
Relation = recordclass('Relation', 'field resource_name many primary_key_name required')
GenericRelation = recordclass('GenericRelation', 'field related many required')
CustomField = recordclass('CustomField', 'name callback')
