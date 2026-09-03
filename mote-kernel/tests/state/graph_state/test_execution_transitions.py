# pyright: reportPrivateUsage=false

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

import mote_kernel.state.graph_state.execution_transitions as transitions
from mote_kernel.execution.graph.constants import END
from mote_kernel.state.graph_state import (
    ActivationReference,
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    FailedGraphNodeOutcome,
    FenceGraphExecution,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierActivation,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphJoinIdentity,
    GraphJoinOccurrenceIdentity,
    GraphJoinProgress,
    GraphNodeId,
    GraphNodeInterruptIdentity,
    GraphNodeOutcome,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    InterruptedGraphNode,
    InterruptedGraphNodeOutcome,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    RoutedActivationCause,
    SelectGraphRoute,
    SettleGraphNode,
    StartActivationCause,
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


def join_occurrence(
    sources: tuple[GraphNodeId, ...] = (A, B),
    target: GraphNodeId = C,
    *,
    target_superstep: int = 2,
) -> GraphJoinOccurrenceIdentity:
    return GraphJoinOccurrenceIdentity(
        GraphJoinIdentity(sources, target),
        GraphRunId("run"),
        target_superstep,
    )


def join_progress(
    arrived: tuple[ActivationReference, ...],
    *,
    occurrence: GraphJoinOccurrenceIdentity | None = None,
) -> GraphJoinProgress:
    return GraphJoinProgress(occurrence or join_occurrence(), arrived)


def running(*nodes: GraphNodeId, codec: bool = True) -> GraphRunState:
    return reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            tuple(GraphFrontierActivation(node, StartActivationCause()) for node in nodes),
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
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()), StartActivationCause()),
            GraphFrontierNode(B, PendingGraphNode(UseStepRequestInput()), StartActivationCause()),
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


def test_start_installs_the_explicit_state_owned_activation_cause() -> None:
    command = StartGraphRun(
        GraphRunId("run"),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(1),
        (GraphFrontierActivation(A, StartActivationCause()),),
    )

    state = reduce_graph_run(None, command)

    assert state.frontier.nodes[0].activation == command.activations[0]


def test_start_rejects_a_routed_activation_cause() -> None:
    command = StartGraphRun(
        GraphRunId("run"),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(1),
        (_routed_activation(),),
    )

    with pytest.raises(GraphStateTransitionError, match="initial activations must use the START cause"):
        reduce_graph_run(None, command)


def _routed_activation(
    node_id: GraphNodeId = A,
    *,
    superstep: int = 0,
    route: str = "continue",
    source_node_id: GraphNodeId | None = None,
) -> GraphFrontierActivation:
    reference = ActivationReference(
        GraphActivationIdentity(GraphRunId("run"), superstep, source_node_id or node_id),
        GraphRouteId(route),
    )
    return GraphFrontierActivation(node_id, RoutedActivationCause((reference,)))


def _continue_activation(
    node_id: GraphNodeId,
    *,
    source_node_id: GraphNodeId = A,
    superstep: int = 0,
) -> GraphFrontierActivation:
    return GraphFrontierActivation(
        node_id,
        RoutedActivationCause(
            (
                ActivationReference(
                    GraphActivationIdentity(GraphRunId("run"), superstep, source_node_id),
                ),
            )
        ),
    )


def test_advance_preserves_and_validates_the_successful_predecessor_cause() -> None:
    settled = settle(
        claim(running(A)),
        SucceededGraphNodeOutcome(A, SelectGraphRoute(GraphRouteId("continue"))),
    )
    activation = _routed_activation()

    advanced = reduce_graph_run(
        settled,
        AdvanceGraphFrontier(settled.revision, (activation,), ()),
    )

    assert advanced.frontier.nodes[0].activation == activation


