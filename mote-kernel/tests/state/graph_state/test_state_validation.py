from dataclasses import replace
from enum import Enum, auto
from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    ActivationReference,
    ClaimGraphExecution,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphAbort,
    GraphAbortReason,
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
    GraphJoinProgress,
    GraphNodeId,
    GraphNodeInputBinding,
    GraphNodeInterrupt,
    GraphNodeInterruptIdentity,
    GraphNodeSettlement,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRouteId,
    GraphRoutingContribution,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    RoutedActivationCause,
    SelectGraphRoute,
    StartActivationCause,
    StartGraphRun,
    SucceededGraphNode,
    UseStepRequestInput,
    child_graph_run_id,
    frontier_status,
    reduce_graph_run,
    validate_graph_run_state,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
CODEC = GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1)


def arrival(source: GraphNodeId) -> ActivationReference:
    return ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, source))


def forged_reference(
    activation: object,
    route: object = None,
) -> ActivationReference:
    reference = object.__new__(ActivationReference)
    object.__setattr__(reference, "activation", activation)
    object.__setattr__(reference, "route", route)
    return reference


def forged_identity(run_id: object, superstep: object, node_id: object) -> GraphActivationIdentity:
    activation = object.__new__(GraphActivationIdentity)
    object.__setattr__(activation, "run_id", run_id)
    object.__setattr__(activation, "superstep", superstep)
    object.__setattr__(activation, "node_id", node_id)
    return activation


def forged_cause(references: object) -> RoutedActivationCause:
    cause = object.__new__(RoutedActivationCause)
    object.__setattr__(cause, "references", references)
    return cause


def running() -> GraphRunState:
    return reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphFrontierActivation(A, StartActivationCause()),),
            resume_input_codec=CODEC,
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"run_id": GraphRunId("")},
        {"definition_id": GraphDefinitionId(" graph")},
        {"definition_version": GraphDefinitionVersion(0)},
        {"revision": -1},
        {"parent": GraphActivationIdentity(GraphRunId("run"), 0, A)},
    ],
)
def test_invalid_run_identity_version_counter_and_parent_fail_closed(mutation: dict[str, object]) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(running(), **mutation))  # type: ignore[arg-type]


def test_parent_bearing_recovered_state_requires_deterministic_child_run_identity() -> None:
    parent = GraphActivationIdentity(GraphRunId("parent"), 3, GraphNodeId("nested"))
    child = replace(
        running(),
        run_id=child_graph_run_id(parent.run_id, parent.superstep, parent.node_id),
        parent=parent,
    )

    validate_graph_run_state(child)
    with pytest.raises(GraphStateTransitionError, match="child graph run identity"):
        validate_graph_run_state(replace(child, run_id=GraphRunId("arbitrary-child")))


def later_running() -> GraphRunState:
    return replace(
        running(),
        superstep=1,
        settled_activations=(arrival(A),),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    PendingGraphNode(UseStepRequestInput()),
                    RoutedActivationCause((arrival(A),)),
                ),
            )
        ),
    )


@pytest.mark.parametrize(
    "progress",
    [
        (
            GraphJoinProgress((A, C), GraphNodeId("z"), (arrival(A),)),
            GraphJoinProgress((A, B), C, (arrival(A),)),
        ),
        (GraphJoinProgress((), C, (arrival(A),)),),
        (GraphJoinProgress((A, A), C, (arrival(A),)),),
        (GraphJoinProgress((A, B), A, (arrival(B),)),),
        (GraphJoinProgress((GraphNodeId(" a"), B), C, (arrival(A),)),),
        (GraphJoinProgress((A, B), GraphNodeId(" c"), (arrival(A),)),),
        (GraphJoinProgress((A, B), C, ()),),
        (GraphJoinProgress((A, B), C, (arrival(A), arrival(B))),),
        (
            GraphJoinProgress((A, B), C, (arrival(A),)),
            GraphJoinProgress((A, B), C, (arrival(B),)),
        ),
        (
            GraphJoinProgress(
                (A, B),
                C,
                (ActivationReference(GraphActivationIdentity(GraphRunId("other"), 0, A)),),
            ),
        ),
        (
            GraphJoinProgress(
                (A, B),
                C,
                (forged_reference(GraphActivationIdentity(GraphRunId("run"), 0, A), GraphRouteId(" bad")),),
            ),
        ),
    ],
)
def test_invalid_join_progress_fails_closed(progress: tuple[GraphJoinProgress, ...]) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(later_running(), join_progress=progress))


