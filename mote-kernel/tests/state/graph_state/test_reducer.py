from dataclasses import dataclass, replace
from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    FailedGraphNodeOutcome,
    FenceGraphExecution,
    GraphAbortReason,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierResolution,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphJoinProgress,
    GraphNodeId,
    GraphNodeOutcome,
    GraphNodeResumeAction,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRouteId,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    GraphStateTransitionError,
    InterruptedGraphNode,
    InterruptedGraphNodeOutcome,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResumeFailedNode,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    SelectGraphRoute,
    SettleGraphExecution,
    SkipFailedNode,
    SkippedGraphNode,
    StartGraphRun,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    UseStepRequestInput,
    derive_graph_node_interrupt_identity,
    frontier_status,
    graph_interrupt_id,
    reduce_graph_run,
    validate_graph_run_state,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
ATTEMPT = GraphExecutionAttemptId("attempt")
CODEC = GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1)


def start(*nodes: GraphNodeId, codec: bool = True) -> GraphRunState:
    return reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            tuple(nodes),
            resume_input_codec=CODEC if codec else None,
        ),
    )


def claim(state: GraphRunState) -> GraphRunState:
    node_ids = tuple(node.node_id for node in state.frontier.nodes if isinstance(node.settlement, PendingGraphNode))
    return reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, ATTEMPT, node_ids),
    )


def settle(
    state: GraphRunState,
    outcomes: tuple[GraphNodeOutcome, ...],
    resolution: GraphFrontierResolution | None = None,
) -> GraphRunState:
    assert state.execution is not None
    return reduce_graph_run(
        state,
        SettleGraphExecution(state.revision, state.execution.token, outcomes, resolution),
    )


def awaiting_state() -> GraphRunState:
    leased = claim(start(A, B, C))
    return settle(
        leased,
        (
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            FailedGraphNodeOutcome(B, GraphFailure("b failed")),
            FailedGraphNodeOutcome(C, GraphFailure("c failed")),
        ),
    )


def test_start_claim_and_mixed_settlement_preserve_complete_frontier() -> None:
    initial = start(A, B, C)
    leased = claim(initial)
    assert leased.execution is not None
    assert leased.execution.node_ids == (A, B, C)
    assert leased.execution.token.generation == 1
    assert leased.execution_sequence == 1

    interrupted_identity = derive_graph_node_interrupt_identity(leased.run_id, 0, C, 1)
    mixed = settle(
        leased,
        (
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            FailedGraphNodeOutcome(B, GraphFailure("failed")),
            InterruptedGraphNodeOutcome(C, interrupted_identity, GraphInterruptPayload(b"question")),
        ),
    )

    assert mixed.execution is None
    assert mixed.resources is None
    assert isinstance(mixed.frontier.nodes[0].settlement, SucceededGraphNode)
    assert mixed.frontier.nodes[1].settlement == FailedGraphNode(GraphFailure("failed"))
    assert isinstance(mixed.frontier.nodes[2].settlement, InterruptedGraphNode)
    assert frontier_status(mixed.frontier).name == "AWAITING_RESUME"


def test_selective_failure_resume_leaves_siblings_unchanged() -> None:
    awaiting = awaiting_state()

    resumed = reduce_graph_run(
        awaiting,
        ResumeGraphNodes(
            awaiting.revision,
            (ResumeFailedNode(B, OverrideGraphNodeInput(GraphResumeInputPayload(b"retry-b"))),),
            None,
        ),
    )

    assert resumed.frontier.nodes[0] == awaiting.frontier.nodes[0]
    assert resumed.frontier.nodes[1].settlement == PendingGraphNode(
        OverrideGraphNodeInput(GraphResumeInputPayload(b"retry-b"))
    )
    assert resumed.frontier.nodes[2] == awaiting.frontier.nodes[2]