def test_each_feedback_round_persists_only_its_immediate_predecessor_cause() -> None:
    first_settlement = settle(
        claim(running(A)),
        SucceededGraphNodeOutcome(A, SelectGraphRoute(GraphRouteId("continue"))),
    )
    first_activation = _routed_activation(superstep=0)
    first_repeat = reduce_graph_run(
        first_settlement,
        AdvanceGraphFrontier(first_settlement.revision, (first_activation,), ()),
    )
    second_settlement = settle(
        claim(first_repeat, attempt="second"),
        SucceededGraphNodeOutcome(A, SelectGraphRoute(GraphRouteId("continue"))),
    )
    second_activation = _routed_activation(superstep=1)

    second_repeat = reduce_graph_run(
        second_settlement,
        AdvanceGraphFrontier(second_settlement.revision, (second_activation,), ()),
    )

    assert first_repeat.frontier.nodes[0].activation == first_activation
    assert second_repeat.frontier.nodes[0].activation == second_activation
    assert isinstance(first_repeat.frontier.nodes[0].cause, RoutedActivationCause)
    assert isinstance(second_repeat.frontier.nodes[0].cause, RoutedActivationCause)
    assert first_repeat.frontier.nodes[0].cause.references[0].activation == GraphActivationIdentity(
        GraphRunId("run"), 0, A
    )
    assert second_repeat.frontier.nodes[0].cause.references[0].activation == GraphActivationIdentity(
        GraphRunId("run"), 1, A
    )


@pytest.mark.parametrize(
    "activation, match",
    [
        (_routed_activation(source_node_id=B), "not a settled success"),
        (_routed_activation(route="other"), "route does not match"),
        (_routed_activation(superstep=1), "wrong frontier"),
    ],
)
def test_advance_rejects_a_forged_predecessor_cause(
    activation: GraphFrontierActivation,
    match: str,
) -> None:
    settled = settle(
        claim(running(A)),
        SucceededGraphNodeOutcome(A, SelectGraphRoute(GraphRouteId("continue"))),
    )

    with pytest.raises(GraphStateTransitionError, match=match):
        reduce_graph_run(
            settled,
            AdvanceGraphFrontier(settled.revision, (activation,), ()),
        )


@pytest.mark.parametrize(
    ("activation", "match"),
    [
        (
            GraphFrontierActivation(
                A,
                RoutedActivationCause(
                    (
                        ActivationReference(
                            GraphActivationIdentity(GraphRunId("other"), 0, A),
                            GraphRouteId("continue"),
                        ),
                    )
                ),
            ),
            "wrong frontier",
        ),
    ],
)
def test_advance_rejects_a_foreign_run_activation(
    activation: GraphFrontierActivation,
    match: str,
) -> None:
    settled = settle(
        claim(running(A)),
        SucceededGraphNodeOutcome(A, SelectGraphRoute(GraphRouteId("continue"))),
    )

    with pytest.raises(GraphStateTransitionError, match=match):
        reduce_graph_run(
            settled,
            AdvanceGraphFrontier(settled.revision, (activation,), ()),
        )


def test_advance_rejects_a_frontier_that_contains_a_failed_source() -> None:
    leased = claim(running(A))
    failed = settle(leased, FailedGraphNodeOutcome(A, GraphFailure("failed")))
    activation = _routed_activation()

    with pytest.raises(GraphStateTransitionError, match="running graph"):
        # A failed source makes the run terminal.  The reducer must reject the
        # advance before it can manufacture a cause.
        reduce_graph_run(
            failed,
            AdvanceGraphFrontier(failed.revision, (activation,), ()),
        )


def test_advance_rejects_a_start_cause_after_the_initial_frontier() -> None:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))

    with pytest.raises(GraphStateTransitionError, match="must carry routed causes"):
        reduce_graph_run(
            settled,
            AdvanceGraphFrontier(
                settled.revision,
                (GraphFrontierActivation(B, StartActivationCause()),),
                (),
            ),
        )


