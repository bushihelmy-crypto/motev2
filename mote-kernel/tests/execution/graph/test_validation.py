from dataclasses import replace

import pytest
from tests.execution.graph.factories import graph, node

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import (
    DuplicateBoundaryError,
    DuplicateEdgeError,
    DuplicateNodeError,
    GraphValidationError,
    InvalidGraphIdentityError,
    InvalidJoinError,
    InvalidResourceDefinitionError,
    MissingEntryError,
    UnknownNodeError,
    UnreachableNodeError,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END, START
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import normalize_graph_output_declarations
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphRouteId,
)


class Codec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


def definition(
    definition_id: str,
    version: int,
    *,
    nodes: tuple[CallableNodeDefinition[str], ...] = (),
    edges: tuple[Edge, ...] = (),
    entries: tuple[GraphNodeId, ...] = (),
    resume_input: ResumeInputBinding[str] | None = None,
) -> GraphDefinition[str]:
    return GraphDefinition(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(version),
        nodes,
        edges,
        entries,
        normalize_graph_output_declarations({}),
        resume_input=resume_input,
    )


@pytest.mark.parametrize(
    ("definition", "error"),
    [
        (
            graph(
                nodes=(node("a"),),
                edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("a")),),
                entries=(),
            ),
            MissingEntryError,
        ),
        (
            definition("", 1, nodes=(node("a"),)),
            InvalidGraphIdentityError,
        ),
        (
            definition(" test.graph", 1, nodes=(node("a"),)),
            InvalidGraphIdentityError,
        ),
        (
            definition("test.graph", 0, nodes=(node("a"),)),
            InvalidGraphIdentityError,
        ),
        (graph(nodes=(node(""),), entries=(GraphNodeId(""),)), InvalidGraphIdentityError),
        (graph(nodes=(node(" a"),), entries=(GraphNodeId(" a"),)), InvalidGraphIdentityError),
        (graph(nodes=(node(START),), entries=(START,)), InvalidGraphIdentityError),
        (graph(nodes=(node(END),), entries=(END,)), InvalidGraphIdentityError),
        (graph(nodes=(node("a"), node("a"))), DuplicateNodeError),
        (graph(nodes=(node("a"),), entries=(GraphNodeId("a"), GraphNodeId("a"))), DuplicateBoundaryError),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")), DirectEdge(GraphNodeId("a"), GraphNodeId("b"))),
            ),
            DuplicateEdgeError,
        ),
        (graph(nodes=(node("a"),), entries=(GraphNodeId("missing"),)), UnknownNodeError),
        (graph(nodes=(node("a"),), edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("missing")),)), UnknownNodeError),
        (graph(nodes=(node("a"),), edges=(DirectEdge(GraphNodeId("missing"), GraphNodeId("a")),)), UnknownNodeError),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(ConditionalEdge(GraphNodeId("missing"), GraphRouteId("next"), GraphNodeId("b")),),
            ),
            UnknownNodeError,
        ),
        (
            graph(
                nodes=(node("a"),),
                edges=(ConditionalEdge(GraphNodeId("a"), GraphRouteId("next"), GraphNodeId("missing")),),
            ),
            UnknownNodeError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(JoinEdge((GraphNodeId("a"), GraphNodeId("missing")), GraphNodeId("b")),),
            ),
            InvalidJoinError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("missing")),),
            ),
            InvalidJoinError,
        ),
        (
            graph(
                nodes=(node("a"), node("b"), node("c")),
                edges=(
                    DirectEdge(GraphNodeId("b"), GraphNodeId("c")),
                    DirectEdge(GraphNodeId("c"), GraphNodeId("b")),
                ),
            ),
            UnreachableNodeError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(ConditionalEdge(GraphNodeId("a"), GraphRouteId(""), GraphNodeId("b")),),
            ),
            InvalidGraphIdentityError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(ConditionalEdge(GraphNodeId("a"), GraphRouteId(" next"), GraphNodeId("b")),),
            ),
            InvalidGraphIdentityError,
        ),
        pytest.param(
            graph(
                nodes=(node("a"),),
                edges=(DirectEdge(GraphNodeId("missing"), END),),
            ),
            UnknownNodeError,
            id="definition16-UnknownNodeError",
        ),
        pytest.param(
            graph(
                nodes=(node("a"),),
                edges=(ConditionalEdge(GraphNodeId("missing"), GraphRouteId("next"), END),),
            ),
            UnknownNodeError,
            id="definition17-UnknownNodeError",
        ),
    ],
)
def test_invalid_graphs_fail_closed(definition: GraphDefinition[str], error: type[GraphValidationError]) -> None:
    with pytest.raises(error):
        compile_graph(definition)


def test_duplicate_conditional_route_fails_closed() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("next"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("next"), GraphNodeId("c")),
        ),
    )

    with pytest.raises(DuplicateEdgeError):
        compile_graph(definition)


@pytest.mark.parametrize(
    "resources",
    [
        (ResourceDefinition(ResourceId(""), 0),),
        (ResourceDefinition(ResourceId(" file"), 0),),
        (ResourceDefinition(ResourceId("file"), -1),),
        (ResourceDefinition(ResourceId("file"), 0), ResourceDefinition(ResourceId("file"), 1)),
        (ResourceDefinition(ResourceId("file"), 1), ResourceDefinition(ResourceId("database"), 1)),
    ],
)
def test_invalid_resource_definitions_fail_closed(resources: tuple[ResourceDefinition, ...]) -> None:
    with pytest.raises(InvalidResourceDefinitionError):
        compile_graph(graph(nodes=(node("a"),), resources=resources))


def test_node_resource_requirements_must_be_unique_and_declared() -> None:
    declared = (ResourceDefinition(ResourceId("file"), 0),)

    with pytest.raises(InvalidResourceDefinitionError, match="unknown"):
        compile_graph(
            graph(
                nodes=(replace(node("a"), resources=(ResourceId("database"),)),),
                resources=declared,
            )
        )
    with pytest.raises(InvalidResourceDefinitionError, match="repeats"):
        compile_graph(
            graph(
                nodes=(replace(node("a"), resources=(ResourceId("file"), ResourceId("file"))),),
                resources=declared,
            )
        )


def test_resume_input_codec_version_must_be_positive() -> None:
    codec = Codec()
    with pytest.raises(InvalidGraphIdentityError, match="codec version"):
        compile_graph(
            definition(
                "graph",
                1,
                nodes=(node("a"),),
                resume_input=ResumeInputBinding(GraphResumeInputCodecId("input"), 0, codec, codec),
            )
        )
