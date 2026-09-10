from dataclasses import dataclass
from typing import Never

import pytest
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence, interrupt_graph

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.persistence import DurableGraphCommit, GraphRecovery


@pytest.mark.asyncio
async def test_cold_interrupt_uses_the_same_resume_and_state_owned_override_path() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    waiting = await interrupt_graph(calls).run(
        Graph.values(value="question"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    graph = interrupt_graph(calls)
    replayed = await graph.run(recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store)))
    assert isinstance(replayed, Graph.AwaitingResumeResult)
    assert replayed.interrupts == waiting.interrupts
    store.fail_when = lambda request: request.candidate_state.execution is not None
    with pytest.raises(OSError):
        await graph.run(
            recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store)),
            resume=(graph.resume_interrupted("ask", waiting.interrupts[0].interrupt_id, Graph.values(value="answer")),),
        )
    store.reopen()
    completed = await interrupt_graph(calls).run(
        recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store))
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "answer"
    assert calls == ["question", "answer"]


@dataclass(frozen=True, slots=True, eq=False)
class OpaqueValue:
    text: str

    def __eq__(self, other: object) -> Never:
        raise AssertionError("business equality is not persistence proof")


@pytest.mark.asyncio
async def test_business_equality_is_never_used_for_durable_confirmation_or_recovery() -> None:

    def encode(values: Graph.Values[OpaqueValue]) -> bytes:
        return values["value"].text.encode()

    def decode(payload: bytes) -> Graph.Values[OpaqueValue]:
        return Graph.values(value=OpaqueValue(payload.decode()))

    def graph() -> Graph[OpaqueValue]:
        result = Graph[OpaqueValue]("opaque-values")

        async def operation(values: Graph.Values[OpaqueValue]) -> Graph.Values[OpaqueValue]:
            return values

        result.add_node(
            "echo",
            operation,
            inputs={"value": result.graph_input("value", OpaqueValue)},
            outputs={"value": OpaqueValue},
        )
        result.set_outputs({"value": result.output_ref("echo", "value")})
        return result

    codec = FrameCodec("opaque-text", 1, encode, decode)
    store = MemoryPersistence[OpaqueValue]()
    await graph().run(Graph.values(value=OpaqueValue("data")), run_id="run", commit=DurableGraphCommit(codec, store))
    result = await graph().run(recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(codec, store)))
    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"].text == "data"


@pytest.mark.asyncio
async def test_failed_run_remains_terminal_after_cold_recovery() -> None:

    def graph() -> Graph[str]:
        result = Graph[str]("persistent-failure")

        async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
            return Graph.failure("domain failure")

        result.add_node("fail", fail, inputs={}, outputs={})
        result.set_outputs({})
        return result

    store = MemoryPersistence[str]()
    failed = await graph().run(Graph.values(), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store))
    assert isinstance(failed, Graph.FailedResult)
    count = len(store.requests)
    recovered = await graph().run(recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store)))
    assert isinstance(recovered, Graph.FailedResult)
    assert recovered.failures == failed.failures
    assert len(store.requests) == count
