from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    ActivationReference,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphActivationIdentity,
    GraphFailure,
    GraphFrontierActivation,
    GraphFrontierNode,
    GraphFrontierState,
    GraphFrontierStatus,
    GraphInterruptPayload,
    GraphJoinIdentity,
    GraphJoinOccurrenceIdentity,
    GraphNodeId,
    GraphNodeInterrupt,
    GraphNodeInterruptIdentity,
    GraphResumeInputPayload,
    GraphRunId,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    PendingGraphNode,
    RoutedActivationCause,
    StartActivationCause,
    SucceededGraphNode,
    UseStepRequestInput,
    frontier_node,
    frontier_status,
    interrupted_node_ids,
    pending_node_ids,
    routing_contributions,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
D = GraphNodeId("d")
E = GraphNodeId("e")


def interrupt(node_id: GraphNodeId = C) -> InterruptedGraphNode:
    identity = GraphNodeInterruptIdentity(GraphRunId("run"), 4, node_id, 2)
    return InterruptedGraphNode(GraphNodeInterrupt(identity, GraphInterruptPayload(b"question")))


def test_pending_has_priority_over_failed_and_interrupted_status() -> None:
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()), StartActivationCause()),
            GraphFrontierNode(B, FailedGraphNode(GraphFailure("failed")), StartActivationCause()),
            GraphFrontierNode(C, interrupt(), StartActivationCause()),
        )
    )

    assert frontier_status(frontier) is GraphFrontierStatus.EXECUTABLE


def test_no_pending_with_failure_is_failed() -> None:
    frontier = GraphFrontierState(
        (GraphFrontierNode(A, FailedGraphNode(GraphFailure("failed")), StartActivationCause()),)
    )

    assert frontier_status(frontier) is GraphFrontierStatus.FAILED


def test_no_pending_with_interrupt_is_awaiting_resume() -> None:
    frontier = GraphFrontierState((GraphFrontierNode(A, interrupt(A), StartActivationCause()),))

    assert frontier_status(frontier) is GraphFrontierStatus.AWAITING_RESUME


def test_success_only_frontier_is_settled() -> None:
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),
            GraphFrontierNode(B, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),
        )
    )

    assert frontier_status(frontier) is GraphFrontierStatus.SETTLED


def test_empty_frontier_has_no_derived_status() -> None:
    with pytest.raises(ValueError, match="no valid derived status"):
        frontier_status(GraphFrontierState(()))


def test_frontier_queries_preserve_canonical_node_order_by_settlement() -> None:
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(
                A, PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"a"))), StartActivationCause()
            ),
            GraphFrontierNode(B, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),
            GraphFrontierNode(C, FailedGraphNode(GraphFailure("c failed")), StartActivationCause()),
            GraphFrontierNode(D, interrupt(D), StartActivationCause()),
            GraphFrontierNode(E, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),
        )
    )

    assert pending_node_ids(frontier) == (A,)
    assert interrupted_node_ids(frontier) == (D,)


def test_frontier_node_returns_exact_value_or_none() -> None:
    expected = GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()), StartActivationCause())
    frontier = GraphFrontierState((expected,))

    assert frontier_node(frontier, A) is expected
    assert frontier_node(frontier, B) is None


def test_routing_contributions_include_only_successful_nodes() -> None:
    success = SucceededGraphNode(ContinueGraphRouting())
    other_success = SucceededGraphNode(ContinueGraphRouting())
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()), StartActivationCause()),
            GraphFrontierNode(B, success, StartActivationCause()),
            GraphFrontierNode(C, FailedGraphNode(GraphFailure("failed")), StartActivationCause()),
            GraphFrontierNode(D, interrupt(D), StartActivationCause()),
            GraphFrontierNode(E, other_success, StartActivationCause()),
        )
    )

    assert routing_contributions(frontier) == ((B, success.routing), (E, other_success.routing))


def test_frontier_and_nested_settlements_are_deeply_immutable() -> None:
    node = GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()), StartActivationCause())
    frontier = GraphFrontierState((node,))

    with pytest.raises(FrozenInstanceError):
        node.node_id = B  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        frontier.nodes = ()  # type: ignore[misc]


def test_activation_value_objects_reject_malformed_causes() -> None:
    reference = ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, A))
    other = ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, B))
    occurrence = GraphJoinOccurrenceIdentity(GraphJoinIdentity((A, B), C), GraphRunId("run"), 1)

    with pytest.raises(ValueError, match="at least one"):
        RoutedActivationCause(())
    with pytest.raises(ValueError, match="typed values"):
        RoutedActivationCause((cast(ActivationReference, object()),))
    with pytest.raises(ValueError, match="canonical and distinct"):
        RoutedActivationCause((reference, reference))
    with pytest.raises(ValueError, match="non-Join"):
        RoutedActivationCause((reference, other))
    assert RoutedActivationCause((reference, other), occurrence).join_occurrence == occurrence
    with pytest.raises(ValueError, match="GraphJoinOccurrenceIdentity"):
        RoutedActivationCause((reference,), cast(GraphJoinOccurrenceIdentity, object()))
    with pytest.raises(ValueError, match="exactly one reference per source"):
        RoutedActivationCause((reference,), occurrence)
    stale = GraphJoinOccurrenceIdentity(GraphJoinIdentity((A, B), C), GraphRunId("run"), 1)
    stale_references = (
        ActivationReference(GraphActivationIdentity(GraphRunId("run"), 1, A)),
        ActivationReference(GraphActivationIdentity(GraphRunId("run"), 1, B)),
    )
    with pytest.raises(ValueError, match="precede"):
        RoutedActivationCause(stale_references, stale)
    with pytest.raises(ValueError, match="node_id"):
        GraphFrontierActivation(GraphNodeId(" bad"), StartActivationCause())
    with pytest.raises(ValueError, match="unsupported variant"):
        GraphFrontierActivation(A, cast(StartActivationCause, object()))
