from dataclasses import replace
from enum import Enum, auto
from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphAbort,
    GraphAbortReason,
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
    GraphNodeInputBinding,
    GraphNodeInterrupt,
    GraphNodeSettlement,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRouteId,
    GraphRoutingContribution,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    GraphStateTransitionError,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    ParentGraphActivation,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    SelectGraphRoute,
    SkippedGraphNode,
    StartGraphRun,
    SucceededGraphNode,
    UseStepRequestInput,
    child_graph_run_id,
    derive_graph_node_interrupt_identity,
    frontier_status,
    reduce_graph_run,
    validate_graph_run_state,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
CODEC = GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1)


def running() -> GraphRunState:
    return reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (A,),
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
        {"parent": ParentGraphActivation(GraphRunId("run"), 0, A)},
    ],
)
def test_invalid_run_identity_version_counter_and_parent_fail_closed(mutation: dict[str, object]) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(running(), **mutation))  # type: ignore[arg-type]


def test_parent_bearing_recovered_state_requires_deterministic_child_run_identity() -> None:
    parent = ParentGraphActivation(GraphRunId("parent"), 3, GraphNodeId("nested"))
    child = replace(
        running(),
        run_id=child_graph_run_id(parent.run_id, parent.superstep, parent.node_id),
        parent=parent,
    )

    validate_graph_run_state(child)
    with pytest.raises(GraphStateTransitionError, match="child graph run identity"):
        validate_graph_run_state(replace(child, run_id=GraphRunId("arbitrary-child")))


@pytest.mark.parametrize(
    "progress",
    [
        (
            GraphJoinProgress((A, C), GraphNodeId("z"), frozenset({A})),
            GraphJoinProgress((A, B), C, frozenset({A})),
        ),
        (GraphJoinProgress((), C, frozenset({A})),),
        (GraphJoinProgress((A, A), C, frozenset({A})),),
        (GraphJoinProgress((A, B), A, frozenset({B})),),
        (GraphJoinProgress((GraphNodeId(" a"), B), C, frozenset({GraphNodeId(" a")})),),
        (GraphJoinProgress((A, B), GraphNodeId(" c"), frozenset({A})),),
        (GraphJoinProgress((A, B), C, frozenset()),),
        (GraphJoinProgress((A, B), C, frozenset({A, B})),),
        (
            GraphJoinProgress((A, B), C, frozenset({A})),
            GraphJoinProgress((A, B), C, frozenset({B})),
        ),
    ],
)
def test_invalid_join_progress_fails_closed(progress: tuple[GraphJoinProgress, ...]) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(running(), join_progress=progress))


@pytest.mark.parametrize(
    "case",
    [
        "duplicate-node",
        "invalid-node-id",
        "invalid-success-route",
        "empty-failure",
        "empty-skipped-failure",
        "empty-skip-reason",
        "invalid-skipped-route",
    ],
)
def test_frontier_identity_and_value_objects_fail_closed(case: str) -> None:
    base = running()
    invalid_frontier = {
        "duplicate-node": GraphFrontierState((base.frontier.nodes[0], base.frontier.nodes[0])),
        "invalid-node-id": GraphFrontierState(
            (GraphFrontierNode(GraphNodeId(" bad"), PendingGraphNode(UseStepRequestInput())),)
        ),
        "invalid-success-route": GraphFrontierState(
            (GraphFrontierNode(A, SucceededGraphNode(SelectGraphRoute(GraphRouteId("")))),)
        ),
        "empty-failure": GraphFrontierState((GraphFrontierNode(A, FailedGraphNode(GraphFailure(""))),)),
        "empty-skipped-failure": GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    SkippedGraphNode(GraphFailure(""), GraphSkipReason("reason"), ContinueGraphRouting()),
                ),
            )
        ),
        "empty-skip-reason": GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    SkippedGraphNode(GraphFailure("failure"), GraphSkipReason(""), ContinueGraphRouting()),
                ),
            )
        ),
        "invalid-skipped-route": GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    SkippedGraphNode(
                        GraphFailure("failure"),
                        GraphSkipReason("reason"),
                        SelectGraphRoute(GraphRouteId("")),
                    ),
                ),
            )
        ),
    }[case]
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(base, frontier=invalid_frontier))


def test_valid_skipped_settlement_can_coexist_with_failure() -> None:
    base = running()
    valid_skipped = GraphFrontierState(
        (
            GraphFrontierNode(
                A,
                SkippedGraphNode(
                    GraphFailure("failure"),
                    GraphSkipReason("reason"),
                    ContinueGraphRouting(),
                ),
            ),
            GraphFrontierNode(B, FailedGraphNode(GraphFailure("blocked"))),
        )
    )
    validate_graph_run_state(replace(base, frontier=valid_skipped))


