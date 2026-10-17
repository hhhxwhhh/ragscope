from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - PEP 695 generics need pydantic>=2.11
    items: list[T]
    total: int
    offset: int
    limit: int
