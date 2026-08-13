from dataclasses import replace

import pytest

from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    FailedGraphNode,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphNodeId,
    GraphNodeInterrupt,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphRunId,
    GraphRunStatus,
    GraphStateTransitionError,
    InterruptedGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    StartGraphRun,
    UpdateGraphResources,
    derive_graph_node_interrupt_identity,
    reduce_graph_run,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
FILE = ResourceId("file")
DATABASE = ResourceId("database")


def running():
    return reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (A, B),
        ),
    )


def first_admission() -> ResourceSnapshot:
    return ResourceSnapshot(
        (ResourceLock(FILE, A), ResourceLock(DATABASE)),
        (ResourceAcquisition(A, (FILE,), (FILE,)),),
    )


def extended_admission() -> ResourceSnapshot:
    return ResourceSnapshot(
        (ResourceLock(FILE, A, (B,)), ResourceLock(DATABASE)),
        (
            ResourceAcquisition(A, (FILE,), (FILE,)),
            ResourceAcquisition(B, (FILE,), (), FILE),
        ),
    )


def test_resource_admission_can_be_committed_then_replayably_extended() -> None:
    state = running()
    first = reduce_graph_run(state, UpdateGraphResources(state.revision, first_admission()))
    extended = reduce_graph_run(first, UpdateGraphResources(first.revision, extended_admission()))

    assert first.resources == first_admission()
    assert extended.resources == extended_admission()


def test_resource_admission_cannot_rewrite_prior_acquisitions() -> None:
    state = reduce_graph_run(running(), UpdateGraphResources(0, first_admission()))
    rewritten = ResourceSnapshot(
        (ResourceLock(FILE, B), ResourceLock(DATABASE)),
        (ResourceAcquisition(B, (FILE,), (FILE,)),),
    )
    with pytest.raises(GraphStateTransitionError, match="rewrite"):
        reduce_graph_run(state, UpdateGraphResources(state.revision, rewritten))


def test_resource_admission_requires_legal_replay_and_exact_result() -> None:
    state = running()
    illegal = ResourceSnapshot(
        (ResourceLock(FILE), ResourceLock(DATABASE)),
        (ResourceAcquisition(A, (DATABASE, FILE), (), DATABASE),),
    )
    with pytest.raises(GraphStateTransitionError, match="legal acquisition sequence"):
        reduce_graph_run(state, UpdateGraphResources(state.revision, illegal))

    structurally_different = ResourceSnapshot(
        (ResourceLock(FILE, A), ResourceLock(DATABASE)),
        (ResourceAcquisition(A, (FILE,), (FILE,), FILE),),
    )
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state, UpdateGraphResources(state.revision, structurally_different))


def test_resource_admission_rejects_terminal_or_active_execution() -> None:
    state = running()
    active = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("attempt"), (A, B)),
    )
    with pytest.raises(GraphStateTransitionError, match="during execution"):
        reduce_graph_run(active, UpdateGraphResources(active.revision, first_admission()))

    completed = replace(state, status=GraphRunStatus.COMPLETED, frontier=type(state.frontier)(()))
    with pytest.raises(GraphStateTransitionError, match="running"):
        reduce_graph_run(completed, UpdateGraphResources(completed.revision, first_admission()))


@pytest.mark.parametrize("settlement", ["failed", "interrupted"])
def test_resource_admission_rejects_frontier_without_pending_nodes(settlement: str) -> None:
    state = running()
    if settlement == "failed":
        blocked = replace(
            state,
            frontier=GraphFrontierState((GraphFrontierNode(A, FailedGraphNode(GraphFailure("failed"))),)),
        )
    else:
        blocked = replace(
            state,
            execution_sequence=1,
            resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1),
            frontier=GraphFrontierState(
                (
                    GraphFrontierNode(
                        A,
                        InterruptedGraphNode(
                            GraphNodeInterrupt(
                                derive_graph_node_interrupt_identity(state.run_id, state.superstep, A, 1),
                                GraphInterruptPayload(b"question"),
                            )
                        ),
                    ),
                )
            ),
        )

    with pytest.raises(GraphStateTransitionError, match="executable frontier"):
        reduce_graph_run(blocked, UpdateGraphResources(blocked.revision, ResourceSnapshot(())))