def test_advance_requires_one_current_source_alongside_historical_causes() -> None:
    first = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    repeated = reduce_graph_run(
        first,
        AdvanceGraphFrontier(first.revision, (_continue_activation(A),), ()),
    )
    settled = settle(claim(repeated, attempt="second"), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    historical_only = GraphFrontierActivation(
        B,
        RoutedActivationCause((ActivationReference(GraphActivationIdentity(settled.run_id, 0, A)),)),
    )

    with pytest.raises(GraphStateTransitionError, match="requires a current settled source"):
        reduce_graph_run(
            settled,
            AdvanceGraphFrontier(settled.revision, (historical_only,), ()),
        )


def test_advance_requires_historical_causes_to_complete_one_persisted_join() -> None:
    first = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    repeated = reduce_graph_run(
        first,
        AdvanceGraphFrontier(first.revision, (_continue_activation(A),), ()),
    )
    settled = settle(claim(repeated, attempt="second"), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    activation = GraphFrontierActivation(
        C,
        RoutedActivationCause(
            (
                ActivationReference(GraphActivationIdentity(settled.run_id, 0, B)),
                ActivationReference(GraphActivationIdentity(settled.run_id, 1, A)),
            ),
            join_occurrence(),
        ),
    )

    with pytest.raises(GraphStateTransitionError, match="lack matching pending progress"):
        reduce_graph_run(
            settled,
            AdvanceGraphFrontier(settled.revision, (activation,), ()),
        )


@pytest.mark.parametrize(
    "command",
    [
        StartGraphRun(
            GraphRunId(""),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphFrontierActivation(A, StartActivationCause()),),
        ),
        StartGraphRun(
            GraphRunId(" run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphFrontierActivation(A, StartActivationCause()),),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId(""),
            GraphDefinitionVersion(1),
            (GraphFrontierActivation(A, StartActivationCause()),),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(0),
            (GraphFrontierActivation(A, StartActivationCause()),),
        ),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), ()),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (
                GraphFrontierActivation(B, StartActivationCause()),
                GraphFrontierActivation(A, StartActivationCause()),
            ),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (
                GraphFrontierActivation(A, StartActivationCause()),
                GraphFrontierActivation(A, StartActivationCause()),
            ),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphFrontierActivation(A, StartActivationCause()),),
            resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId(""), 1),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphFrontierActivation(A, StartActivationCause()),),
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
    with pytest.raises(GraphStateTransitionError, match="quiescent running"):
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


def test_failed_node_keeps_the_same_claim_until_every_sibling_settles() -> None:
    leased = claim(running(A, B))
    assert leased.execution is not None
    token = leased.execution.token

    partially_failed = settle(leased, FailedGraphNodeOutcome(A, GraphFailure("failed")))

    assert partially_failed.status is GraphRunStatus.RUNNING
    assert isinstance(partially_failed.frontier.nodes[0].settlement, FailedGraphNode)
    assert isinstance(partially_failed.frontier.nodes[1].settlement, PendingGraphNode)
    assert partially_failed.execution == GraphExecutionLease(token)
    assert frontier_status(partially_failed.frontier).name == "EXECUTABLE"

    terminal = settle(partially_failed, SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    assert terminal.status is GraphRunStatus.FAILED
    assert terminal.execution is terminal.resources is None
    assert frontier_status(terminal.frontier).name == "FAILED"


def test_each_typed_outcome_uses_the_same_single_node_transition() -> None:
    success = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    assert frontier_status(success.frontier).name == "SETTLED"
    failure = settle(claim(running(A)), FailedGraphNodeOutcome(A, GraphFailure("failed")))
    assert isinstance(failure.frontier.nodes[0].settlement, FailedGraphNode)
    assert failure.status is GraphRunStatus.FAILED
    assert frontier_status(failure.frontier).name == "FAILED"
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


def test_failure_has_terminal_priority_after_an_interrupted_sibling_settles() -> None:
    leased = claim(running(A, B))
    assert leased.execution is not None
    partially_failed = settle(leased, FailedGraphNodeOutcome(A, GraphFailure("failed")))
    identity = GraphNodeInterruptIdentity(
        partially_failed.run_id,
        partially_failed.superstep,
        B,
        leased.execution.token.generation,
    )

    terminal = settle(
        partially_failed,
        InterruptedGraphNodeOutcome(B, identity, GraphInterruptPayload(b"question")),
    )

    assert terminal.status is GraphRunStatus.FAILED
    assert isinstance(terminal.frontier.nodes[0].settlement, FailedGraphNode)
    assert isinstance(terminal.frontier.nodes[1].settlement, InterruptedGraphNode)
    assert terminal.execution is terminal.resources is None


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
    reference = ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, A))
    progress = join_progress((reference,))
    activation = _continue_activation(B)
    advanced = reduce_graph_run(
        settled,
        AdvanceGraphFrontier(settled.revision, (activation,), (progress,)),
    )
    assert advanced.superstep == 1
    assert advanced.frontier == GraphFrontierState(
        (GraphFrontierNode(B, PendingGraphNode(UseStepRequestInput()), activation.cause),)
    )
    assert advanced.join_progress == (progress,)


