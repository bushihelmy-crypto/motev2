import pytest
from tests.execution.graph.factories import graph, node

from mote_kernel.execution.errors import InvalidJoinError, UnreachableNodeError
from mote_kernel.execution.graph import DirectEdge, JoinEdge, NodeId, compile_graph


@pytest.mark.parametrize(
    "edge",
    [
        JoinEdge((NodeId("a"), NodeId("a")), NodeId("b")),
        JoinEdge((NodeId("a"),), NodeId("b")),
        JoinEdge((NodeId("a"), NodeId("b")), NodeId("b")),
    ],
)
def test_invalid_join_shapes_fail_closed(edge: JoinEdge) -> None:
    with pytest.raises(InvalidJoinError):
        compile_graph(graph(nodes=(node("a"), node("b")), edges=(edge,)))


def test_duplicate_join_fails_closed_regardless_of_source_order() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            JoinEdge((NodeId("a"), NodeId("b")), NodeId("c")),
            JoinEdge((NodeId("b"), NodeId("a")), NodeId("c")),
        ),
    )

    with pytest.raises(InvalidJoinError):
        compile_graph(definition)


def test_direct_edges_and_multiple_joins_coexist_deterministically() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d"), node("e")),
        edges=(
            JoinEdge((NodeId("c"), NodeId("a")), NodeId("e")),
            DirectEdge(NodeId("a"), NodeId("c")),
            JoinEdge((NodeId("b"), NodeId("a")), NodeId("d")),
            DirectEdge(NodeId("a"), NodeId("b")),
        ),
    )
    compiled = compile_graph(definition)

    assert compiled.direct_targets[NodeId("a")] == (NodeId("b"), NodeId("c"))
    assert compiled.joins_by_source[NodeId("a")] == (
        JoinEdge((NodeId("a"), NodeId("b")), NodeId("d")),
        JoinEdge((NodeId("a"), NodeId("c")), NodeId("e")),
    )


def test_distinct_joins_may_share_a_target() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d")),
        edges=(
            JoinEdge((NodeId("a"), NodeId("b")), NodeId("d")),
            JoinEdge((NodeId("a"), NodeId("c")), NodeId("d")),
        ),
        entries=(NodeId("a"), NodeId("b"), NodeId("c")),
    )
    compiled = compile_graph(definition)

    assert compiled.joins_by_source[NodeId("a")] == (
        JoinEdge((NodeId("a"), NodeId("b")), NodeId("d")),
        JoinEdge((NodeId("a"), NodeId("c")), NodeId("d")),
    )


def test_join_target_requires_every_source_to_be_structurally_reachable() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(JoinEdge((NodeId("a"), NodeId("b")), NodeId("c")), DirectEdge(NodeId("c"), NodeId("b"))),
    )

    with pytest.raises(UnreachableNodeError):
        compile_graph(definition)


def test_join_reachability_reaches_fixed_point() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d"), node("e")),
        edges=(
            JoinEdge((NodeId("c"), NodeId("d")), NodeId("e")),
            JoinEdge((NodeId("a"), NodeId("b")), NodeId("c")),
            DirectEdge(NodeId("c"), NodeId("d")),
        ),
        entries=(NodeId("a"), NodeId("b")),
    )

    assert compile_graph(definition).joins_by_source[NodeId("c")] == (
        JoinEdge((NodeId("c"), NodeId("d")), NodeId("e")),
    )
