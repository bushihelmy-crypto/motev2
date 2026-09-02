"""The narrow typed invocation capability shared by Kernel domains."""

from typing import Protocol, TypeVar, runtime_checkable

RequestT_contra = TypeVar("RequestT_contra", contravariant=True)
ResultT_co = TypeVar("ResultT_co", covariant=True)


@runtime_checkable
class Invocation(Protocol[RequestT_contra, ResultT_co]):
    """Invoke one owner-defined typed request and return its typed result."""

    async def invoke(self, request: RequestT_contra, /) -> ResultT_co: ...


__all__ = ["Invocation"]
