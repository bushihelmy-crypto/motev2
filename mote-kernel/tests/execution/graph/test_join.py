import pytest
from tests.execution.graph.factories import graph, node

from mote_kernel.execution.errors import GraphValidationError, InvalidJoinError, UnreachableNodeError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.edge import DirectEdge, JoinEdge
from mote_kernel.state.graph_state import GraphNodeId


@pytest.mark.parametrize(
    "edge",
    [
        JoinEdge((GraphNodeId("a"), GraphNodeId("a")), GraphNodeId("b")),
        JoinEdge((GraphNodeId("a"),), GraphNodeId("b")),
        JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("b")),
    ],
)
def test_invalid_join_shapes_fail_closed(edge: JoinEdge) -> None:
    with pytest.raises(InvalidJoinError):
        compile_graph(graph(nodes=(node("a"), node("b")), edges=(edge,)))


def test_duplicate_join_fails_closed_regardless_of_source_order() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("c")),
            JoinEdge((GraphNodeId("b"), GraphNodeId("a")), GraphNodeId("c")),
        ),
    )

    with pytest.raises(InvalidJoinError):
        compile_graph(definition)


def test_direct_edges_and_multiple_joins_coexist_deterministically() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d"), node("e")),
        edges=(
            JoinEdge((GraphNodeId("c"), GraphNodeId("a")), GraphNodeId("e")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("c")),
            JoinEdge((GraphNodeId("b"), GraphNodeId("a")), GraphNodeId("d")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
        ),
    )
    compiled = compile_graph(definition)

    assert compiled.transition.direct_targets[GraphNodeId("a")] == (GraphNodeId("b"), GraphNodeId("c"))
    assert compiled.transition.joins_by_source[GraphNodeId("a")] == (
        JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("d")),
        JoinEdge((GraphNodeId("a"), GraphNodeId("c")), GraphNodeId("e")),
    )


def test_distinct_joins_sharing_a_target_are_rejected_without_occurrence_identity() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d")),
        edges=(
            JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("d")),
            JoinEdge((GraphNodeId("a"), GraphNodeId("c")), GraphNodeId("d")),
        ),
    )
    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        compile_graph(definition)


def test_join_target_requires_every_source_to_be_structurally_reachable() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("c")),
            DirectEdge(GraphNodeId("c"), GraphNodeId("b")),
        ),
    )

    with pytest.raises(UnreachableNodeError):
        compile_graph(definition)


def test_join_reachability_reaches_fixed_point() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d"), node("e")),
        edges=(
            JoinEdge((GraphNodeId("c"), GraphNodeId("d")), GraphNodeId("e")),
            JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("c")),
            DirectEdge(GraphNodeId("c"), GraphNodeId("d")),
        ),
    )

    assert compile_graph(definition).transition.joins_by_source[GraphNodeId("c")] == (
        JoinEdge((GraphNodeId("c"), GraphNodeId("d")), GraphNodeId("e")),
    )