@pytest.mark.parametrize(
    ("frontier", "message"),
    [
        (
            GraphFrontierState((GraphFrontierNode(A, cast(GraphNodeSettlement, object())),)),
            "unsupported settlement",
        ),
        (
            GraphFrontierState((GraphFrontierNode(A, PendingGraphNode(cast(GraphNodeInputBinding, object()))),)),
            "unsupported input binding",
        ),
        (
            GraphFrontierState(
                (
                    GraphFrontierNode(
                        A,
                        SucceededGraphNode(cast(GraphRoutingContribution, object())),
                    ),
                    GraphFrontierNode(B, FailedGraphNode(GraphFailure("blocked"))),
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
        "run": derive_graph_node_interrupt_identity(GraphRunId("other"), 0, A, 1),
        "superstep": derive_graph_node_interrupt_identity(base.run_id, 1, A, 1),
        "node": derive_graph_node_interrupt_identity(base.run_id, 0, B, 1),
        "zero-generation": derive_graph_node_interrupt_identity(base.run_id, 0, A, 0),
        "future-generation": derive_graph_node_interrupt_identity(base.run_id, 0, A, 3),
    }[coordinate]
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(
                A,
                InterruptedGraphNode(GraphNodeInterrupt(identity, GraphInterruptPayload(b"q"))),
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
                ),
            )
        ),
        "interrupt": GraphFrontierState(
            (
                GraphFrontierNode(
                    A,
                    InterruptedGraphNode(
                        GraphNodeInterrupt(
                            derive_graph_node_interrupt_identity(base.run_id, base.superstep, A, 1),
                            cast(GraphInterruptPayload, "not-bytes"),
                        )
                    ),
                ),
            )
        ),
    }[case]

    with pytest.raises(GraphStateTransitionError, match="opaque bytes"):
        validate_graph_run_state(replace(base, frontier=invalid))


def test_historical_interrupt_generation_remains_valid_after_a_sibling_attempt() -> None:
    base = replace(running(), execution_sequence=3)
    historical = derive_graph_node_interrupt_identity(base.run_id, base.superstep, A, 1)
    frontier = GraphFrontierState(
        (
            GraphFrontierNode(
                A,
                InterruptedGraphNode(GraphNodeInterrupt(historical, GraphInterruptPayload(b"question"))),
            ),
            GraphFrontierNode(B, FailedGraphNode(GraphFailure("sibling failed later"))),
        )
    )

    validate_graph_run_state(replace(base, frontier=frontier))


def test_pending_can_coexist_with_failed_and_interrupted_settlements() -> None:
    base = replace(running(), execution_sequence=1)
    failed_frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput())),
            GraphFrontierNode(B, FailedGraphNode(GraphFailure("failed"))),
        )
    )
    interrupted_frontier = GraphFrontierState(
        (
            GraphFrontierNode(A, PendingGraphNode(UseStepRequestInput())),
            GraphFrontierNode(
                B,
                InterruptedGraphNode(
                    GraphNodeInterrupt(
                        derive_graph_node_interrupt_identity(base.run_id, base.superstep, B, 1),
                        GraphInterruptPayload(b"question"),
                    )
                ),
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
    with pytest.raises(GraphStateTransitionError, match="outside current pending"):
        validate_graph_run_state(replace(base, resources=foreign))


@pytest.mark.parametrize("case", ["historical-generation", "empty-attempt", "wrong-node"])
def test_execution_lease_invariants(case: str) -> None:
    base = running()
    token = GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))
    invalid = {
        "historical-generation": replace(
            base,
            execution_sequence=2,
            execution=GraphExecutionLease(token, (A,)),
        ),
        "empty-attempt": replace(
            base,
            execution_sequence=1,
            execution=GraphExecutionLease(GraphExecutionToken(1, GraphExecutionAttemptId("")), (A,)),
        ),
        "wrong-node": replace(
            base,
            execution_sequence=1,
            execution=GraphExecutionLease(token, (B,)),
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
        execution=GraphExecutionLease(token, ()),
    )
    with pytest.raises(GraphStateTransitionError, match="only a running graph"):
        validate_graph_run_state(completed_with_execution)


@pytest.mark.parametrize(
    "case",
    [
        "running-empty-frontier",
        "running-settled-frontier",
        "running-abort",
        "completed-frontier",
        "completed-abort",
        "aborted-without-reason",
        "aborted-empty-reason",
    ],
)
def test_lifecycle_invariants_fail_closed(case: str) -> None:
    base = running()
    completed = replace(base, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))
    invalid = {
        "running-empty-frontier": replace(base, frontier=GraphFrontierState(())),
        "running-settled-frontier": replace(
            base,
            frontier=GraphFrontierState((GraphFrontierNode(A, SucceededGraphNode(ContinueGraphRouting())),)),
        ),
        "running-abort": replace(base, abort=GraphAbort(GraphAbortReason("abort"))),
        "completed-frontier": replace(completed, frontier=base.frontier),
        "completed-abort": replace(completed, abort=GraphAbort(GraphAbortReason("abort"))),
        "aborted-without-reason": replace(base, status=GraphRunStatus.ABORTED, abort=None),
        "aborted-empty-reason": replace(
            base,
            status=GraphRunStatus.ABORTED,
            abort=GraphAbort(GraphAbortReason("")),
        ),
    }[case]
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(invalid)


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
        (A,),
    )
    with pytest.raises(GraphStateTransitionError, match="started again"):
        reduce_graph_run(running(), command)
    with pytest.raises(GraphStateTransitionError, match="must be started"):
        reduce_graph_run(None, ClaimGraphExecution(0, GraphExecutionAttemptId("attempt"), (A,)))


def test_empty_frontier_has_no_derived_status() -> None:
    with pytest.raises(ValueError, match="no valid derived status"):
        frontier_status(GraphFrontierState(()))
