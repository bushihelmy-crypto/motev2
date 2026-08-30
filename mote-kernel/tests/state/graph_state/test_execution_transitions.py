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
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphJoinProgress,
    GraphNodeId,
    GraphNodeInterruptIdentity,
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
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    SettleGraphNode,
    StartGraphRun,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    UseStepRequestInput,
    child_graph_run_id,
    frontier_status,
    reduce_graph_run,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
ATTEMPT = GraphExecutionAttemptId("attempt")
CODEC = GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1)


def running(*nodes: GraphNodeId, codec: bool = True) -> GraphRunState:
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


def claim(
    state: GraphRunState,
    *,
    attempt: str = "attempt",
    resources: ResourceSnapshot | None = None,
) -> GraphRunState:
    return reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId(attempt), resources),
    )


def settle(state: GraphRunState, outcome: GraphNodeOutcome) -> GraphRunState:
    assert state.execution is not None
    return reduce_graph_run(state, SettleGraphNode(state.revision, state.execution.token, outcome))


def test_start_is_canonical_and_immutable() -> None:
    state = running(A, B)
    assert state.status is GraphRunStatus.RUNNING
    assert state.revision == state.execution_sequence == 0
    assert state.frontier == GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput())),
            GraphFrontierNode(B, PendingGraphNode(UseStepRequestInput())),
        )
    )
    with pytest.raises(FrozenInstanceError):
        state.superstep = 1  # type: ignore[misc]


def test_start_initializes_every_durable_field_and_default_binding() -> None:
    state = running(A, B)

    assert state.status is GraphRunStatus.RUNNING
    assert state.superstep == state.execution_sequence == state.revision == 0
    assert state.resume_input_codec == CODEC
    assert state.join_progress == ()
    assert state.resources is state.execution is state.abort is state.parent is None


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
def test_start_rejects_each_invalid_identity_frontier_and_codec(command: StartGraphRun) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(None, command)


def test_claim_stores_only_token_and_initial_resource_snapshot() -> None:
    state = claim(running(A, B), attempt="worker")
    assert state.execution == GraphExecutionLease(GraphExecutionToken(1, GraphExecutionAttemptId("worker")))
    assert state.resources is None
    assert state.revision == 1


def test_claim_rejects_invalid_lifecycle_and_attempt() -> None:
    state = running(A)
    with pytest.raises(GraphStateTransitionError, match="attempt identity"):
        claim(state, attempt=" ")
    active = claim(state)
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        claim(active)
    failed = settle(active, FailedGraphNodeOutcome(A, GraphFailure("failed")))
    with pytest.raises(GraphStateTransitionError, match="executable"):
        claim(failed)


@pytest.mark.parametrize("attempt", ["", " worker", "worker\n2"])
def test_claim_rejects_each_unstable_attempt_identity(attempt: str) -> None:
    state = running(A)

    with pytest.raises(GraphStateTransitionError, match="attempt identity"):
        claim(state, attempt=attempt)


