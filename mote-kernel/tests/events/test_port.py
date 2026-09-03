"""Deterministic tests for the best-effort Events invocation port."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass

import pytest

from mote_kernel.events.port import EventPort
from mote_kernel.events.record import NodeSettlementEventReference
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId


def _event() -> NodeSettlementEventReference:
    return NodeSettlementEventReference(
        run_id=GraphRunId("run"),
        scope=("nested",),
        superstep=2,
        node_id=GraphNodeId("node"),
        execution_generation=3,
        settlement_revision=4,
    )


@dataclass(frozen=True, slots=True)
class _RecordingInvocation:
    events: list[NodeSettlementEventReference]

    async def invoke(self, event: NodeSettlementEventReference, /) -> None:
        self.events.append(event)


@dataclass(frozen=True, slots=True)
class _RaisingInvocation:
    error: BaseException

    async def invoke(self, _event: NodeSettlementEventReference, /) -> None:
        raise self.error


def test_event_port_is_a_frozen_slot_adapter() -> None:
    invocation = _RecordingInvocation([])
    port = EventPort(invocation)

    assert port.invocation is invocation
    assert "__dict__" not in EventPort.__slots__
    with pytest.raises(FrozenInstanceError):
        port.invocation = invocation  # type: ignore[misc]


@pytest.mark.asyncio
async def test_event_port_emits_one_typed_reference_once() -> None:
    event = _event()
    events: list[NodeSettlementEventReference] = []
    port = EventPort(_RecordingInvocation(events))

    assert await port.emit(event) is None
    assert await port.emit(event) is None
    assert events == [event, event]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    (RuntimeError("event adapter failed"), asyncio.CancelledError("event adapter cancelled")),
    ids=("ordinary-error", "adapter-cancellation"),
)
async def test_event_port_drops_adapter_owned_failures(error: BaseException) -> None:
    port = EventPort(_RaisingInvocation(error))

    assert await port.emit(_event()) is None


@pytest.mark.asyncio
async def test_event_port_does_not_drop_base_exceptions() -> None:
    class SystemSignal(BaseException):
        pass

    signal = SystemSignal("stop")
    port = EventPort(_RaisingInvocation(signal))

    with pytest.raises(SystemSignal) as raised:
        await port.emit(_event())
    assert raised.value is signal


@pytest.mark.asyncio
async def test_event_port_propagates_cancellation_of_the_calling_task() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    @dataclass(frozen=True, slots=True)
    class BlockingInvocation:
        async def invoke(self, _event: NodeSettlementEventReference, /) -> None:
            entered.set()
            await release.wait()

    task = asyncio.create_task(EventPort(BlockingInvocation()).emit(_event()))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