def _partial_join_state() -> tuple[GraphRunState, GraphJoinProgress]:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    reference = ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, A))
    progress = join_progress((reference,))
    advanced = reduce_graph_run(
        settled,
        AdvanceGraphFrontier(settled.revision, (_continue_activation(B),), (progress,)),
    )
    return advanced, progress


def test_advance_preserves_unrelated_partial_join_progress() -> None:
    settled_a = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    reference = ActivationReference(GraphActivationIdentity(settled_a.run_id, settled_a.superstep, A))
    progress = join_progress((reference,), occurrence=join_occurrence(target_superstep=3))
    partial = reduce_graph_run(
        settled_a,
        AdvanceGraphFrontier(settled_a.revision, (_continue_activation(B),), (progress,)),
    )
    settled = settle(claim(partial, attempt="join-source"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    activation = GraphFrontierActivation(
        C,
        RoutedActivationCause((ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, B)),)),
    )

    advanced = reduce_graph_run(
        settled,
        AdvanceGraphFrontier(settled.revision, (activation,), (progress,)),
    )

    assert advanced.join_progress == (progress,)


def test_advance_rejects_dropping_unrelated_partial_join_progress() -> None:
    partial, _progress = _partial_join_state()
    settled = settle(claim(partial, attempt="join-source"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    activation = GraphFrontierActivation(
        C,
        RoutedActivationCause((ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, B)),)),
    )

    with pytest.raises(GraphStateTransitionError, match="cannot be discarded"):
        reduce_graph_run(settled, AdvanceGraphFrontier(settled.revision, (activation,), ()))


def test_advance_rejects_injecting_a_historical_join_arrival() -> None:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    forged = ActivationReference(GraphActivationIdentity(settled.run_id, 0, B))
    activation = _continue_activation(C)

    with pytest.raises(GraphStateTransitionError, match="current settled successes"):
        reduce_graph_run(
            settled,
            AdvanceGraphFrontier(
                settled.revision,
                (activation,),
                (join_progress((forged,)),),
            ),
        )


def test_advance_rejects_replacing_a_historical_join_arrival() -> None:
    partial, progress = _partial_join_state()
    settled = settle(claim(partial, attempt="join-source"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    replacement = GraphJoinProgress(
        progress.occurrence,
        (ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, B)),),
    )
    activation = GraphFrontierActivation(
        C,
        RoutedActivationCause((ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, B)),)),
    )

    with pytest.raises(GraphStateTransitionError, match="remove or replace"):
        reduce_graph_run(settled, AdvanceGraphFrontier(settled.revision, (activation,), (replacement,)))


def test_advance_consumes_partial_join_progress_only_with_the_complete_join_activation() -> None:
    partial, progress = _partial_join_state()
    settled = settle(claim(partial, attempt="join-source"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    activation = GraphFrontierActivation(
        C,
        RoutedActivationCause(
            (
                progress.arrived[0],
                ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, B)),
            ),
            progress.occurrence,
        ),
    )

    advanced = reduce_graph_run(settled, AdvanceGraphFrontier(settled.revision, (activation,), ()))

    assert advanced.join_progress == ()
    assert advanced.frontier.nodes[0].activation == activation


def test_advance_rejects_retaining_a_join_progress_record_after_consumption() -> None:
    partial, progress = _partial_join_state()
    settled = settle(claim(partial, attempt="join-source"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    activation = GraphFrontierActivation(
        C,
        RoutedActivationCause(
            (
                progress.arrived[0],
                ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, B)),
            ),
            progress.occurrence,
        ),
    )

    with pytest.raises(GraphStateTransitionError, match="cannot be retained"):
        reduce_graph_run(settled, AdvanceGraphFrontier(settled.revision, (activation,), (progress,)))


def test_complete_rejects_discarding_unresolved_join_progress_atomically() -> None:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    reference = ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, A))
    progressed = reduce_graph_run(
        settled,
        AdvanceGraphFrontier(
            settled.revision,
            (_continue_activation(B),),
            (join_progress((reference,)),),
        ),
    )
    invalid = settle(claim(progressed), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    with pytest.raises(GraphStateTransitionError, match="unresolved join"):
        reduce_graph_run(invalid, CompleteGraphFrontier(invalid.revision))
    assert invalid.frontier.nodes


def _partial_terminal_join_state() -> tuple[GraphRunState, GraphJoinProgress]:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    reference = ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, A))
    progress = join_progress(
        (reference,),
        occurrence=join_occurrence(target=GraphNodeId(END)),
    )
    advanced = reduce_graph_run(
        settled,
        AdvanceGraphFrontier(settled.revision, (_continue_activation(B),), (progress,)),
    )
    return advanced, progress


