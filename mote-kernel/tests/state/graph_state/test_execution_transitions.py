from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    FailedGraphNodeOutcome,
    FenceGraphExecution,
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
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    InterruptedGraphNode,
    InterruptedGraphNodeOutcome,
    OverrideGraphNodeInput,
    ParentGraphActivation,
    PendingGraphNode,
    SettleGraphExecution,
    StartGraphRun,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    UseStepRequestInput,
    child_graph_run_id,
    derive_graph_node_interrupt_identity,
    reduce_graph_run,
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


def running(*nodes: GraphNodeId, codec: bool = True) -> GraphRunState:
    return start(*nodes, codec=codec)


def claim(state: GraphRunState, *, attempt: str = "attempt") -> GraphRunState:
    node_ids = tuple(node.node_id for node in state.frontier.nodes if isinstance(node.settlement, PendingGraphNode))
    return reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId(attempt), node_ids),
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


def test_start_initializes_every_durable_field_and_default_binding() -> None:
    state = running(A, B)

    assert state.status is GraphRunStatus.RUNNING
    assert state.superstep == state.execution_sequence == state.revision == 0
    assert state.frontier == GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput())),
            GraphFrontierNode(B, PendingGraphNode(UseStepRequestInput())),
        )
    )
    assert state.resume_input_codec == CODEC
    assert state.join_progress == ()
    assert state.resources is state.execution is state.abort is state.parent is None


def test_start_preserves_parent_activation_and_is_pure() -> None:
    parent = ParentGraphActivation(GraphRunId("parent"), 7, GraphNodeId("nested"))
    command = StartGraphRun(
        child_graph_run_id(parent.run_id, parent.superstep, parent.node_id),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(1),
        (A,),
        parent,
        CODEC,
    )

    state = reduce_graph_run(None, command)

    assert state.parent is parent
    assert state.resume_input_codec is CODEC
    with pytest.raises(FrozenInstanceError):
        state.superstep = 9  # type: ignore[misc]


def test_start_rejects_child_run_identity_that_does_not_match_parent_activation() -> None:
    parent = ParentGraphActivation(GraphRunId("parent"), 7, GraphNodeId("nested"))

    with pytest.raises(GraphStateTransitionError, match="child graph run identity"):
        reduce_graph_run(
            None,
            StartGraphRun(
                GraphRunId("arbitrary-child"),
                GraphDefinitionId("graph"),
                GraphDefinitionVersion(1),
                (A,),
                parent,
            ),
        )


@pytest.mark.parametrize(
    "command",
    [
        StartGraphRun(GraphRunId(""), GraphDefinitionId("graph"), GraphDefinitionVersion(1), (A,)),
        StartGraphRun(GraphRunId(" run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), (A,)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId(""), GraphDefinitionVersion(1), (A,)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(0), (A,)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), ()),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), (B, A)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), (A, A)),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (A,),
            resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId(""), 1),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (A,),
            resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input"), 0),
        ),
    ],
)
def test_start_rejects_invalid_identity_frontier_and_codec(command: StartGraphRun) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(None, command)


def test_claim_creates_exact_lease_and_increments_generation_and_revision() -> None:
    state = running(A, B)

    leased = claim(state, attempt="worker-1")

    assert leased.execution is not None
    assert leased.execution.node_ids == (A, B)
    assert leased.execution.token == GraphExecutionToken(1, GraphExecutionAttemptId("worker-1"))
    assert leased.execution_sequence == 1
    assert leased.revision == state.revision + 1
    assert state.execution is None


@pytest.mark.parametrize("node_ids", [(), (A,), (B,), (B, A), (A, B, B), (A, C)])
def test_claim_rejects_nonexact_or_noncanonical_pending_coverage(node_ids: tuple[GraphNodeId, ...]) -> None:
    state = running(A, B)

    with pytest.raises(GraphStateTransitionError, match="exactly cover"):
        reduce_graph_run(state, ClaimGraphExecution(state.revision, ATTEMPT, node_ids))