def test_settle_one_node_keeps_sibling_pending_and_token() -> None:
    leased = claim(running(A, B))
    assert leased.execution is not None
    token = leased.execution.token
    settled = settle(leased, SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    assert isinstance(settled.frontier.nodes[0].settlement, SucceededGraphNode)
    assert isinstance(settled.frontier.nodes[1].settlement, PendingGraphNode)
    assert settled.execution == GraphExecutionLease(token)
    assert settled.revision == leased.revision + 1
    assert frontier_status(settled.frontier).name == "EXECUTABLE"


def test_each_typed_outcome_uses_the_same_single_node_transition() -> None:
    success = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    assert frontier_status(success.frontier).name == "SETTLED"
    failure = settle(claim(running(A)), FailedGraphNodeOutcome(A, GraphFailure("failed")))
    assert isinstance(failure.frontier.nodes[0].settlement, FailedGraphNode)
    interrupted = settle(
        claim(running(A)),
        InterruptedGraphNodeOutcome(
            A,
            GraphNodeInterruptIdentity(GraphRunId("run"), 0, A, 1),
            GraphInterruptPayload(b"question"),
        ),
    )
    assert isinstance(interrupted.frontier.nodes[0].settlement, InterruptedGraphNode)


def test_interrupt_identity_and_codec_are_checked_at_settlement() -> None:
    leased = claim(running(A))
    assert leased.execution is not None
    wrong = GraphNodeInterruptIdentity(GraphRunId("other"), 0, A, 1)
    with pytest.raises(GraphStateTransitionError, match="identity"):
        settle(leased, InterruptedGraphNodeOutcome(A, wrong, GraphInterruptPayload(b"q")))
    no_codec = claim(running(A, codec=False))
    with pytest.raises(GraphStateTransitionError, match="codec"):
        settle(
            no_codec,
            InterruptedGraphNodeOutcome(
                A,
                GraphNodeInterruptIdentity(GraphRunId("run"), 0, A, 1),
                GraphInterruptPayload(b"q"),
            ),
        )


def test_last_settlement_only_creates_a_stable_settled_revision() -> None:
    leased = claim(running(A))
    settled = settle(leased, SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    assert settled.status is GraphRunStatus.RUNNING
    assert frontier_status(settled.frontier).name == "SETTLED"
    assert settled.execution is settled.resources is None
    completed = reduce_graph_run(settled, CompleteGraphFrontier(settled.revision))
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.revision == settled.revision + 1


def test_advance_is_a_standalone_revision_and_replaces_the_frontier() -> None:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    progress = GraphJoinProgress((A, B), C, frozenset({A}))
    advanced = reduce_graph_run(
        settled,
        AdvanceGraphFrontier(settled.revision, (B,), (progress,)),
    )
    assert advanced.superstep == 1
    assert advanced.frontier == GraphFrontierState((GraphFrontierNode(B, PendingGraphNode(UseStepRequestInput())),))
    assert advanced.join_progress == (progress,)


def test_complete_rejects_discarding_unresolved_join_progress_atomically() -> None:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    invalid = replace(
        settled,
        join_progress=(GraphJoinProgress((A, B), C, frozenset({A})),),
    )
    with pytest.raises(GraphStateTransitionError, match="unresolved join"):
        reduce_graph_run(invalid, CompleteGraphFrontier(invalid.revision))
    assert invalid.frontier.nodes


def test_fence_preserves_partial_settlements_and_pending_input() -> None:
    leased = claim(running(A, B))
    assert leased.execution is not None
    partial = settle(leased, SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    assert partial.execution is not None
    fenced = reduce_graph_run(partial, FenceGraphExecution(partial.revision, partial.execution.token))
    assert fenced.execution is fenced.resources is None
    assert isinstance(fenced.frontier.nodes[0].settlement, SucceededGraphNode)
    assert isinstance(fenced.frontier.nodes[1].settlement, PendingGraphNode)


def test_fence_preserves_override_and_execution_sequence() -> None:
    initial = running(A)
    override = replace(
        initial,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"retry"))),
                ),
            )
        ),
    )
    leased = claim(override)
    assert leased.execution is not None

    fenced = reduce_graph_run(leased, FenceGraphExecution(leased.revision, leased.execution.token))

    assert fenced.frontier == leased.frontier
    assert fenced.superstep == leased.superstep
    assert fenced.execution_sequence == leased.execution_sequence
    assert fenced.execution is fenced.resources is None


def test_fence_then_reclaim_increments_generation_and_rejects_old_fence() -> None:
    first = claim(running(A), attempt="first")
    assert first.execution is not None
    old_token = first.execution.token
    fenced = reduce_graph_run(first, FenceGraphExecution(first.revision, old_token))
    second = claim(fenced, attempt="second")
    assert second.execution is not None

    assert second.execution.token.generation == old_token.generation + 1
    with pytest.raises(GraphStateTransitionError, match="active execution lease"):
        reduce_graph_run(second, FenceGraphExecution(second.revision, old_token))


def test_stale_token_and_nonpending_duplicate_are_rejected() -> None:
    first = claim(running(A), attempt="first")
    assert first.execution is not None
    fenced = reduce_graph_run(first, FenceGraphExecution(first.revision, first.execution.token))
    second = claim(fenced, attempt="second")
    assert second.execution is not None
    with pytest.raises(GraphStateTransitionError, match="active execution"):
        reduce_graph_run(
            second,
            SettleGraphNode(
                second.revision,
                first.execution.token,
                SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            ),
        )


@pytest.mark.parametrize("coordinate", ["run", "superstep", "node", "generation"])
def test_interrupt_settlement_rejects_each_wrong_execution_coordinate(coordinate: str) -> None:
    leased = claim(running(A))
    assert leased.execution is not None
    identity = GraphNodeInterruptIdentity(
        GraphRunId("other") if coordinate == "run" else leased.run_id,
        leased.superstep + 1 if coordinate == "superstep" else leased.superstep,
        B if coordinate == "node" else A,
        leased.execution.token.generation + 1 if coordinate == "generation" else leased.execution.token.generation,
    )

    with pytest.raises(GraphStateTransitionError, match="identity"):
        settle(
            leased,
            InterruptedGraphNodeOutcome(A, identity, GraphInterruptPayload(b"question")),
        )


@pytest.mark.parametrize("order", [(A, B), (B, A)])
def test_node_settlement_order_preserves_every_outcome_without_batch_coverage(
    order: tuple[GraphNodeId, GraphNodeId],
) -> None:
    state = claim(running(A, B))
    first = settle(state, SucceededGraphNodeOutcome(order[0], ContinueGraphRouting()))
    second = settle(first, SucceededGraphNodeOutcome(order[1], ContinueGraphRouting()))

    assert first.revision == state.revision + 1
    assert second.revision == first.revision + 1
    assert all(isinstance(node.settlement, SucceededGraphNode) for node in second.frontier.nodes)
    assert second.execution is None


