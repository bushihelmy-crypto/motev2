import pytest

from mote_kernel.execution import Graph


class TypedResultConsumer:
    def __init__(self) -> None:
        self.successes: list[str] = []
        self.failures: list[str] = []
        self.interrupts: list[bytes] = []

    async def __call__(self, transition: Graph.Transition[str]) -> Graph.State:
        result = transition.result
        if isinstance(result, Graph.SuccessResult):
            self.successes.append(_require_text(result.output))
        elif isinstance(result, Graph.FailureResult):
            self.failures.append(result.failure)
        elif isinstance(result, Graph.InterruptResult):
            self.interrupts.append(result.request_payload)
        return transition.next_state


def _require_text(value: str) -> str:
    return value


def _encode_text(value: str) -> bytes:
    return value.encode()


def _decode_text(payload: bytes) -> str:
    return payload.decode()


@pytest.mark.asyncio
async def test_graph_namespace_strictly_narrows_every_commit_result_variant() -> None:
    async def succeed(value: str) -> str:
        return value

    async def fail(value: str) -> Graph.Outcome[str]:
        return Graph.failure(value)

    async def interrupt(value: str) -> Graph.Outcome[str]:
        return Graph.interrupt(value.encode())

    success_consumer = TypedResultConsumer()
    failure_consumer = TypedResultConsumer()
    interrupt_consumer = TypedResultConsumer()
    success_graph = Graph[str, str]("typing.success").add_node("node", succeed).add_edge(Graph.START, "node")
    failure_graph = Graph[str, str]("typing.failure").add_node("node", fail).add_edge(Graph.START, "node")
    interrupt_graph = Graph[str, str]("typing.interrupt")
    interrupt_graph.set_resume_codec("text", 1, _encode_text, _decode_text)
    interrupt_graph.add_node("node", interrupt).add_edge(Graph.START, "node")

    await success_graph.run("success", commit=success_consumer)
    await failure_graph.run("failure", commit=failure_consumer)
    await interrupt_graph.run("interrupt", commit=interrupt_consumer)

    assert success_consumer.successes == ["success"]
    assert failure_consumer.failures == ["failure"]
    assert interrupt_consumer.interrupts == [b"interrupt"]


@pytest.mark.asyncio
async def test_graph_namespace_exposes_precise_public_execution_errors() -> None:
    async def echo(value: str) -> str:
        return value

    invalid_graph = Graph[str, str]("typing.invalid").add_node("node", echo)
    with pytest.raises(Graph.ValidationError):
        await invalid_graph.run("input")

    graph = Graph[str, str]("typing.errors").add_node("node", echo).add_edge(Graph.START, "node")
    with pytest.raises(Graph.SnapshotMismatchError):
        await graph.run("input", resume=(graph.resume_failed("node"),))
    with pytest.raises(Graph.ExecutionLimitError):
        await graph.run("input", max_parallel_tasks=0)

    assert issubclass(Graph.ValidationError, Graph.Error)
    assert issubclass(Graph.SnapshotMismatchError, Graph.Error)
    assert issubclass(Graph.ExecutionLimitError, Graph.Error)