def test_one_resume_command_atomically_resumes_failure_and_interrupt_and_skips_failure() -> None:
    leased = claim(start(A, B, C))
    identity = derive_graph_node_interrupt_identity(leased.run_id, 0, C, 1)
    awaiting = settle(
        leased,
        (
            FailedGraphNodeOutcome(A, GraphFailure("a")),
            FailedGraphNodeOutcome(B, GraphFailure("b")),
            InterruptedGraphNodeOutcome(C, identity, GraphInterruptPayload(b"request")),
        ),
    )

    resumed = reduce_graph_run(
        awaiting,
        ResumeGraphNodes(
            awaiting.revision,
            (
                ResumeFailedNode(A, UseStepRequestInput()),
                SkipFailedNode(B, GraphSkipReason("operator"), SelectGraphRoute(GraphRouteId("alternate"))),
                ResumeInterruptedNode(
                    C,
                    graph_interrupt_id(
                        identity.run_id,
                        identity.superstep,
                        identity.node_id,
                        identity.execution_generation,
                    ),
                    OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
                ),
            ),
            None,
        ),
    )

    assert resumed.frontier.nodes[0].settlement == PendingGraphNode(UseStepRequestInput())
    assert resumed.frontier.nodes[1].settlement == SkippedGraphNode(
        GraphFailure("b"), GraphSkipReason("operator"), SelectGraphRoute(GraphRouteId("alternate"))
    )
    assert resumed.frontier.nodes[2].settlement == PendingGraphNode(
        OverrideGraphNodeInput(GraphResumeInputPayload(b"answer"))
    )


def test_skipping_last_failure_requires_and_atomically_applies_resolution() -> None:
    leased = claim(start(A, B))
    awaiting = settle(
        leased,
        (
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            FailedGraphNodeOutcome(B, GraphFailure("failed")),
        ),
    )
    with pytest.raises(GraphStateTransitionError, match="settled resume"):
        reduce_graph_run(
            awaiting,
            ResumeGraphNodes(
                awaiting.revision,
                (SkipFailedNode(B, GraphSkipReason("skip"), ContinueGraphRouting()),),
                None,
            ),
        )

    completed = reduce_graph_run(
        awaiting,
        ResumeGraphNodes(
            awaiting.revision,
            (SkipFailedNode(B, GraphSkipReason("skip"), ContinueGraphRouting()),),
            CompleteGraphFrontier(),
        ),
    )
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.frontier == GraphFrontierState(())