@pytest.mark.parametrize("attempt", ["", " worker", "worker\n2"])
def test_claim_rejects_invalid_attempt_identity(attempt: str) -> None:
    state = running(A)

    with pytest.raises(GraphStateTransitionError, match="attempt identity"):
        reduce_graph_run(
            state,
            ClaimGraphExecution(state.revision, GraphExecutionAttemptId(attempt), (A,)),
        )


def test_claim_rejects_existing_lease_and_awaiting_resume_frontier() -> None:
    leased = claim(running(A))
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        reduce_graph_run(leased, ClaimGraphExecution(leased.revision, ATTEMPT, (A,)))

    failed = settle(leased, (FailedGraphNodeOutcome(A, GraphFailure("failed")),))
    with pytest.raises(GraphStateTransitionError, match="executable"):
        reduce_graph_run(failed, ClaimGraphExecution(failed.revision, ATTEMPT, ()))


def test_fence_clears_only_execution_and_resources_and_preserves_override() -> None:
    state = running(A)
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"retry"))),
                ),
            )
        ),
    )
    leased = claim(state)
    assert leased.execution is not None

    fenced = reduce_graph_run(leased, FenceGraphExecution(leased.revision, leased.execution.token))

    assert fenced.execution is fenced.resources is None
    assert fenced.frontier == leased.frontier
    assert fenced.superstep == leased.superstep
    assert fenced.execution_sequence == leased.execution_sequence
    assert fenced.revision == leased.revision + 1


def test_fence_then_reclaim_uses_new_generation_and_rejects_old_token() -> None:
    first = claim(running(A), attempt="first")
    assert first.execution is not None
    old_token = first.execution.token
    fenced = reduce_graph_run(first, FenceGraphExecution(first.revision, old_token))
    second = claim(fenced, attempt="second")

    assert second.execution is not None
    assert second.execution.token.generation == old_token.generation + 1
    with pytest.raises(GraphStateTransitionError, match="active execution lease"):
        reduce_graph_run(second, FenceGraphExecution(second.revision, old_token))


@pytest.mark.parametrize(
    "outcomes",
    [
        (),
        (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
        (
            SucceededGraphNodeOutcome(B, ContinueGraphRouting()),
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
        ),
        (
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
        ),
    ],
)
def test_settlement_requires_exact_canonical_lease_coverage(outcomes: tuple[GraphNodeOutcome, ...]) -> None:
    leased = claim(running(A, B))

    with pytest.raises(GraphStateTransitionError, match="exactly cover"):
        settle(leased, outcomes)


@pytest.mark.parametrize("coordinate", ["run", "superstep", "node", "generation"])
def test_interrupt_settlement_rejects_each_wrong_exact_lease_coordinate(coordinate: str) -> None:
    leased = claim(running(A))
    assert leased.execution is not None
    identity = derive_graph_node_interrupt_identity(
        GraphRunId("other") if coordinate == "run" else leased.run_id,
        leased.superstep + 1 if coordinate == "superstep" else leased.superstep,
        B if coordinate == "node" else A,
        leased.execution.token.generation + 1 if coordinate == "generation" else leased.execution.token.generation,
    )

    with pytest.raises(GraphStateTransitionError, match="identity"):
        settle(
            leased,
            (InterruptedGraphNodeOutcome(A, identity, GraphInterruptPayload(b"question")),),
        )


def test_mixed_settlement_preserves_every_typed_outcome_and_prior_sibling() -> None:
    state = running(A, B, C)
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting())),
                state.frontier.nodes[1],
                state.frontier.nodes[2],
            )
        ),
    )
    leased = claim(state)
    assert leased.execution is not None
    identity = derive_graph_node_interrupt_identity(leased.run_id, leased.superstep, C, 1)

    mixed = settle(
        leased,
        (
            FailedGraphNodeOutcome(B, GraphFailure("b failed")),
            InterruptedGraphNodeOutcome(C, identity, GraphInterruptPayload(b"question")),
        ),
    )

    assert mixed.frontier.nodes[0] is state.frontier.nodes[0]
    assert mixed.frontier.nodes[1].settlement == FailedGraphNode(GraphFailure("b failed"))
    assert isinstance(mixed.frontier.nodes[2].settlement, InterruptedGraphNode)
    assert mixed.execution is mixed.resources is None
    assert mixed.superstep == leased.superstep


