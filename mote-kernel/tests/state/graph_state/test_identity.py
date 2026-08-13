import pytest

from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    FailedGraphNode,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphNodeId,
    GraphRunId,
    GraphSkipReason,
    SkippedGraphNode,
    child_graph_run_id,
    graph_interrupt_id,
    skipped_node_ids,
)


@pytest.mark.parametrize(
    ("run_id", "superstep", "node_id", "generation", "expected"),
    [
        ("r", 0, "n", 1, "28:mote.graph-node-interrupt.v11:r1:01:n1:1"),
        ("a:b", 12, "节点", 3, "28:mote.graph-node-interrupt.v13:a:b2:122:节点1:3"),
        ("ab", 3, "c", 11, "28:mote.graph-node-interrupt.v12:ab1:31:c2:11"),
        ("a", 3, "bc", 11, "28:mote.graph-node-interrupt.v11:a1:32:bc2:11"),
        ("é", 0, "e\u0301", 2, "28:mote.graph-node-interrupt.v11:é1:02:e\u03011:2"),
    ],
)
def test_graph_interrupt_id_v1_exact_vectors(
    run_id: str,
    superstep: int,
    node_id: str,
    generation: int,
    expected: str,
) -> None:
    assert graph_interrupt_id(GraphRunId(run_id), superstep, GraphNodeId(node_id), generation) == expected


@pytest.mark.parametrize(
    ("run_id", "superstep", "node_id", "expected"),
    [
        ("r", 0, "n", "23:mote.child-graph-run.v11:r1:01:n"),
        ("a:b", 12, "节点", "23:mote.child-graph-run.v13:a:b2:122:节点"),
        ("ab", 3, "c", "23:mote.child-graph-run.v12:ab1:31:c"),
        ("a", 3, "bc", "23:mote.child-graph-run.v11:a1:32:bc"),
        ("loop", 4, "self", "23:mote.child-graph-run.v14:loop1:44:self"),
        ("loop", 5, "self", "23:mote.child-graph-run.v14:loop1:54:self"),
        ("é", 0, "e\u0301", "23:mote.child-graph-run.v11:é1:02:e\u0301"),
    ],
)
def test_child_graph_run_id_v1_exact_vectors(run_id: str, superstep: int, node_id: str, expected: str) -> None:
    assert child_graph_run_id(GraphRunId(run_id), superstep, GraphNodeId(node_id)) == GraphRunId(expected)


def test_identity_projection_is_stable_and_coordinate_unambiguous() -> None:
    left = child_graph_run_id(GraphRunId("ab"), 3, GraphNodeId("c"))
    right = child_graph_run_id(GraphRunId("a"), 3, GraphNodeId("bc"))
    assert left != right
    assert left == child_graph_run_id(GraphRunId("ab"), 3, GraphNodeId("c"))

    interrupt = graph_interrupt_id(GraphRunId("a:b"), 12, GraphNodeId("节点"), 3)
    assert interrupt == "28:mote.graph-node-interrupt.v13:a:b2:122:节点1:3"
    assert interrupt != graph_interrupt_id(GraphRunId("a"), 12, GraphNodeId("b节点"), 3)
    assert interrupt != graph_interrupt_id(GraphRunId("a:b"), 12, GraphNodeId("节点"), 4)


def test_skipped_node_query_returns_canonical_empty_for_non_skipped_frontier() -> None:
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(GraphNodeId("a"), FailedGraphNode(GraphFailure("x"))),
            GraphFrontierNode(
                GraphNodeId("b"),
                SkippedGraphNode(
                    GraphFailure("y"),
                    GraphSkipReason("skip"),
                    ContinueGraphRouting(),
                ),
            ),
        )
    )
    assert skipped_node_ids(frontier) == (GraphNodeId("b"),)