def test_terminal_settlement_and_skip_validate_transient_frontier_values_before_resolution() -> None:
    leased = claim(start(A))
    with pytest.raises(GraphStateTransitionError, match="graph route identity"):
        settle(
            leased,
            (SucceededGraphNodeOutcome(A, SelectGraphRoute(GraphRouteId(""))),),
            CompleteGraphFrontier(),
        )

    with pytest.raises(GraphStateTransitionError, match="unsupported variant"):
        settle(
            claim(start(A)),
            (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
            cast(GraphFrontierResolution, object()),
        )

    failed = settle(claim(start(A)), (FailedGraphNodeOutcome(A, GraphFailure("failed")),))
    with pytest.raises(GraphStateTransitionError, match="skip reason"):
        reduce_graph_run(
            failed,
            ResumeGraphNodes(
                failed.revision,
                (SkipFailedNode(A, GraphSkipReason(""), ContinueGraphRouting()),),
                CompleteGraphFrontier(),
            ),
        )


def test_settled_execution_can_advance_to_clean_activation_or_complete() -> None:
    leased = claim(start(A))
    progress = GraphJoinProgress((A, B), C, frozenset({A}))
    advanced = settle(
        leased,
        (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
        AdvanceGraphFrontier((B,), (progress,)),
    )
    assert advanced.superstep == 1
    assert advanced.frontier == GraphFrontierState((GraphFrontierNode(B, PendingGraphNode(UseStepRequestInput())),))
    assert advanced.join_progress == (progress,)

    completed = settle(
        claim(start(A)),
        (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
        CompleteGraphFrontier(),
    )
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.frontier.nodes == ()


def test_exact_fence_preserves_frontier_input_and_sequence_and_rejects_stale_token() -> None:
    leased = claim(start(A))
    assert leased.execution is not None
    fenced = reduce_graph_run(leased, FenceGraphExecution(leased.revision, leased.execution.token))
    assert fenced.frontier == leased.frontier
    assert fenced.execution_sequence == leased.execution_sequence
    assert fenced.execution is None

    new_lease = claim(fenced)
    with pytest.raises(GraphStateTransitionError, match="active execution lease"):
        reduce_graph_run(new_lease, FenceGraphExecution(new_lease.revision, leased.execution.token))


@pytest.mark.parametrize(
    "actions",
    [
        (),
        (cast(GraphNodeResumeAction, object()),),
        (ResumeFailedNode(B, UseStepRequestInput()), ResumeFailedNode(B, UseStepRequestInput())),
        (ResumeFailedNode(C, UseStepRequestInput()), ResumeFailedNode(B, UseStepRequestInput())),
        (ResumeFailedNode(GraphNodeId("missing"), UseStepRequestInput()),),
        (SkipFailedNode(A, GraphSkipReason("no"), ContinueGraphRouting()),),
    ],
)
def test_resume_actions_fail_closed_as_one_atomic_group(actions: tuple[GraphNodeResumeAction, ...]) -> None:
    state = awaiting_state()
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state, ResumeGraphNodes(state.revision, actions, None))
    assert state == awaiting_state()


def test_interrupt_resume_rejects_wrong_or_consumed_identity() -> None:
    leased = claim(start(A))
    identity = derive_graph_node_interrupt_identity(leased.run_id, 0, A, 1)
    awaiting = settle(
        leased,
        (InterruptedGraphNodeOutcome(A, identity, GraphInterruptPayload(b"request")),),
    )
    override = OverrideGraphNodeInput(GraphResumeInputPayload(b"answer"))
    with pytest.raises(GraphStateTransitionError, match="does not match"):
        reduce_graph_run(
            awaiting,
            ResumeGraphNodes(
                awaiting.revision,
                (ResumeInterruptedNode(A, graph_interrupt_id(awaiting.run_id, 0, A, 2), override),),
                None,
            ),
        )
    resumed = reduce_graph_run(
        awaiting,
        ResumeGraphNodes(
            awaiting.revision,
            (ResumeInterruptedNode(A, graph_interrupt_id(awaiting.run_id, 0, A, 1), override),),
            None,
        ),
    )
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(
            resumed,
            ResumeGraphNodes(
                resumed.revision,
                (ResumeInterruptedNode(A, graph_interrupt_id(awaiting.run_id, 0, A, 1), override),),
                None,
            ),
        )


def test_claim_and_settlement_require_exact_canonical_coverage_and_identity() -> None:
    initial = start(A, B)
    for node_ids in ((A,), (B, A), (A, B, B)):
        with pytest.raises(GraphStateTransitionError):
            reduce_graph_run(initial, ClaimGraphExecution(initial.revision, ATTEMPT, node_ids))
    leased = claim(initial)
    assert leased.execution is not None
    invalid_identity = derive_graph_node_interrupt_identity(leased.run_id, 0, A, 2)
    for outcomes in (
        (FailedGraphNodeOutcome(A, GraphFailure("a")),),
        (FailedGraphNodeOutcome(B, GraphFailure("b")), FailedGraphNodeOutcome(A, GraphFailure("a"))),
        (
            InterruptedGraphNodeOutcome(A, invalid_identity, GraphInterruptPayload(b"request")),
            FailedGraphNodeOutcome(B, GraphFailure("b")),
        ),
    ):
        with pytest.raises(GraphStateTransitionError):
            settle(leased, outcomes)

    unsupported = cast(GraphNodeOutcome, object())
    with pytest.raises(GraphStateTransitionError, match="unsupported variant"):
        settle(claim(start(A)), (unsupported,))


def test_no_codec_allows_default_failure_resume_but_rejects_override_and_interrupt() -> None:
    leased = claim(start(A, codec=False))
    failed = settle(leased, (FailedGraphNodeOutcome(A, GraphFailure("failed")),))
    resumed = reduce_graph_run(
        failed,
        ResumeGraphNodes(failed.revision, (ResumeFailedNode(A, UseStepRequestInput()),), None),
    )
    assert isinstance(resumed.frontier.nodes[0].settlement, PendingGraphNode)
    with pytest.raises(GraphStateTransitionError, match="codec"):
        reduce_graph_run(
            failed,
            ResumeGraphNodes(
                failed.revision,
                (ResumeFailedNode(A, OverrideGraphNodeInput(GraphResumeInputPayload(b"x"))),),
                None,
            ),
        )

    leased = claim(start(A, codec=False))
    identity = derive_graph_node_interrupt_identity(leased.run_id, 0, A, 1)
    with pytest.raises(GraphStateTransitionError, match="codec"):
        settle(leased, (InterruptedGraphNodeOutcome(A, identity, GraphInterruptPayload(b"q")),))


def test_quiescent_abort_clears_admission_and_retains_diagnostic_frontier() -> None:
    state = start(A)
    aborted = reduce_graph_run(state, AbortGraphRun(state.revision, GraphAbortReason("operator")))
    assert aborted.status is GraphRunStatus.ABORTED
    assert aborted.frontier is state.frontier
    assert aborted.abort is not None
    for command in (
        ClaimGraphExecution(aborted.revision, ATTEMPT, (A,)),
        ResumeGraphNodes(aborted.revision, (ResumeFailedNode(A, UseStepRequestInput()),), None),
        AbortGraphRun(aborted.revision, GraphAbortReason("again")),
    ):
        with pytest.raises(GraphStateTransitionError):
            reduce_graph_run(aborted, command)

    leased = claim(start(A))
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        reduce_graph_run(leased, AbortGraphRun(leased.revision, GraphAbortReason("unsafe")))


def test_reducer_rejects_stale_revision_and_invalid_recovered_state() -> None:
    state = start(A)
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(state, ClaimGraphExecution(99, ATTEMPT, (A,)))
    corrupt = replace(state, superstep=-1)
    with pytest.raises(GraphStateTransitionError, match="negative"):
        validate_graph_run_state(corrupt)


def test_reducer_fails_closed_for_a_command_outside_the_closed_union() -> None:
    @dataclass(frozen=True, slots=True)
    class ForgedCommand:
        expected_revision: int

    state = start(A)
    command = cast(GraphRunCommand, ForgedCommand(state.revision))
    with pytest.raises(AssertionError, match="Expected code to be unreachable"):
        reduce_graph_run(state, command)


def test_claim_fence_and_settlement_lifecycle_and_resolution_guards() -> None:
    awaiting = awaiting_state()
    with pytest.raises(GraphStateTransitionError, match="executable"):
        reduce_graph_run(awaiting, ClaimGraphExecution(awaiting.revision, ATTEMPT, ()))

    completed = settle(
        claim(start(A)),
        (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
        CompleteGraphFrontier(),
    )
    token = GraphExecutionToken(1, ATTEMPT)
    with pytest.raises(GraphStateTransitionError, match="running"):
        reduce_graph_run(completed, FenceGraphExecution(completed.revision, token))
    with pytest.raises(GraphStateTransitionError, match="running graph execution"):
        reduce_graph_run(
            completed,
            SettleGraphExecution(
                completed.revision,
                token,
                (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
                CompleteGraphFrontier(),
            ),
        )

    leased = claim(start(A))
    assert leased.execution is not None
    with pytest.raises(GraphStateTransitionError, match="settled frontier requires"):
        settle(leased, (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),))
    with pytest.raises(GraphStateTransitionError, match="unsettled frontier"):
        settle(
            leased,
            (FailedGraphNodeOutcome(A, GraphFailure("failed")),),
            CompleteGraphFrontier(),
        )


def test_resume_rejects_resolution_while_frontier_remains_unsettled() -> None:
    awaiting = awaiting_state()
    with pytest.raises(GraphStateTransitionError, match="unsettled resume"):
        reduce_graph_run(
            awaiting,
            ResumeGraphNodes(
                awaiting.revision,
                (ResumeFailedNode(B, UseStepRequestInput()),),
                CompleteGraphFrontier(),
            ),
        )