@pytest.mark.parametrize("failure", ["", "  ", " failure", "line\nbreak", "carriage\rreturn"])
def test_failure_outcome_requires_stable_nonempty_reason(failure: str) -> None:
    leased = claim(running(A))

    with pytest.raises(GraphStateTransitionError, match="graph failure"):
        settle(leased, (FailedGraphNodeOutcome(A, GraphFailure(failure)),))


def test_settlement_advance_replaces_activation_without_carrying_override_or_interrupt() -> None:
    leased = claim(running(A))
    progress = GraphJoinProgress((A, B), C, frozenset({A}))

    advanced = settle(
        leased,
        (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
        AdvanceGraphFrontier((B,), (progress,)),
    )

    assert advanced.superstep == 1
    assert advanced.frontier == GraphFrontierState((GraphFrontierNode(B, PendingGraphNode(UseStepRequestInput())),))
    assert advanced.join_progress == (progress,)
    assert advanced.execution is advanced.resources is None


def test_settlement_complete_uses_canonical_empty_terminal_position() -> None:
    leased = claim(running(A))

    completed = settle(
        leased,
        (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
        CompleteGraphFrontier(),
    )

    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.frontier == GraphFrontierState(())
    assert completed.join_progress == ()
    assert completed.execution is completed.resources is completed.abort is None


def test_settlement_cannot_complete_with_unresolved_join_progress() -> None:
    state = running(A)
    state = replace(
        state,
        join_progress=(GraphJoinProgress((A, B), C, frozenset({A})),),
    )
    leased = claim(state)

    with pytest.raises(GraphStateTransitionError, match="unresolved join progress"):
        settle(
            leased,
            (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
            CompleteGraphFrontier(),
        )


def test_settlement_rejects_resolution_presence_mismatch_atomically() -> None:
    leased = claim(running(A))
    with pytest.raises(GraphStateTransitionError, match="requires"):
        settle(leased, (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),))
    assert leased.execution is not None

    leased = claim(running(A))
    with pytest.raises(GraphStateTransitionError, match="unsettled"):
        settle(
            leased,
            (FailedGraphNodeOutcome(A, GraphFailure("failed")),),
            CompleteGraphFrontier(),
        )
    assert leased.execution is not None


def test_settlement_rejects_unknown_outcome_and_resolution_variants() -> None:
    leased = claim(running(A))
    with pytest.raises(GraphStateTransitionError, match=r"outcome.*unsupported"):
        settle(leased, (cast(GraphNodeOutcome, object()),))

    leased = claim(running(A))
    with pytest.raises(GraphStateTransitionError, match=r"resolution.*unsupported"):
        settle(
            leased,
            (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
            cast(GraphFrontierResolution, object()),
        )


def test_settlement_rejects_stale_token_without_changing_active_generation() -> None:
    first = claim(running(A), attempt="first")
    assert first.execution is not None
    fenced = reduce_graph_run(first, FenceGraphExecution(first.revision, first.execution.token))
    second = claim(fenced, attempt="second")
    stale = SettleGraphExecution(
        second.revision,
        first.execution.token,
        (SucceededGraphNodeOutcome(A, ContinueGraphRouting()),),
        CompleteGraphFrontier(),
    )

    with pytest.raises(GraphStateTransitionError, match="active execution lease"):
        reduce_graph_run(second, stale)
    assert second.execution is not None
    assert second.execution.token.generation == 2
