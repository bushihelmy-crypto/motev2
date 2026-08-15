from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    ContinueGraphRouting,
    FailedGraphNodeOutcome,
    FenceGraphExecution,
    GraphAbortReason,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphNodeId,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    GraphStateTransitionError,
    ResumeFailedNode,
    ResumeGraphNodes,
    SettleGraphNode,
    SkipFailedNode,
    StartGraphRun,
    SucceededGraphNodeOutcome,
    UseStepRequestInput,
    reduce_graph_run,
)

A = GraphNodeId("a")


def start() -> GraphRunState:
    return reduce_graph_run(
        None,
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), (A,)),
    )


def test_start_is_the_only_command_allowed_without_existing_state() -> None:
    state = start()
    with pytest.raises(GraphStateTransitionError, match="existing"):
        reduce_graph_run(
            state,
            StartGraphRun(
                GraphRunId("other"),
                GraphDefinitionId("graph"),
                GraphDefinitionVersion(1),
                (A,),
            ),
        )
    with pytest.raises(GraphStateTransitionError, match="started"):
        reduce_graph_run(None, cast(GraphRunCommand, ClaimGraphExecution(0, GraphExecutionAttemptId("a"), None)))


def test_every_command_increments_exactly_one_revision_and_is_pure() -> None:
    initial = start()
    claimed = reduce_graph_run(initial, ClaimGraphExecution(0, GraphExecutionAttemptId("a"), None))
    assert claimed.revision == 1 and initial.revision == 0
    assert initial.execution is None
    assert claimed.execution is not None
    settled = reduce_graph_run(
        claimed,
        SettleGraphNode(1, claimed.execution.token, SucceededGraphNodeOutcome(A, ContinueGraphRouting())),
    )
    assert settled.revision == 2
    with pytest.raises(FrozenInstanceError):
        claimed.revision = 99  # type: ignore[misc]


def test_stale_revision_is_rejected_before_transition() -> None:
    state = start()
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(state, ClaimGraphExecution(1, GraphExecutionAttemptId("a"), None))


def test_single_node_settlement_dispatches_success_and_failure() -> None:
    claimed = reduce_graph_run(start(), ClaimGraphExecution(0, GraphExecutionAttemptId("a"), None))
    assert claimed.execution is not None
    failed = reduce_graph_run(
        claimed,
        SettleGraphNode(1, claimed.execution.token, FailedGraphNodeOutcome(A, GraphFailure("failed"))),
    )
    assert failed.status is GraphRunStatus.RUNNING
    assert failed.revision == 2


def test_fence_and_abort_are_closed_union_transitions() -> None:
    claimed = reduce_graph_run(start(), ClaimGraphExecution(0, GraphExecutionAttemptId("a"), None))
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(1, claimed.execution.token))
    aborted = reduce_graph_run(fenced, AbortGraphRun(fenced.revision, GraphAbortReason("operator")))
    assert aborted.status is GraphRunStatus.ABORTED
    assert aborted.execution is aborted.resources is None


def test_abort_rejects_an_active_execution_without_changing_it() -> None:
    claimed = reduce_graph_run(start(), ClaimGraphExecution(0, GraphExecutionAttemptId("active"), None))
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        reduce_graph_run(claimed, AbortGraphRun(claimed.revision, GraphAbortReason("unsafe")))
    assert claimed.execution is not None


def test_resume_changes_only_selected_failure_and_is_not_a_resolution() -> None:
    claimed = reduce_graph_run(start(), ClaimGraphExecution(0, GraphExecutionAttemptId("a"), None))
    assert claimed.execution is not None
    failed = reduce_graph_run(
        claimed,
        SettleGraphNode(1, claimed.execution.token, FailedGraphNodeOutcome(A, GraphFailure("failed"))),
    )
    resumed = reduce_graph_run(
        failed,
        ResumeGraphNodes(failed.revision, (ResumeFailedNode(A, UseStepRequestInput()),)),
    )
    assert resumed.frontier.nodes[0].settlement.__class__.__name__ == "PendingGraphNode"


def test_skip_then_resolution_requires_two_revisions() -> None:
    claimed = reduce_graph_run(start(), ClaimGraphExecution(0, GraphExecutionAttemptId("a"), None))
    assert claimed.execution is not None
    failed = reduce_graph_run(
        claimed,
        SettleGraphNode(1, claimed.execution.token, FailedGraphNodeOutcome(A, GraphFailure("failed"))),
    )
    skipped = reduce_graph_run(
        failed,
        ResumeGraphNodes(
            failed.revision,
            (SkipFailedNode(A, GraphSkipReason("operator"), ContinueGraphRouting()),),
        ),
    )
    assert skipped.revision == failed.revision + 1
    assert skipped.status is GraphRunStatus.RUNNING


def test_unknown_runtime_command_fails_closed() -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(start(), cast(GraphRunCommand, object()))
