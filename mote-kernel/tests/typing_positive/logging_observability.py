"""Strict positive typing examples for the diagnostic decorator contract."""

from collections.abc import Awaitable, Callable, Generator
from typing import assert_type

from mote_kernel.execution import Graph
from mote_kernel.logging import LoggedGraphCommit, LoggedNode
from mote_kernel.logging.record import LogRecord
from mote_kernel.observability import ObservedNode
from mote_kernel.observability.record import Observation
from mote_kernel.observability.span import Span, SpanContext, SpanId, TraceId


class Sink:
    def write(self, _record: LogRecord, /) -> None:
        pass


class Port:
    def record(self, _observation: Observation, /) -> None:
        pass


class DeferredNumber:
    def __init__(self, value: int) -> None:
        self.value = value

    def __await__(self) -> Generator[None, None, int]:
        yield from ()
        return self.value


def make_span() -> Span:
    return Span(SpanContext(TraceId("trace"), SpanId("span")), "node")


async def text_to_number(value: str) -> int:
    return len(value)


def deferred_number(value: str) -> DeferredNumber:
    return DeferredNumber(len(value))


logged = LoggedNode(Sink())(text_to_number)
observed = ObservedNode(Port(), make_span)(text_to_number)
chained = LoggedNode(Sink())(observed)
deferred = LoggedNode(Sink())(deferred_number)
static_only = LoggedNode(Sink(), fields_factory=None)(text_to_number)

assert_type(logged, Callable[[str], Awaitable[int]])
assert_type(observed, Callable[[str], Awaitable[int]])
assert_type(chained, Callable[[str], Awaitable[int]])
assert_type(deferred, Callable[[str], Awaitable[int]])
assert_type(static_only, Callable[[str], Awaitable[int]])


graph = Graph[str]("typing.logging-observability")


async def graph_node(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(value=values["value"])


async def persistence_commit(transition: Graph.Transition[str], /) -> Graph.State:
    return transition.candidate_state


@LoggedNode(Sink())
async def decorated_graph_node(values: Graph.Values[str]) -> Graph.Values[str]:
    return await graph_node(values)


@ObservedNode(Port(), make_span)
async def observed_graph_node(values: Graph.Values[str]) -> Graph.Values[str]:
    return await graph_node(values)


@LoggedGraphCommit(Sink())
async def decorated_commit(transition: Graph.Transition[str], /) -> Graph.State:
    return await persistence_commit(transition)


graph.add_node(
    "work",
    LoggedNode(Sink())(ObservedNode(Port(), make_span)(graph_node)),
    inputs={"value": Graph.graph_input("value", str)},
    outputs={"value": str},
)
graph.set_outputs({"value": Graph.node_output("work", "value")})


async def run_graph() -> None:
    result = await graph.run(Graph.values(value="input"), commit=LoggedGraphCommit(Sink())(persistence_commit))
    assert_type(result, Graph.Result[str])


async def run_graph_with_execution_fallback() -> None:
    result = await graph.run(Graph.values(value="input"), commit=None)
    assert_type(result, Graph.Result[str])
