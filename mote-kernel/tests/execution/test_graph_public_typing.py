from dataclasses import replace
from typing import Protocol, cast

import pytest

from mote_kernel.execution import Graph


class _PartialCommitConstructor(Protocol):
    def __call__(
        self,
        *,
        state: Graph.State,
        continuation: Graph.Continuation[str],
        cause: Exception,
        failed_scope: tuple[str, ...],
        _seal: object,
    ) -> Graph.Error: ...


class TypedResultConsumer:
    def __init__(self) -> None:
        self.successes: list[str] = []
        self.failures: list[str] = []
        self.interrupts: list[bytes] = []

    async def __call__(self, transition: Graph.Transition[str]) -> Graph.State:
        result = transition.writes.settlement
        if isinstance(result, Graph.SuccessResult):
            with pytest.raises(Graph.Error, match="settlement admission"):
                replace(result, _seal=1)
            self.successes.append(_require_text(result.output["value"]))
        elif isinstance(result, Graph.FailureResult):
            with pytest.raises(Graph.Error, match="settlement admission"):
                replace(result, _seal=1)
            assert transition.writes.publications == ()
            self.failures.append(result.failure)
        elif isinstance(result, Graph.InterruptResult):
            with pytest.raises(Graph.Error, match="settlement admission"):
                replace(result, _seal=1)
            assert transition.writes.publications == ()
            self.interrupts.append(result.request_payload)
        return transition.candidate_state


def _require_text(value: str) -> str:
    return value


def _encode_text(value: Graph.Values[str]) -> bytes:
    return value["value"].encode()


def _decode_text(payload: bytes) -> Graph.Values[str]:
    return Graph.values(value=payload.decode())


@pytest.mark.asyncio
async def test_graph_namespace_strictly_narrows_every_commit_result_variant() -> None:
    async def succeed(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    async def fail(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure(values["value"])

    async def interrupt(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(values["value"].encode())

    success_consumer = TypedResultConsumer()
    failure_consumer = TypedResultConsumer()
    interrupt_consumer = TypedResultConsumer()
    source = Graph.graph_input("value", str)
    success_graph = Graph[str]("typing.success")
    success_graph.add_node("node", succeed, inputs={"value": source}, outputs={"value": str})
    success_graph.set_outputs({})
    failure_graph = Graph[str]("typing.failure")
    failure_graph.add_node("node", fail, inputs={"value": source}, outputs={"value": str})
    failure_graph.set_outputs({})
    interrupt_graph = Graph[str]("typing.interrupt")
    interrupt_graph.set_resume_codec("text", 1, _encode_text, _decode_text)
    interrupt_graph.add_node("node", interrupt, inputs={"value": source}, outputs={"value": str})
    interrupt_graph.set_outputs({})

    await success_graph.run(Graph.values(value="success"), commit=success_consumer)
    await failure_graph.run(Graph.values(value="failure"), commit=failure_consumer)
    await interrupt_graph.run(Graph.values(value="interrupt"), commit=interrupt_consumer)

    assert success_consumer.successes == ["success"]
    assert failure_consumer.failures == ["failure"]
    assert interrupt_consumer.interrupts == [b"interrupt"]


@pytest.mark.asyncio
async def test_graph_namespace_exposes_precise_public_execution_errors() -> None:
    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    source = Graph.graph_input("value", str)
    invalid_graph = Graph[str]("typing.invalid")
    invalid_graph.add_node("node", echo, inputs={"value": source}, outputs={"value": str})
    with pytest.raises(Graph.ValidationError):
        await invalid_graph.run(Graph.values(value="input"))

    graph = Graph[str]("typing.errors")
    graph.set_resume_codec("text", 1, _encode_text, _decode_text)
    graph.add_node("node", echo, inputs={"value": source}, outputs={"value": str})
    graph.set_outputs({})
    completed = await graph.run(Graph.values(value="input"))
    assert isinstance(completed, Graph.CompletedResult)
    with pytest.raises(Graph.SnapshotMismatchError):
        await graph.run(
            state=completed.state,
            continuation=completed.continuation,
            resume=(
                graph.resume_interrupted(
                    "node",
                    "missing-interrupt",
                    Graph.values(value="replacement"),
                ),
            ),
        )
    with pytest.raises(Graph.ExecutionLimitError):
        await graph.run(Graph.values(value="input"), max_parallel_tasks=0)

    assert issubclass(Graph.ValidationError, Graph.Error)
    assert issubclass(Graph.SnapshotMismatchError, Graph.Error)
    assert issubclass(Graph.ExecutionLimitError, Graph.Error)
    assert issubclass(Graph.PartialCommitError, Graph.Error)
    partial_commit_error = cast(_PartialCommitConstructor, Graph.PartialCommitError[str])
    with pytest.raises(Graph.SnapshotMismatchError, match="Graph owner"):
        partial_commit_error(
            state=completed.state,
            continuation=completed.continuation,
            cause=RuntimeError("forged"),
            failed_scope=(),
            _seal=object(),
        )


@pytest.mark.asyncio
async def test_causal_start_input_is_replaced_by_the_actual_predecessor_on_reactivation() -> None:
    graph = Graph[int]("causal.start.runtime")
    received: list[int] = []

    async def source(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(value=values["seed"])

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        received.append(values["value"])
        return Graph.success(Graph.values(value=values["value"]), route="done")

    graph.add_node(
        "source",
        source,
        inputs={"seed": Graph.graph_input("seed", int)},
        outputs={"value": int},
    )
    graph.add_node(
        "loop",
        loop,
        inputs={"value": Graph.node_output("value")},
        outputs={"value": int},
    )
    graph.add_edge("source", "loop")
    graph.add_edge("loop", "done", Graph.END)
    graph.add_edge(Graph.START, "loop")
    graph.set_outputs({"result": Graph.node_output("loop", "value")})

    result = await graph.run(Graph.values(seed=3, value=10))

    assert isinstance(result, Graph.CompletedResult)
    assert received == [10, 3]
    assert result.outputs["result"] == 3
