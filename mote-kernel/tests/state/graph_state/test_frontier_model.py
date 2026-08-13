from dataclasses import FrozenInstanceError

import pytest

from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    FailedGraphNode,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphFrontierStatus,
    GraphInterruptPayload,
    GraphNodeId,
    GraphNodeInterrupt,
    GraphNodeSettlement,
    GraphResumeInputPayload,
    GraphRunId,
    GraphSkipReason,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    PendingGraphNode,
    SkippedGraphNode,
    SucceededGraphNode,
    UseStepRequestInput,
    derive_graph_node_interrupt_identity,
    failed_node_ids,
    frontier_node,
    frontier_status,
    interrupted_node_ids,
    pending_node_ids,
    routing_contributions,
    skipped_node_ids,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
D = GraphNodeId("d")
E = GraphNodeId("e")


def interrupt(node_id: GraphNodeId = C) -> InterruptedGraphNode:
    identity = derive_graph_node_interrupt_identity(GraphRunId("run"), 4, node_id, 2)
    return InterruptedGraphNode(GraphNodeInterrupt(identity, GraphInterruptPayload(b"question")))


def test_pending_has_priority_over_failed_and_interrupted_status() -> None:
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput())),
            GraphFrontierNode(B, FailedGraphNode(GraphFailure("failed"))),
            GraphFrontierNode(C, interrupt()),
        )
    )

    assert frontier_status(frontier) is GraphFrontierStatus.EXECUTABLE


@pytest.mark.parametrize(
    "settlement",
    [
        FailedGraphNode(GraphFailure("failed")),
        interrupt(A),
    ],
)
def test_no_pending_with_recoverable_settlement_is_awaiting_resume(settlement: GraphNodeSettlement) -> None:
    frontier = GraphFrontierState((GraphFrontierNode(A, settlement),))

    assert frontier_status(frontier) is GraphFrontierStatus.AWAITING_RESUME


def test_success_and_skip_only_frontier_is_settled() -> None:
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting())),
            GraphFrontierNode(
                B,
                SkippedGraphNode(
                    GraphFailure("failed"),
                    GraphSkipReason("operator"),
                    ContinueGraphRouting(),
                ),
            ),
        )
    )

    assert frontier_status(frontier) is GraphFrontierStatus.SETTLED


def test_empty_frontier_has_no_derived_status() -> None:
    with pytest.raises(ValueError, match="no valid derived status"):
        frontier_status(GraphFrontierState(()))


def test_frontier_queries_preserve_canonical_node_order_by_settlement() -> None:
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"a")))),
            GraphFrontierNode(B, SucceededGraphNode(ContinueGraphRouting())),
            GraphFrontierNode(C, FailedGraphNode(GraphFailure("c failed"))),
            GraphFrontierNode(D, interrupt(D)),
            GraphFrontierNode(
                E,
                SkippedGraphNode(GraphFailure("e failed"), GraphSkipReason("skip"), ContinueGraphRouting()),
            ),
        )
    )

    assert pending_node_ids(frontier) == (A,)
    assert failed_node_ids(frontier) == (C,)
    assert interrupted_node_ids(frontier) == (D,)
    assert skipped_node_ids(frontier) == (E,)


def test_frontier_node_returns_exact_value_or_none() -> None:
    expected = GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()))
    frontier = GraphFrontierState((expected,))

    assert frontier_node(frontier, A) is expected
    assert frontier_node(frontier, B) is None


def test_routing_contributions_include_success_and_skip_but_not_unsettled_nodes() -> None:
    success = SucceededGraphNode(ContinueGraphRouting())
    skipped = SkippedGraphNode(GraphFailure("failed"), GraphSkipReason("skip"), ContinueGraphRouting())
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput())),
            GraphFrontierNode(B, success),
            GraphFrontierNode(C, FailedGraphNode(GraphFailure("failed"))),
            GraphFrontierNode(D, interrupt(D)),
            GraphFrontierNode(E, skipped),
        )
    )

    assert routing_contributions(frontier) == ((B, success.routing), (E, skipped.routing))


def test_frontier_and_nested_settlements_are_deeply_immutable() -> None:
    node = GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()))
    frontier = GraphFrontierState((node,))

    with pytest.raises(FrozenInstanceError):
        node.node_id = B  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        frontier.nodes = ()  # type: ignore[misc]
