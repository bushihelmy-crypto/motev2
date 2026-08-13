import pytest
from tests.execution.graph.factories import graph, node

from mote_kernel.execution.errors import (
    DuplicateBoundaryError,
    DuplicateEdgeError,
    DuplicateNodeError,
    GraphValidationError,
    InvalidGraphIdentityError,
    InvalidResourceDefinitionError,
    MissingEntryError,
    UnknownNodeError,
    UnreachableNodeError,
)
from mote_kernel.execution.graph import (
    END,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
    JoinEdge,
    NodeDefinition,
    ResumeInputBinding,
    compile_graph,
)
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state import GraphResumeInputCodecId


class Codec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


@pytest.mark.parametrize(
    ("definition", "error"),
    [
        (graph(nodes=(node("a"),), entries=()), MissingEntryError),
        (
            GraphDefinition(GraphDefinitionId(""), GraphDefinitionVersion(1), (node("a"),), (), (GraphNodeId("a"),)),
            InvalidGraphIdentityError,
        ),
        (
            GraphDefinition(
                GraphDefinitionId(" test.graph"),
                GraphDefinitionVersion(1),
                (node("a"),),
                (),
                (GraphNodeId("a"),),
            ),
            InvalidGraphIdentityError,
        ),
        (
            GraphDefinition(
                GraphDefinitionId("test.graph"),
                GraphDefinitionVersion(0),
                (node("a"),),
                (),
                (GraphNodeId("a"),),
            ),
            InvalidGraphIdentityError,
        ),
        (graph(nodes=(node(""),), entries=(GraphNodeId(""),)), InvalidGraphIdentityError),
        (graph(nodes=(node(" a"),), entries=(GraphNodeId(" a"),)), InvalidGraphIdentityError),
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
            UnknownNodeError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("missing")),),
            ),
            UnknownNodeError,
        ),
        (graph(nodes=(node("a"), node("b"))), UnreachableNodeError),
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
    ],
)
def test_invalid_graphs_fail_closed(definition: GraphDefinition[str, str], error: type[GraphValidationError]) -> None:
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
        (ResourceDefinition(ResourceId(""), 1),),
        (ResourceDefinition(ResourceId(" file"), 1),),
        (ResourceDefinition(ResourceId("file"), -1),),
        (ResourceDefinition(ResourceId("file"), 1), ResourceDefinition(ResourceId("file"), 2)),
        (ResourceDefinition(ResourceId("file"), 1), ResourceDefinition(ResourceId("database"), 1)),
    ],
)
def test_invalid_resource_definitions_fail_closed(resources: tuple[ResourceDefinition, ...]) -> None:
    with pytest.raises(InvalidResourceDefinitionError):
        compile_graph(graph(nodes=(node("a"),), resources=resources))


def test_node_resource_requirements_must_be_unique_and_declared() -> None:
    declared = (ResourceDefinition(ResourceId("file"), 10),)

    with pytest.raises(InvalidResourceDefinitionError, match="unknown"):
        compile_graph(
            graph(
                nodes=(NodeDefinition(GraphNodeId("a"), node("a").node, (ResourceId("database"),)),),
                resources=declared,
            )
        )
    with pytest.raises(InvalidResourceDefinitionError, match="repeats"):
        compile_graph(
            graph(
                nodes=(NodeDefinition(GraphNodeId("a"), node("a").node, (ResourceId("file"), ResourceId("file"))),),
                resources=declared,
            )
        )


def test_resume_input_codec_version_must_be_positive() -> None:
    codec = Codec()
    with pytest.raises(InvalidGraphIdentityError, match="codec version"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("graph"),
                GraphDefinitionVersion(1),
                (node("a"),),
                (),
                (GraphNodeId("a"),),
                resume_input=ResumeInputBinding(GraphResumeInputCodecId("input"), 0, codec, codec),
            )
        )
