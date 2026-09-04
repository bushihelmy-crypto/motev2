"""An Events port must receive an invocation for its exact reference type."""

from mote_kernel.events.port import EventPort


class _WrongInvocation:
    async def invoke(self, _event: int, /) -> None:
        pass


EventPort(_WrongInvocation())
