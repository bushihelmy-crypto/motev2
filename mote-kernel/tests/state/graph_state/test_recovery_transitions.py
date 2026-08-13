from dataclasses import replace
from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    FailedGraphNodeOutcome,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptId,
    GraphInterruptPayload,
    GraphJoinProgress,
    GraphNodeId,
    GraphNodeOutcome,
    GraphNodeResumeAction,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    GraphStateTransitionError,
    InterruptedGraphNode,
    InterruptedGraphNodeOutcome,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    ResumeFailedNode,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    SelectGraphRoute,
    SettleGraphExecution,
    SkipFailedNode,
    SkippedGraphNode,
    StartGraphRun,
    SucceededGraphNodeOutcome,
    UseStepRequestInput,
    derive_graph_node_interrupt_identity,
    graph_interrupt_id,
    reduce_graph_run,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
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
        ClaimGraphExecution(
            state.revision,
            GraphExecutionAttemptId(f"attempt-{state.execution_sequence + 1}"),
            node_ids,
        ),
    )


def settle(state: GraphRunState, outcomes: tuple[GraphNodeOutcome, ...]) -> GraphRunState:
    assert state.execution is not None
    return reduce_graph_run(
        state,
        SettleGraphExecution(
            state.revision,
            state.execution.token,
            outcomes,
            None,
        ),
    )


def failed_state() -> GraphRunState:
    leased = claim(start(A, B, C))
    return settle(
        leased,
        (
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            FailedGraphNodeOutcome(B, GraphFailure("b failed")),
            FailedGraphNodeOutcome(C, GraphFailure("c failed")),
        ),
    )


def interrupted_state() -> GraphRunState:
    leased = claim(start(A, B))
    assert leased.execution is not None
    identity = derive_graph_node_interrupt_identity(
        leased.run_id,
        leased.superstep,
        B,
        leased.execution.token.generation,
    )
    return settle(
        leased,
        (
            FailedGraphNodeOutcome(A, GraphFailure("a failed")),
            InterruptedGraphNodeOutcome(B, identity, GraphInterruptPayload(b"question")),
        ),
    )


def test_selective_failure_resume_preserves_every_unselected_settlement() -> None:
    state = failed_state()

    resumed = reduce_graph_run(
        state,
        ResumeGraphNodes(
            state.revision,
            (ResumeFailedNode(B, OverrideGraphNodeInput(GraphResumeInputPayload(b"retry-b"))),),
            None,
        ),
    )

    assert resumed.frontier.nodes[0] is state.frontier.nodes[0]
    assert resumed.frontier.nodes[1].settlement == PendingGraphNode(
        OverrideGraphNodeInput(GraphResumeInputPayload(b"retry-b"))
    )
    assert resumed.frontier.nodes[2] is state.frontier.nodes[2]
    assert resumed.superstep == state.superstep


def test_failure_resume_supports_request_default_binding() -> None:
    state = failed_state()

    resumed = reduce_graph_run(
        state,
        ResumeGraphNodes(state.revision, (ResumeFailedNode(B, UseStepRequestInput()),), None),
    )

    assert resumed.frontier.nodes[1].settlement == PendingGraphNode(UseStepRequestInput())


def test_interrupt_resume_requires_exact_current_projected_id() -> None:
    state = interrupted_state()
    interrupted = state.frontier.nodes[1].settlement
    assert isinstance(interrupted, InterruptedGraphNode)
    identity = interrupted.interrupt.identity
    exact = graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )

    resumed = reduce_graph_run(
        state,
        ResumeGraphNodes(
            state.revision,
            (
                ResumeInterruptedNode(
                    B,
                    exact,
                    OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
                ),
            ),
            None,
        ),
    )

    assert resumed.frontier.nodes[1].settlement == PendingGraphNode(
        OverrideGraphNodeInput(GraphResumeInputPayload(b"answer"))
    )


