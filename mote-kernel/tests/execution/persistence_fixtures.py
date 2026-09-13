"""Deterministic codec, graph and persistence fixtures for contract tests."""

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Generic, TypeVar, cast

import mote_kernel.execution.persistence as persistence_module
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.identity import ScopeRunCoordinate, root_scope_run
from mote_kernel.execution.persistence import GraphCheckpoint, GraphPersistenceCommit
from mote_kernel.execution.run_context import ScopedStateBinding, UncreatedGraphRun
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId, GraphRunState

GraphValueT = TypeVar("GraphValueT")
capture_graph_input = persistence_module._capture_graph_input  # pyright: ignore[reportPrivateUsage]
capture_publication = persistence_module._capture_publication  # pyright: ignore[reportPrivateUsage]


def encode_strings(values: Graph.Values[str]) -> bytes:
    return json.dumps(dict(values.items()), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def decode_strings(payload: bytes) -> Graph.Values[str]:
    parsed: object = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("string frame must be an object")
    entries = cast(dict[object, object], parsed)
    values: dict[str, str] = {}
    for name, value in entries.items():
        if type(name) is not str or type(value) is not str:
            raise ValueError("string frame requires exact string names and values")
        values[name] = value
    return Graph.values(**values)


STRING_CODEC = FrameCodec("test.string-frame", 1, encode_strings, decode_strings)


def linear_graph(calls: list[str], *, version: int = 1) -> Graph[str]:
    graph = Graph[str]("persistent-chain", version=version)

    async def first(inputs: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append("first")
        return Graph.success(Graph.values(value=inputs["value"] + "-first"))

    async def second(inputs: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append("second")
        return Graph.success(Graph.values(value=inputs["value"] + "-second"))

    graph.add_node("first", first, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_node("second", second, inputs={"value": graph.node_output("first", "value")}, outputs={"value": str})
    graph.add_edge(Graph.START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", Graph.END)
    graph.set_outputs({"value": graph.output_ref("second", "value")})
    return graph


def nested_graph(calls: list[str], *, depth: int = 1) -> Graph[str]:
    graph = linear_graph(calls)
    for level in range(depth):
        parent = Graph[str](f"persistent-parent-{level}")
        parent.add_node("child", graph, inputs={"value": parent.graph_input("value", str)})
        parent.add_edge(Graph.START, "child")
        parent.add_edge("child", Graph.END)
        parent.set_outputs({"value": parent.output_ref("child", "value")})
        graph = parent
    return graph


def interrupt_graph(calls: list[str]) -> Graph[str]:
    graph = Graph[str]("persistent-interrupt")

    async def operation(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        calls.append(values["value"])
        return Graph.interrupt(b"question") if values["value"] == "question" else values

    graph.set_resume_codec(STRING_CODEC.codec_id, STRING_CODEC.version, STRING_CODEC.encoder, STRING_CODEC.decoder)
    graph.add_node("ask", operation, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.set_outputs({"value": graph.output_ref("ask", "value")})
    return graph


class MemoryPersistence(Generic[GraphValueT]):
    def __init__(self) -> None:
        self.requests: list[GraphPersistenceCommit[GraphValueT]] = []
        self.states: dict[ScopeRunCoordinate, GraphRunState] = {}
        self.fail_when: Callable[[GraphPersistenceCommit[GraphValueT]], bool] | None = None
        self.unavailable = False

    async def __call__(self, request: GraphPersistenceCommit[GraphValueT], /) -> GraphPersistenceCommit[GraphValueT]:
        if self.unavailable or (self.fail_when is not None and self.fail_when(request)):
            self.unavailable = True
            raise OSError("injected persistence outage")
        for existing in self.requests:
            if (existing.scope, existing.writes.commit_key) == (request.scope, request.writes.commit_key):
                if existing != request:
                    raise ValueError("same commit key has different content")
                return deepcopy(existing)
        coordinate = ScopeRunCoordinate(
            tuple(GraphNodeId(part) for part in request.scope), request.candidate_state.run_id
        )
        previous = self.states.get(coordinate)
        if request.expected_revision != (previous.revision if previous is not None else None):
            raise ValueError("unexpected revision")
        self.requests.append(deepcopy(request))
        self.states[coordinate] = deepcopy(request.candidate_state)
        return deepcopy(request)

    def checkpoint(
        self,
        run_id: str = "run",
        *,
        child_reads: tuple[ScopeRunCoordinate, ...] = (),
    ) -> GraphCheckpoint[GraphValueT]:
        root = root_scope_run(GraphRunId(run_id))
        inputs = tuple(record for request in self.requests for record in request.writes.graph_inputs)
        publications = tuple(record for request in self.requests for record in request.writes.publications)
        return GraphCheckpoint(
            self.states[root],
            tuple(
                sorted(
                    (
                        *(
                            ScopedStateBinding(coordinate, state)
                            for coordinate, state in self.states.items()
                            if coordinate != root
                        ),
                        *(UncreatedGraphRun(coordinate) for coordinate in child_reads if coordinate not in self.states),
                    ),
                    key=lambda record: record.scope_run,
                )
            ),
            tuple(sorted(inputs, key=lambda record: record.coordinate)),
            tuple(sorted(publications, key=lambda record: record.coordinate)),
            self.requests[-1].agent_session if self.requests else None,
        )

    def reopen(self) -> None:
        self.unavailable = False
        self.fail_when = None
