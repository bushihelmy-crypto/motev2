"""Strict positive typing example for the Events invocation port."""

from typing import assert_type

from mote_kernel.events.port import EventPort
from mote_kernel.events.record import NodeSettlementEventReference


class Invocation:
    async def invoke(self, _event: NodeSettlementEventReference, /) -> None:
        pass


port = EventPort(Invocation())
assert_type(port, EventPort)
