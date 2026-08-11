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
    JoinEdge,
    NodeDefinition,
    NodeId,
    ResolutionBinding,
    ResolutionCodecId,
    RouteId,
    compile_graph,
)
from mote_kernel.parallel import ResourceDefinition, ResourceId


class Decoder:
    def decode(self, payload: bytes) -> str:
        return payload.decode()


@pytest.mark.parametrize(
    ("definition", "error"),
    [
        (graph(nodes=(node("a"),), entries=()), MissingEntryError),
        (
            GraphDefinition(GraphDefinitionId(""), GraphDefinitionVersion(1), (node("a"),), (), (NodeId("a"),)),
            InvalidGraphIdentityError,
        ),
        (
            GraphDefinition(
                GraphDefinitionId(" test.graph"),
                GraphDefinitionVersion(1),
                (node("a"),),
                (),
                (NodeId("a"),),
            ),
            InvalidGraphIdentityError,
        ),
        (
            GraphDefinition(
                GraphDefinitionId("test.graph"),
                GraphDefinitionVersion(0),
                (node("a"),),
                (),
                (NodeId("a"),),
            ),
            InvalidGraphIdentityError,
        ),
        (graph(nodes=(node(""),), entries=(NodeId(""),)), InvalidGraphIdentityError),
        (graph(nodes=(node(" a"),), entries=(NodeId(" a"),)), InvalidGraphIdentityError),
        (graph(nodes=(node(END),), entries=(END,)), InvalidGraphIdentityError),
        (graph(nodes=(node("a"), node("a"))), DuplicateNodeError),
        (graph(nodes=(node("a"),), entries=(NodeId("a"), NodeId("a"))), DuplicateBoundaryError),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(DirectEdge(NodeId("a"), NodeId("b")), DirectEdge(NodeId("a"), NodeId("b"))),
            ),
            DuplicateEdgeError,
        ),
        (graph(nodes=(node("a"),), entries=(NodeId("missing"),)), UnknownNodeError),
        (graph(nodes=(node("a"),), edges=(DirectEdge(NodeId("a"), NodeId("missing")),)), UnknownNodeError),
        (graph(nodes=(node("a"),), edges=(DirectEdge(NodeId("missing"), NodeId("a")),)), UnknownNodeError),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(ConditionalEdge(NodeId("missing"), RouteId("next"), NodeId("b")),),
            ),
            UnknownNodeError,
        ),
        (
            graph(
                nodes=(node("a"),),
                edges=(ConditionalEdge(NodeId("a"), RouteId("next"), NodeId("missing")),),
            ),
            UnknownNodeError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(JoinEdge((NodeId("a"), NodeId("missing")), NodeId("b")),),
            ),
            UnknownNodeError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(JoinEdge((NodeId("a"), NodeId("b")), NodeId("missing")),),
            ),
            UnknownNodeError,
        ),
        (graph(nodes=(node("a"), node("b"))), UnreachableNodeError),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(ConditionalEdge(NodeId("a"), RouteId(""), NodeId("b")),),
            ),
            InvalidGraphIdentityError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(ConditionalEdge(NodeId("a"), RouteId(" next"), NodeId("b")),),
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
            ConditionalEdge(NodeId("a"), RouteId("next"), NodeId("b")),
            ConditionalEdge(NodeId("a"), RouteId("next"), NodeId("c")),
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
                nodes=(NodeDefinition(NodeId("a"), node("a").node, (ResourceId("database"),)),),
                resources=declared,
            )
        )
    with pytest.raises(InvalidResourceDefinitionError, match="repeats"):
        compile_graph(
            graph(
                nodes=(NodeDefinition(NodeId("a"), node("a").node, (ResourceId("file"), ResourceId("file"))),),
                resources=declared,
            )
        )


def test_resolution_codec_version_must_be_positive() -> None:
    with pytest.raises(InvalidGraphIdentityError, match="codec version"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("graph"),
                GraphDefinitionVersion(1),
                (node("a"),),
                (),
                (NodeId("a"),),
                resolution=ResolutionBinding(ResolutionCodecId("input"), 0, Decoder()),
            )
        )