@pytest.mark.parametrize(
    "interrupt_id",
    [
        GraphInterruptId("wrong"),
        graph_interrupt_id(GraphRunId("other"), 0, B, 1),
        graph_interrupt_id(GraphRunId("run"), 1, B, 1),
        graph_interrupt_id(GraphRunId("run"), 0, B, 2),
    ],
)
def test_interrupt_resume_rejects_wrong_or_stale_id(interrupt_id: GraphInterruptId) -> None:
    state = interrupted_state()

    with pytest.raises(GraphStateTransitionError, match="does not match"):
        reduce_graph_run(
            state,
            ResumeGraphNodes(
                state.revision,
                (
                    ResumeInterruptedNode(
                        B,
                        interrupt_id,
                        OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
                    ),
                ),
                None,
            ),
        )


@pytest.mark.parametrize("settlement", ["succeeded", "pending", "interrupted", "skipped"])
def test_skip_requires_failed_settlement(settlement: str) -> None:
    state = failed_state()
    interrupted = interrupted_state().frontier.nodes[1].settlement
    replacement = {
        "succeeded": state.frontier.nodes[0].settlement,
        "pending": PendingGraphNode(UseStepRequestInput()),
        "interrupted": interrupted,
        "skipped": SkippedGraphNode(
            GraphFailure("already failed"),
            GraphSkipReason("already skipped"),
            ContinueGraphRouting(),
        ),
    }[settlement]
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                state.frontier.nodes[0],
                GraphFrontierNode(B, replacement),
                state.frontier.nodes[2],
            )
        ),
    )

    with pytest.raises(GraphStateTransitionError, match="does not match"):
        reduce_graph_run(
            state,
            ResumeGraphNodes(
                state.revision,
                (SkipFailedNode(B, GraphSkipReason("skip"), ContinueGraphRouting()),),
                None,
            ),
        )


@pytest.mark.parametrize(
    "action",
    [
        ResumeFailedNode(A, UseStepRequestInput()),
        ResumeInterruptedNode(
            A,
            GraphInterruptId("interrupt"),
            OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
        ),
    ],
)
def test_resume_action_must_match_current_settlement(action: GraphNodeResumeAction) -> None:
    state = failed_state()

    with pytest.raises(GraphStateTransitionError, match="does not match"):
        reduce_graph_run(state, ResumeGraphNodes(state.revision, (action,), None))


@pytest.mark.parametrize(
    "actions",
    [
        (),
        (ResumeFailedNode(C, UseStepRequestInput()), ResumeFailedNode(B, UseStepRequestInput())),
        (ResumeFailedNode(B, UseStepRequestInput()), ResumeFailedNode(B, UseStepRequestInput())),
        (ResumeFailedNode(GraphNodeId("missing"), UseStepRequestInput()),),
        (cast(GraphNodeResumeAction, object()),),
    ],
)
def test_resume_action_group_requires_nonempty_distinct_canonical_known_nodes(
    actions: tuple[GraphNodeResumeAction, ...],
) -> None:
    state = failed_state()

    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state, ResumeGraphNodes(state.revision, actions, None))


def test_any_invalid_action_rejects_entire_resume_group_atomically() -> None:
    state = failed_state()
    actions = (
        ResumeFailedNode(B, OverrideGraphNodeInput(GraphResumeInputPayload(b"valid"))),
        ResumeInterruptedNode(
            C,
            GraphInterruptId("not-an-interrupt"),
            OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
        ),
    )

    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state, ResumeGraphNodes(state.revision, actions, None))
    assert isinstance(state.frontier.nodes[1].settlement, FailedGraphNode)
    assert isinstance(state.frontier.nodes[2].settlement, FailedGraphNode)


def test_skip_preserves_failure_reason_and_routing_without_output() -> None:
    state = failed_state()
    routing = SelectGraphRoute(GraphRouteId("alternate"))

    skipped = reduce_graph_run(
        state,
        ResumeGraphNodes(
            state.revision,
            (SkipFailedNode(B, GraphSkipReason("operator"), routing),),
            None,
        ),
    )

    assert skipped.frontier.nodes[1].settlement == SkippedGraphNode(
        GraphFailure("b failed"),
        GraphSkipReason("operator"),
        routing,
    )