def test_join_progress_accepts_canonical_predecessor_references() -> None:
    validate_graph_run_state(
        replace(
            later_running(),
            join_progress=(GraphJoinProgress((A, B), C, (arrival(A),)),),
        )
    )


@pytest.mark.parametrize(
    ("base", "cause", "message"),
    [
        (later_running(), StartActivationCause(), "START activation cause"),
        (later_running(), object(), "unsupported activation cause"),
        (running(), RoutedActivationCause((arrival(A),)), "initial frontier nodes"),
        (later_running(), forged_cause(()), "non-empty references"),
        (later_running(), forged_cause((object(),)), "invalid reference"),
        (
            later_running(),
            forged_cause((forged_reference(object()),)),
            "invalid activation identity",
        ),
        (
            later_running(),
            RoutedActivationCause((ActivationReference(forged_identity(GraphRunId("run"), -1, A)),)),
            "non-negative integer",
        ),
        (
            later_running(),
            forged_cause((arrival(A), arrival(A))),
            "canonical and distinct",
        ),
        (
            later_running(),
            RoutedActivationCause((ActivationReference(GraphActivationIdentity(GraphRunId("other"), 0, A)),)),
            "non-predecessor activation",
        ),
    ],
)
def test_recovered_activation_cause_validation_fails_closed(
    base: GraphRunState,
    cause: object,
    message: str,
) -> None:
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(
                A,
                PendingGraphNode(UseStepRequestInput()),
                cast(StartActivationCause | RoutedActivationCause, cause),
            ),
        )
    )

    with pytest.raises(GraphStateTransitionError, match=message):
        validate_graph_run_state(replace(base, frontier=frontier))


def test_recovered_activation_cause_requires_settlement_evidence() -> None:
    base = replace(later_running(), settled_activations=())
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(
                A,
                PendingGraphNode(UseStepRequestInput()),
                RoutedActivationCause((arrival(A),)),
            ),
        )
    )

    with pytest.raises(GraphStateTransitionError, match="lacks committed settlement evidence"):
        validate_graph_run_state(replace(base, frontier=frontier))


@pytest.mark.parametrize(
    "case",
    [
        "duplicate-node",
        "invalid-node-id",
        "invalid-success-route",
        "empty-failure",
    ],
)
def test_frontier_identity_and_value_objects_fail_closed(case: str) -> None:
    base = running()
    invalid_frontier = {
        "duplicate-node": GraphFrontierState((base.frontier.nodes[0], base.frontier.nodes[0])),
        "invalid-node-id": GraphFrontierState(
            (GraphFrontierNode(GraphNodeId(" bad"), PendingGraphNode(UseStepRequestInput()), StartActivationCause()),)
        ),
        "invalid-success-route": GraphFrontierState(
            (GraphFrontierNode(A, SucceededGraphNode(SelectGraphRoute(GraphRouteId(""))), StartActivationCause()),)
        ),
        "empty-failure": GraphFrontierState(
            (GraphFrontierNode(A, FailedGraphNode(GraphFailure("")), StartActivationCause()),)
        ),
    }[case]
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(base, frontier=invalid_frontier))


def test_failed_status_accepts_success_and_failure_diagnostics() -> None:
    base = running()
    diagnostic = GraphFrontierState(
        (
            GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),
            GraphFrontierNode(B, FailedGraphNode(GraphFailure("blocked")), StartActivationCause()),
        )
    )
    validate_graph_run_state(replace(base, status=GraphRunStatus.FAILED, frontier=diagnostic))


@pytest.mark.parametrize(
    ("frontier", "message"),
    [
        (
            GraphFrontierState((GraphFrontierNode(A, cast(GraphNodeSettlement, object()), StartActivationCause()),)),
            "unsupported settlement",
        ),
        (
            GraphFrontierState(
                (GraphFrontierNode(A, PendingGraphNode(cast(GraphNodeInputBinding, object())), StartActivationCause()),)
            ),
            "unsupported input binding",
        ),
        (
            GraphFrontierState(
                (
                    GraphFrontierNode(
                        A,
                        SucceededGraphNode(cast(GraphRoutingContribution, object())),
                        StartActivationCause(),
                    ),
                    GraphFrontierNode(B, FailedGraphNode(GraphFailure("blocked")), StartActivationCause()),
                )
            ),
            "unsupported routing contribution",
        ),
    ],
)
def test_frontier_rejects_unsupported_typed_union_variants(
    frontier: GraphFrontierState,
    message: str,
) -> None:
    with pytest.raises(GraphStateTransitionError, match=message):
        validate_graph_run_state(replace(running(), frontier=frontier))


