import pytest
from tests.execution.graph.factories import graph, node

from mote_kernel.execution.errors import (
    DuplicateBoundaryError,
    DuplicateEdgeError,
    DuplicateNodeError,
    GraphValidationError,
    InvalidGraphIdentityError,
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
    NodeId,
    RouteId,
    compile_graph,
)


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
