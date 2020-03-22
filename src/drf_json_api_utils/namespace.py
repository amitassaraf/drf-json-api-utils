#
#
#
#
from typing import Type


def _append_to_namespace(type: Type) -> None:
    globals()[type.__name__] = type