def test_skip_last_failure_requires_atomic_completion() -> None:
    leased = claim(start(A))
    state = settle(leased, (FailedGraphNodeOutcome(A, GraphFailure("failed")),))
    action = SkipFailedNode(A, GraphSkipReason("operator"), ContinueGraphRouting())

    with pytest.raises(GraphStateTransitionError, match="settled resume"):
        reduce_graph_run(state, ResumeGraphNodes(state.revision, (action,), None))

    completed = reduce_graph_run(
        state,
        ResumeGraphNodes(state.revision, (action,), CompleteGraphFrontier()),
    )
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.frontier == GraphFrontierState(())


def test_skip_last_failure_can_atomically_advance_with_all_routing_facts() -> None:
    leased = claim(start(A, B))
    state = settle(
        leased,
        (
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            FailedGraphNodeOutcome(B, GraphFailure("failed")),
        ),
    )
    progress = GraphJoinProgress((A, B), C, frozenset({A}))

    advanced = reduce_graph_run(
        state,
        ResumeGraphNodes(
            state.revision,
            (SkipFailedNode(B, GraphSkipReason("operator"), ContinueGraphRouting()),),
            AdvanceGraphFrontier((C,), (progress,)),
        ),
    )

    assert advanced.superstep == 1
    assert advanced.frontier == GraphFrontierState((GraphFrontierNode(C, PendingGraphNode(UseStepRequestInput())),))
    assert advanced.join_progress == (progress,)


def test_resume_rejects_resolution_until_frontier_is_fully_settled() -> None:
    state = failed_state()

    with pytest.raises(GraphStateTransitionError, match="unsettled"):
        reduce_graph_run(
            state,
            ResumeGraphNodes(
                state.revision,
                (ResumeFailedNode(B, UseStepRequestInput()),),
                CompleteGraphFrontier(),
            ),
        )


def test_resume_rejects_stale_revision_active_lease_and_resources() -> None:
    state = failed_state()
    action = ResumeFailedNode(B, UseStepRequestInput())
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(state, ResumeGraphNodes(state.revision + 1, (action,), None))

    pending = reduce_graph_run(state, ResumeGraphNodes(state.revision, (action,), None))
    leased = claim(pending)
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        reduce_graph_run(leased, ResumeGraphNodes(leased.revision, (ResumeFailedNode(C, UseStepRequestInput()),), None))

    admitted = replace(state, resources=ResourceSnapshot((ResourceLock(ResourceId("file")),)))
    with pytest.raises(GraphStateTransitionError, match="current pending nodes"):
        reduce_graph_run(admitted, ResumeGraphNodes(admitted.revision, (action,), None))


def test_failure_override_requires_durable_codec_but_default_does_not() -> None:
    leased = claim(start(A, codec=False))
    failed = settle(leased, (FailedGraphNodeOutcome(A, GraphFailure("failed")),))

    default = reduce_graph_run(
        failed,
        ResumeGraphNodes(failed.revision, (ResumeFailedNode(A, UseStepRequestInput()),), None),
    )
    assert isinstance(default.frontier.nodes[0].settlement, PendingGraphNode)

    with pytest.raises(GraphStateTransitionError, match="codec"):
        reduce_graph_run(
            failed,
            ResumeGraphNodes(
                failed.revision,
                (
                    ResumeFailedNode(
                        A,
                        OverrideGraphNodeInput(GraphResumeInputPayload(b"override")),
                    ),
                ),
                None,
            ),
        )


def test_consumed_interrupt_identity_cannot_be_resumed_again() -> None:
    state = interrupted_state()
    interrupted = state.frontier.nodes[1].settlement
    assert isinstance(interrupted, InterruptedGraphNode)
    identity = interrupted.interrupt.identity
    interrupt_id = graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )
    resumed = reduce_graph_run(
        state,
        ResumeGraphNodes(
            state.revision,
            (
                ResumeInterruptedNode(
                    B,
                    interrupt_id,
                    OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
                ),
            ),
            None,
        ),
    )

    with pytest.raises(GraphStateTransitionError, match="does not match"):
        reduce_graph_run(
            resumed,
            ResumeGraphNodes(
                resumed.revision,
                (
                    ResumeInterruptedNode(
                        B,
                        interrupt_id,
                        OverrideGraphNodeInput(GraphResumeInputPayload(b"again")),
                    ),
                ),
                None,
            ),
        )