def test_complete_consumes_a_terminal_join_progress_with_an_explicit_proof() -> None:
    partial, progress = _partial_terminal_join_state()
    settled = settle(claim(partial, attempt="terminal-join"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))

    completed = reduce_graph_run(
        settled,
        CompleteGraphFrontier(settled.revision, (progress.occurrence,)),
    )

    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.join_progress == ()


def test_advance_consumes_a_terminal_join_progress_before_an_unrelated_successor() -> None:
    partial, progress = _partial_terminal_join_state()
    settled = settle(claim(partial, attempt="terminal-join"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    successor = _continue_activation(C, source_node_id=B, superstep=settled.superstep)

    advanced = reduce_graph_run(
        settled,
        AdvanceGraphFrontier(
            settled.revision,
            (successor,),
            (),
            (progress.occurrence,),
        ),
    )

    assert advanced.join_progress == ()
    assert advanced.frontier.nodes[0].activation == successor


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
                    StartActivationCause(),
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
    parent = GraphActivationIdentity(GraphRunId("parent"), 2, GraphNodeId("nested"))
    command = StartGraphRun(
        child_graph_run_id(parent.run_id, parent.superstep, parent.node_id),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(1),
        (GraphFrontierActivation(A, StartActivationCause()),),
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


@pytest.mark.parametrize(
    "activations",
    [
        (),
        (_continue_activation(B), _continue_activation(A)),
        (_continue_activation(B), _continue_activation(B)),
    ],
)
def test_advance_rejects_each_noncanonical_next_frontier(
    activations: tuple[GraphFrontierActivation, ...],
) -> None:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))

    with pytest.raises(GraphStateTransitionError, match=r"malformed|canonical"):
        reduce_graph_run(settled, AdvanceGraphFrontier(settled.revision, activations, ()))


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
        claim_graph_execution,
        complete_graph_frontier,
        fence_graph_execution,
        settle_graph_node,
    )

    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    with pytest.raises(GraphStateTransitionError, match="executable frontier"):
        claim_graph_execution(settled, ClaimGraphExecution(settled.revision, ATTEMPT, None))
    with pytest.raises(GraphStateTransitionError, match="running graph"):
        complete_graph_frontier(replace(settled, status=GraphRunStatus.COMPLETED), CompleteGraphFrontier(0))
    with pytest.raises(GraphStateTransitionError, match="settled frontier"):
        advance_graph_frontier(running(A), AdvanceGraphFrontier(0, (_continue_activation(B),), ()))
    active = claim(running(A))
    assert active.execution is not None
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        complete_graph_frontier(
            replace(settled, execution=active.execution),
            CompleteGraphFrontier(settled.revision),
        )
    with pytest.raises(GraphStateTransitionError, match="canonical"):
        advance_graph_frontier(
            settled,
            AdvanceGraphFrontier(
                settled.revision,
                (_continue_activation(B), _continue_activation(B)),
                (),
            ),
        )
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


def _forged_unhashable_reference() -> ActivationReference:
    reference = object.__new__(ActivationReference)
    object.__setattr__(reference, "activation", GraphActivationIdentity(GraphRunId("run"), 0, A))
    object.__setattr__(reference, "route", cast(GraphRouteId, []))
    return reference


def _arrival(node_id: GraphNodeId, superstep: int = 0) -> ActivationReference:
    return ActivationReference(GraphActivationIdentity(GraphRunId("run"), superstep, node_id))


def test_next_activation_rejects_duplicate_source_occurrences() -> None:
    settled = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    cause = object.__new__(RoutedActivationCause)
    object.__setattr__(
        cause,
        "references",
        (
            ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, A)),
            ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, A), GraphRouteId("other")),
        ),
    )
    object.__setattr__(cause, "join_occurrence", None)
    activation = GraphFrontierActivation(B, cause)

    with pytest.raises(GraphStateTransitionError, match="repeat one source"):
        transitions._validate_next_activations(settled, (activation,))