def test_start_preserves_parent_and_rejects_invalid_parent_identity() -> None:
    parent = ParentGraphActivation(GraphRunId("parent"), 2, GraphNodeId("nested"))
    command = StartGraphRun(
        child_graph_run_id(parent.run_id, parent.superstep, parent.node_id),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(1),
        (A,),
        parent=parent,
        resume_input_codec=CODEC,
    )
    state = reduce_graph_run(None, command)
    assert state.parent == parent
    with pytest.raises(GraphStateTransitionError, match="child graph run identity"):
        reduce_graph_run(
            None,
            replace(command, run_id=GraphRunId("wrong-child")),
        )


@pytest.mark.parametrize("reason", ["", " ", "bad\nreason", "bad\rreason", " bad"])
def test_failure_settlement_rejects_unstable_reason(reason: str) -> None:
    leased = claim(running(A))
    with pytest.raises(GraphStateTransitionError, match="graph failure"):
        settle(leased, FailedGraphNodeOutcome(A, GraphFailure(reason)))


@pytest.mark.parametrize("node_ids", [(), (B, A), (B, B)])
def test_advance_rejects_each_noncanonical_next_frontier(node_ids: tuple[GraphNodeId, ...]) -> None:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))

    with pytest.raises(GraphStateTransitionError, match="non-empty and canonical"):
        reduce_graph_run(settled, AdvanceGraphFrontier(settled.revision, node_ids, ()))


def test_claim_rejects_corrupt_resource_snapshot_without_mutating_state() -> None:
    file_id = ResourceId("file")
    malformed = ResourceSnapshot(
        (ResourceLock(file_id),),
        (ResourceAcquisition(A, (file_id,), (), file_id),),
    )
    state = running(A)
    with pytest.raises(GraphStateTransitionError, match="resource snapshot"):
        claim(state, resources=malformed)
    assert state.execution is None and state.resources is None


def test_resolution_and_lifecycle_guards_fail_closed() -> None:
    from mote_kernel.state.graph_state.execution_transitions import (
        advance_graph_frontier,
        complete_graph_frontier,
        fence_graph_execution,
        settle_graph_node,
    )

    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    with pytest.raises(GraphStateTransitionError, match="running graph"):
        complete_graph_frontier(replace(settled, status=GraphRunStatus.COMPLETED), CompleteGraphFrontier(0))
    with pytest.raises(GraphStateTransitionError, match="settled frontier"):
        advance_graph_frontier(running(A), AdvanceGraphFrontier(0, (B,), ()))
    active = claim(running(A))
    assert active.execution is not None
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        complete_graph_frontier(
            replace(settled, execution=active.execution),
            CompleteGraphFrontier(settled.revision),
        )
    with pytest.raises(GraphStateTransitionError, match="canonical"):
        advance_graph_frontier(settled, AdvanceGraphFrontier(settled.revision, (B, B), ()))
    with pytest.raises(GraphStateTransitionError, match="running graph execution"):
        settle_graph_node(
            replace(settled, status=GraphRunStatus.COMPLETED),
            SettleGraphNode(0, GraphExecutionToken(1, ATTEMPT), cast(GraphNodeOutcome, object())),
        )
    with pytest.raises(GraphStateTransitionError, match="running graph"):
        fence_graph_execution(
            replace(settled, status=GraphRunStatus.COMPLETED),
            FenceGraphExecution(0, GraphExecutionToken(1, ATTEMPT)),
        )
    with pytest.raises(GraphStateTransitionError, match="unsupported variant"):
        settle_graph_node(
            active,
            SettleGraphNode(active.revision, active.execution.token, cast(GraphNodeOutcome, object())),
        )
    with pytest.raises(GraphStateTransitionError, match="pending"):
        settle_graph_node(
            active,
            SettleGraphNode(
                active.revision, active.execution.token, SucceededGraphNodeOutcome(B, ContinueGraphRouting())
            ),
        )


def test_claim_guard_rejects_a_corrupt_empty_pending_frontier(monkeypatch: pytest.MonkeyPatch) -> None:
    import mote_kernel.state.graph_state.execution_transitions as transitions

    state = replace(running(A), frontier=GraphFrontierState(()))

    def executable(_frontier: GraphFrontierState) -> transitions.GraphFrontierStatus:
        return transitions.GraphFrontierStatus.EXECUTABLE

    monkeypatch.setattr(transitions, "frontier_status", executable)
    # The transition owns this guard even when called directly with a recovered malformed snapshot.
    with pytest.raises(GraphStateTransitionError, match="requires pending"):
        transitions.claim_graph_execution(state, ClaimGraphExecution(0, ATTEMPT, None))
    partial = claim(running(A, B), attempt="third")
    assert partial.execution is not None
    settled = settle(partial, SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    with pytest.raises(GraphStateTransitionError, match="pending"):
        reduce_graph_run(
            settled,
            SettleGraphNode(
                settled.revision,
                partial.execution.token,
                SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            ),
        )