@pytest.mark.parametrize(
    "coordinate",
    ["run", "superstep", "node", "zero-generation", "future-generation"],
)
def test_interrupt_identity_generation_and_codec_invariants(coordinate: str) -> None:
    base = replace(running(), execution_sequence=2)
    identity = {
        "run": GraphNodeInterruptIdentity(GraphRunId("other"), 0, A, 1),
        "superstep": GraphNodeInterruptIdentity(base.run_id, 1, A, 1),
        "node": GraphNodeInterruptIdentity(base.run_id, 0, B, 1),
        "zero-generation": GraphNodeInterruptIdentity(base.run_id, 0, A, 0),
        "future-generation": GraphNodeInterruptIdentity(base.run_id, 0, A, 3),
    }[coordinate]
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(
                A,
                InterruptedGraphNode(GraphNodeInterrupt(identity, GraphInterruptPayload(b"q"))),
                StartActivationCause(),
            ),
        )
    )
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(base, frontier=frontier))


@pytest.mark.parametrize("case", ["missing", "invalid-identity", "invalid-version"])
def test_override_and_codec_metadata_invariants(case: str) -> None:
    base = running()
    override = GraphFrontierState(
        (
            GraphFrontierNode(
                A,
                PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"value"))),
                StartActivationCause(),
            ),
        )
    )
    candidate = {
        "missing": replace(base, frontier=override, resume_input_codec=None),
        "invalid-identity": replace(
            base,
            resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId(""), 1),
        ),
        "invalid-version": replace(
            base,
            resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input"), 0),
        ),
    }[case]

    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(candidate)


@pytest.mark.parametrize("case", ["override", "interrupt"])
def test_opaque_frontier_payloads_must_be_bytes(case: str) -> None:
    base = replace(running(), execution_sequence=1)
    invalid = {
        "override": GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    PendingGraphNode(
                        OverrideGraphNodeInput(cast(GraphResumeInputPayload, "not-bytes")),
                    ),
                    StartActivationCause(),
                ),
            )
        ),
        "interrupt": GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    InterruptedGraphNode(
                        GraphNodeInterrupt(
                            GraphNodeInterruptIdentity(base.run_id, base.superstep, A, 1),
                            cast(GraphInterruptPayload, "not-bytes"),
                        )
                    ),
                    StartActivationCause(),
                ),
            )
        ),
    }[case]

    with pytest.raises(GraphStateTransitionError, match="opaque bytes"):
        validate_graph_run_state(replace(base, frontier=invalid))


def test_historical_interrupt_generation_remains_valid_after_a_sibling_attempt() -> None:
    base = replace(running(), execution_sequence=3)
    historical = GraphNodeInterruptIdentity(base.run_id, base.superstep, A, 1)
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(
                A,
                InterruptedGraphNode(GraphNodeInterrupt(historical, GraphInterruptPayload(b"question"))),
                StartActivationCause(),
            ),
            GraphFrontierNode(B, FailedGraphNode(GraphFailure("sibling failed later")), StartActivationCause()),
        )
    )

    validate_graph_run_state(replace(base, status=GraphRunStatus.FAILED, frontier=frontier))


def test_pending_can_coexist_with_failed_and_interrupted_settlements() -> None:
    base = replace(running(), execution_sequence=1)
    failed_frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()), StartActivationCause()),
            GraphFrontierNode(B, FailedGraphNode(GraphFailure("failed")), StartActivationCause()),
        )
    )
    interrupted_frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput()), StartActivationCause()),
            GraphFrontierNode(
                B,
                InterruptedGraphNode(
                    GraphNodeInterrupt(
                        GraphNodeInterruptIdentity(base.run_id, base.superstep, B, 1),
                        GraphInterruptPayload(b"question"),
                    )
                ),
                StartActivationCause(),
            ),
        )
    )

    for frontier in (failed_frontier, interrupted_frontier):
        candidate = replace(base, frontier=frontier)
        validate_graph_run_state(candidate)
        assert frontier_status(candidate.frontier).name == "EXECUTABLE"


def test_resource_membership_and_replay_invariants() -> None:
    base = running()
    resource = ResourceId("file")
    corrupt = ResourceSnapshot((ResourceLock(resource, GraphNodeId("foreign")),))
    with pytest.raises(GraphStateTransitionError, match="resources state"):
        validate_graph_run_state(replace(base, resources=corrupt))

    foreign = ResourceSnapshot(
        (ResourceLock(resource, B),),
        (ResourceAcquisition(B, (resource,), (resource,)),),
    )
    with pytest.raises(GraphStateTransitionError, match="active execution"):
        validate_graph_run_state(replace(base, resources=foreign))


