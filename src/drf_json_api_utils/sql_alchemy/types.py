from dataclasses import dataclass
from typing import Type, Callable

from sqlalchemy.orm import Query


@dataclass
class AlchemyRelation:
    field_name: str
    model: Type
    resource_name: str = None
    many: bool = False
    primary_key: str = 'id'


@dataclass
class AlchemyComputedFilter:
    name: str
    filter_func: Callable[[Query, str, str], Query]