def test_next_activation_rejects_multi_source_non_join_and_wrong_join_coordinates() -> None:
    active = claim(running(A, B))
    settled_a = settle(active, SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    settled = settle(settled_a, SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    references = (_arrival(A), _arrival(B))

    ordinary = object.__new__(RoutedActivationCause)
    object.__setattr__(ordinary, "references", references)
    object.__setattr__(ordinary, "join_occurrence", None)
    with pytest.raises(GraphStateTransitionError, match="non-Join activation cause"):
        transitions._validate_next_activations(settled, (GraphFrontierActivation(C, ordinary),))

    wrong_target = GraphNodeId("other-target")
    wrong_occurrence = GraphJoinOccurrenceIdentity(
        GraphJoinIdentity((A, B), wrong_target),
        settled.run_id,
        settled.superstep + 1,
    )
    with pytest.raises(GraphStateTransitionError, match="wrong occurrence identity"):
        transitions._validate_next_activations(
            settled,
            (GraphFrontierActivation(C, RoutedActivationCause(references, wrong_occurrence)),),
        )


def test_next_join_activation_history_must_equal_its_pending_progress() -> None:
    partial, progress = _partial_join_state()
    settled = settle(claim(partial, attempt="join-history"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    mismatched_history = ActivationReference(
        GraphActivationIdentity(settled.run_id, 0, A),
        GraphRouteId("different-route"),
    )
    current = ActivationReference(GraphActivationIdentity(settled.run_id, settled.superstep, B))
    activation = GraphFrontierActivation(
        C,
        RoutedActivationCause((mismatched_history, current), progress.occurrence),
    )

    with pytest.raises(GraphStateTransitionError, match="history does not match"):
        transitions._validate_next_activations(settled, (activation,))


def test_join_progress_index_rejects_each_malformed_record_shape() -> None:
    with pytest.raises(GraphStateTransitionError, match="must be a tuple"):
        transitions._index_join_progress(cast(tuple[GraphJoinProgress, ...], []), "state")
    with pytest.raises(GraphStateTransitionError, match="malformed record"):
        transitions._index_join_progress(cast(tuple[GraphJoinProgress, ...], (object(),)), "state")
    malformed_occurrence = object.__new__(GraphJoinOccurrenceIdentity)
    object.__setattr__(malformed_occurrence, "join", cast(GraphJoinIdentity, []))
    object.__setattr__(malformed_occurrence, "run_id", GraphRunId("run"))
    object.__setattr__(malformed_occurrence, "target_superstep", 2)
    malformed_key = GraphJoinProgress(malformed_occurrence, ())
    with pytest.raises(GraphStateTransitionError, match="unhashable key"):
        transitions._index_join_progress((malformed_key,), "state")
    valid = join_progress((_arrival(A),))
    with pytest.raises(GraphStateTransitionError, match="repeats one join"):
        transitions._index_join_progress((valid, valid), "state")


def test_current_success_reference_scan_ignores_non_successful_nodes() -> None:
    assert transitions._current_successful_references(running(A)) == frozenset()


def test_join_progress_delta_rejects_overlap_unhashable_values_and_noncurrent_additions() -> None:
    partial, progress = _partial_join_state()
    settled = settle(claim(partial, attempt="join-source"), SucceededGraphNodeOutcome(B, ContinueGraphRouting()))
    key = progress.occurrence
    with pytest.raises(GraphStateTransitionError, match="more than once"):
        transitions._validate_join_progress_delta(settled, (), frozenset({key}), (key,))

    settled_a = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    malformed = join_progress((_forged_unhashable_reference(),))
    with pytest.raises(GraphStateTransitionError, match="unhashable arrivals"):
        transitions._validate_join_progress_delta(settled_a, (malformed,), frozenset(), ())

    prior_bad = replace(settled_a, join_progress=(malformed,))
    valid = join_progress((_arrival(A),))
    with pytest.raises(GraphStateTransitionError, match="state join progress contains unhashable arrivals"):
        transitions._validate_join_progress_delta(prior_bad, (valid,), frozenset(), ())

    partial_again, prior = _partial_join_state()
    settled_b = settle(
        claim(partial_again, attempt="join-source-2"),
        SucceededGraphNodeOutcome(B, ContinueGraphRouting()),
    )
    extra = ActivationReference(GraphActivationIdentity(settled_b.run_id, 0, C))
    changed = GraphJoinProgress(prior.occurrence, (prior.arrived[0], extra))
    with pytest.raises(GraphStateTransitionError, match="current settled successes"):
        transitions._validate_join_progress_delta(settled_b, (changed,), frozenset(), ())


def test_join_consumption_rejects_malformed_missing_incomplete_duplicate_and_unsorted_keys() -> None:
    state = settle(claim(running(A)), SucceededGraphNodeOutcome(A, ContinueGraphRouting()))
    key = join_occurrence(target_superstep=1)
    with pytest.raises(GraphStateTransitionError, match="must be a tuple"):
        transitions._validate_join_consumption(
            state,
            cast(tuple[GraphJoinOccurrenceIdentity, ...], []),
            frozenset(),
        )
    with pytest.raises(GraphStateTransitionError, match="occurrence is malformed"):
        transitions._validate_join_consumption(
            state,
            (cast(GraphJoinOccurrenceIdentity, "bad"),),
            frozenset(),
        )
    unhashable = object.__new__(GraphJoinOccurrenceIdentity)
    object.__setattr__(unhashable, "join", cast(GraphJoinIdentity, []))
    object.__setattr__(unhashable, "run_id", state.run_id)
    object.__setattr__(unhashable, "target_superstep", state.superstep + 1)
    with pytest.raises(GraphStateTransitionError, match="occurrence is malformed"):
        transitions._validate_join_consumption(state, (unhashable,), frozenset())
    with pytest.raises(GraphStateTransitionError, match="wrong target coordinate"):
        transitions._validate_join_consumption(
            state,
            (join_occurrence(target_superstep=state.superstep + 2),),
            frozenset(),
        )
    with pytest.raises(GraphStateTransitionError, match="does not exist"):
        transitions._validate_join_consumption(state, (key,), frozenset())

    progress = join_progress((_arrival(A),), occurrence=key)
    complete = replace(state, join_progress=(progress,))
    with pytest.raises(GraphStateTransitionError, match="not complete"):
        transitions._validate_join_consumption(complete, (key,), frozenset())

    complete_from_other_source = replace(
        settle(claim(running(B)), SucceededGraphNodeOutcome(B, ContinueGraphRouting())),
        join_progress=(progress,),
    )
    with pytest.raises(GraphStateTransitionError, match="canonical and distinct"):
        transitions._validate_join_consumption(
            complete_from_other_source,
            (key, key),
            transitions._current_successful_references(complete_from_other_source),
        )

    duplicate_source_state = settle(
        claim(
            reduce_graph_run(
                state,
                AdvanceGraphFrontier(state.revision, (_continue_activation(A),), ()),
            ),
            attempt="repeat",
        ),
        SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
    )
    duplicate_source_progress = replace(
        duplicate_source_state,
        join_progress=(join_progress((_arrival(A),)),),
    )
    with pytest.raises(GraphStateTransitionError, match="repeats one source"):
        transitions._validate_join_consumption(
            duplicate_source_progress,
            (duplicate_source_progress.join_progress[0].occurrence,),
            transitions._current_successful_references(duplicate_source_progress),
        )

    first_key = join_occurrence(target_superstep=1)
    second_key = join_occurrence((B, C), GraphNodeId("d"), target_superstep=1)
    both = replace(
        settle(claim(running(B, GraphNodeId("d"))), SucceededGraphNodeOutcome(B, ContinueGraphRouting())),
        join_progress=(
            join_progress((_arrival(A),), occurrence=first_key),
            join_progress((_arrival(C),), occurrence=second_key),
        ),
    )
    with pytest.raises(GraphStateTransitionError, match="canonical order"):
        transitions._validate_join_consumption(
            both,
            (second_key, first_key),
            transitions._current_successful_references(both),
        )


def test_settlement_rejects_reusing_a_committed_success_evidence_entry() -> None:
    leased = claim(running(A))
    assert leased.execution is not None
    duplicate = replace(
        leased,
        settled_activations=(ActivationReference(GraphActivationIdentity(leased.run_id, 0, A)),),
    )

    with pytest.raises(GraphStateTransitionError, match="already been committed"):
        transitions.settle_graph_node(
            duplicate,
            SettleGraphNode(
                duplicate.revision,
                leased.execution.token,
                SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            ),
        )