def test_authoritative_resource_shape_guards_are_independently_enforced() -> None:
    base = running()
    resource = ResourceId("file")
    token = GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))
    execution = GraphExecutionLease(token)

    with pytest.raises(GraphStateTransitionError, match="cannot be empty"):
        validate_graph_run_state(replace(base, resources=ResourceSnapshot((ResourceLock(resource),))))

    owned_by_a = ResourceSnapshot(
        (ResourceLock(resource, A),),
        (ResourceAcquisition(A, (resource,), (resource,)),),
    )
    settled = replace(
        base,
        execution_sequence=1,
        execution=execution,
        resources=owned_by_a,
        frontier=GraphFrontierState(
            (GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),)
        ),
    )
    with pytest.raises(GraphStateTransitionError, match="current pending"):
        validate_graph_run_state(settled)

    owned_by_b = ResourceSnapshot(
        (ResourceLock(resource, B),),
        (ResourceAcquisition(B, (resource,), (resource,)),),
    )
    with pytest.raises(GraphStateTransitionError, match="outside current pending"):
        validate_graph_run_state(
            replace(
                base,
                execution_sequence=1,
                execution=execution,
                resources=owned_by_b,
            )
        )


@pytest.mark.parametrize("case", ["historical-generation", "empty-attempt", "no-pending"])
def test_execution_lease_invariants(case: str) -> None:
    base = running()
    token = GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))
    invalid = {
        "historical-generation": replace(
            base,
            execution_sequence=2,
            execution=GraphExecutionLease(token),
        ),
        "empty-attempt": replace(
            base,
            execution_sequence=1,
            execution=GraphExecutionLease(GraphExecutionToken(1, GraphExecutionAttemptId(""))),
        ),
        "no-pending": replace(
            base,
            execution_sequence=1,
            frontier=GraphFrontierState(()),
            execution=GraphExecutionLease(token),
        ),
    }[case]
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(invalid)


def test_completed_lifecycle_rejects_execution_lease() -> None:
    base = running()
    token = GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))
    completed = replace(base, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))
    completed_with_execution = replace(
        completed,
        execution_sequence=1,
        execution=GraphExecutionLease(token),
    )
    with pytest.raises(GraphStateTransitionError, match="only a running graph"):
        validate_graph_run_state(completed_with_execution)


@pytest.mark.parametrize(
    "case",
    [
        "running-empty-frontier",
        "running-abort",
        "running-failed-frontier",
        "completed-frontier",
        "completed-abort",
        "failed-pending-frontier",
        "failed-without-failure",
        "failed-abort",
        "aborted-without-reason",
        "aborted-empty-reason",
    ],
)
def test_lifecycle_invariants_fail_closed(case: str) -> None:
    base = running()
    completed = replace(base, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))
    invalid = {
        "running-empty-frontier": replace(base, frontier=GraphFrontierState(())),
        "running-abort": replace(base, abort=GraphAbort(GraphAbortReason("abort"))),
        "running-failed-frontier": replace(
            base,
            frontier=GraphFrontierState(
                (GraphFrontierNode(A, FailedGraphNode(GraphFailure("failed")), StartActivationCause()),)
            ),
        ),
        "completed-frontier": replace(completed, frontier=base.frontier),
        "completed-abort": replace(completed, abort=GraphAbort(GraphAbortReason("abort"))),
        "failed-pending-frontier": replace(base, status=GraphRunStatus.FAILED),
        "failed-without-failure": replace(
            base,
            status=GraphRunStatus.FAILED,
            frontier=GraphFrontierState(
                (GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),)
            ),
        ),
        "failed-abort": replace(
            base,
            status=GraphRunStatus.FAILED,
            frontier=GraphFrontierState(
                (GraphFrontierNode(A, FailedGraphNode(GraphFailure("failed")), StartActivationCause()),)
            ),
            abort=GraphAbort(GraphAbortReason("abort")),
        ),
        "aborted-without-reason": replace(base, status=GraphRunStatus.ABORTED, abort=None),
        "aborted-empty-reason": replace(
            base,
            status=GraphRunStatus.ABORTED,
            abort=GraphAbort(GraphAbortReason("")),
        ),
    }[case]
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(invalid)


