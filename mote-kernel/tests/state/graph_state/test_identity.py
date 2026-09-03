from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    ActivationReference,
    GraphActivationIdentity,
    GraphJoinIdentity,
    GraphJoinOccurrenceIdentity,
    GraphNodeId,
    GraphRouteId,
    GraphRunId,
    child_graph_run_id,
    graph_interrupt_id,
)


@pytest.mark.parametrize(
    ("run_id", "superstep", "node_id"),
    [
        (GraphRunId(""), 0, GraphNodeId("node")),
        (GraphRunId("run"), -1, GraphNodeId("node")),
        (GraphRunId("run"), cast(int, True), GraphNodeId("node")),
        (GraphRunId("run"), 0, GraphNodeId(" node")),
    ],
)
def test_graph_activation_identity_rejects_noncanonical_coordinates(
    run_id: GraphRunId,
    superstep: int,
    node_id: GraphNodeId,
) -> None:
    with pytest.raises(ValueError):
        GraphActivationIdentity(run_id, superstep, node_id)


def test_activation_reference_rejects_invalid_identity_and_route() -> None:
    activation = GraphActivationIdentity(GraphRunId("run"), 0, GraphNodeId("node"))

    with pytest.raises(ValueError, match="GraphActivationIdentity"):
        ActivationReference(cast(GraphActivationIdentity, object()))
    with pytest.raises(ValueError, match="route"):
        ActivationReference(activation, GraphRouteId(" route"))


@pytest.mark.parametrize(
    ("sources", "target"),
    [
        ((GraphNodeId("a"),), GraphNodeId("c")),
        ((GraphNodeId("a"), GraphNodeId("a")), GraphNodeId("c")),
        ((GraphNodeId("b"), GraphNodeId("a")), GraphNodeId("c")),
        ((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("a")),
    ],
)
def test_join_identity_rejects_noncanonical_shapes(
    sources: tuple[GraphNodeId, ...],
    target: GraphNodeId,
) -> None:
    with pytest.raises(ValueError):
        GraphJoinIdentity(sources, target)


def test_join_occurrence_identity_is_run_and_target_coordinate_specific() -> None:
    join = GraphJoinIdentity((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("c"))
    first = GraphJoinOccurrenceIdentity(join, GraphRunId("run"), 2)

    assert first != GraphJoinOccurrenceIdentity(join, GraphRunId("run"), 3)
    assert first != GraphJoinOccurrenceIdentity(join, GraphRunId("other"), 2)
    with pytest.raises(ValueError, match="GraphJoinIdentity"):
        GraphJoinOccurrenceIdentity(cast(GraphJoinIdentity, object()), GraphRunId("run"), 2)
    with pytest.raises(ValueError, match="run_id"):
        GraphJoinOccurrenceIdentity(join, GraphRunId(" bad"), 2)
    with pytest.raises(ValueError, match="positive"):
        GraphJoinOccurrenceIdentity(join, GraphRunId("run"), 0)


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