def test_running_settled_frontier_is_a_valid_quiescent_recovery_boundary() -> None:
    base = running()
    settled = replace(
        base,
        frontier=GraphFrontierState(
            (GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),)
        ),
    )
    validate_graph_run_state(settled)


def test_running_settled_frontier_rejects_a_retained_execution_lease() -> None:
    base = running()
    settled = replace(
        base,
        execution_sequence=1,
        execution=GraphExecutionLease(GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))),
        frontier=GraphFrontierState(
            (GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),)
        ),
    )
    with pytest.raises(GraphStateTransitionError, match="active execution lease"):
        validate_graph_run_state(settled)


def test_unsupported_lifecycle_variant_fails_closed() -> None:
    base = running()

    class ForgedStatus(Enum):
        UNKNOWN = auto()

    with pytest.raises(GraphStateTransitionError, match="unsupported lifecycle"):
        validate_graph_run_state(replace(base, status=cast(GraphRunStatus, ForgedStatus.UNKNOWN)))


def test_reducer_rejects_start_over_existing_and_transition_before_start() -> None:
    command = StartGraphRun(
        GraphRunId("run"),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(1),
        (GraphFrontierActivation(A, StartActivationCause()),),
    )
    with pytest.raises(GraphStateTransitionError, match="started again"):
        reduce_graph_run(running(), command)
    with pytest.raises(GraphStateTransitionError, match="must be started"):
        reduce_graph_run(
            None,
            ClaimGraphExecution(
                0,
                GraphExecutionAttemptId("attempt"),
                cast(ResourceSnapshot, (A,)),
            ),
        )


def test_empty_frontier_has_no_derived_status() -> None:
    with pytest.raises(ValueError, match="no valid derived status"):
        frontier_status(GraphFrontierState(()))


def test_settled_activation_evidence_rejects_malformed_storage_shapes() -> None:
    base = replace(running(), superstep=2)

    with pytest.raises(GraphStateTransitionError, match="evidence must be a tuple"):
        validate_graph_run_state(replace(base, settled_activations=cast(tuple[ActivationReference, ...], [])))

    with pytest.raises(GraphStateTransitionError, match="invalid reference"):
        validate_graph_run_state(replace(base, settled_activations=cast(tuple[ActivationReference, ...], (object(),))))

    unhashable = forged_reference(
        GraphActivationIdentity(GraphRunId("run"), 0, A),
        cast(GraphRouteId, []),
    )
    with pytest.raises(GraphStateTransitionError, match="unhashable value"):
        validate_graph_run_state(replace(base, settled_activations=(unhashable,)))


def test_settled_activation_evidence_must_be_canonical_and_unique() -> None:
    base = replace(running(), superstep=2)
    first = arrival(A)
    second = ActivationReference(GraphActivationIdentity(GraphRunId("run"), 1, B))

    with pytest.raises(GraphStateTransitionError, match="canonical and distinct"):
        validate_graph_run_state(replace(base, settled_activations=(second, first)))

    duplicate_activation = (
        ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, A)),
        ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, A), GraphRouteId("retry")),
    )
    with pytest.raises(GraphStateTransitionError, match="repeats one activation"):
        validate_graph_run_state(replace(base, settled_activations=duplicate_activation))


def test_current_settled_activation_evidence_matches_the_frontier_success() -> None:
    base = running()
    current = arrival(A)

    with pytest.raises(GraphStateTransitionError, match="invalid coordinate"):
        validate_graph_run_state(
            replace(
                base,
                settled_activations=(ActivationReference(GraphActivationIdentity(GraphRunId("other"), 0, A)),),
            )
        )

    with pytest.raises(GraphStateTransitionError, match="no successful frontier node"):
        validate_graph_run_state(replace(base, settled_activations=(current,)))

    selected = replace(
        base,
        frontier=GraphFrontierState(
            (GraphFrontierNode(A, SucceededGraphNode(SelectGraphRoute(GraphRouteId("ok"))), StartActivationCause()),)
        ),
    )
    with pytest.raises(GraphStateTransitionError, match="route does not match"):
        validate_graph_run_state(
            replace(selected, settled_activations=(ActivationReference(current.activation, GraphRouteId("other")),))
        )


def test_join_progress_rejects_duplicate_records_after_each_record_is_validated() -> None:
    base = replace(
        later_running(),
        settled_activations=(arrival(A),),
        join_progress=(
            GraphJoinProgress((A, B), C, (arrival(A),)),
            GraphJoinProgress((A, B), C, (arrival(A),)),
        ),
    )

    with pytest.raises(GraphStateTransitionError, match="repeats join progress"):
        validate_graph_run_state(base)
