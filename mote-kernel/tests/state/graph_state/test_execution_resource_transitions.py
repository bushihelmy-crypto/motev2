from dataclasses import replace

import pytest

from mote_kernel.state.graph_state import (
    AcquireResources,
    ClaimGraphExecution,
    ContinueGraphRouting,
    FailedGraphNodeOutcome,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphFrontierActivation,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    SettleGraphNode,
    StartActivationCause,
    StartGraphRun,
    SucceededGraphNodeOutcome,
    reduce_graph_run,
    reduce_resources,
    validate_graph_run_state,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
FILE = ResourceId("file")
NETWORK = ResourceId("network")


def running(*nodes: GraphNodeId) -> GraphRunState:
    return reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            tuple(GraphFrontierActivation(node, StartActivationCause()) for node in nodes),
        ),
    )


def snapshot(*requests: tuple[GraphNodeId, tuple[ResourceId, ...]]) -> ResourceSnapshot:
    state = ResourceSnapshot((ResourceLock(FILE), ResourceLock(NETWORK)))
    for node_id, resources in requests:
        state = reduce_resources(state, AcquireResources(node_id, resources))
    return state


def claim(state: GraphRunState, resources: ResourceSnapshot | None) -> GraphRunState:
    return reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("attempt"), resources),
    )


def settle_success(state: GraphRunState, node_id: GraphNodeId) -> GraphRunState:
    assert state.execution is not None
    return reduce_graph_run(
        state,
        SettleGraphNode(
            state.revision,
            state.execution.token,
            SucceededGraphNodeOutcome(node_id, ContinueGraphRouting()),
        ),
    )


def test_claim_commits_resource_admission_and_execution_atomically() -> None:
    proposed = snapshot((A, (FILE,)), (B, (FILE,)))
    state = claim(running(A, B), proposed)
    assert state.execution is not None
    assert state.resources == proposed
    assert proposed.acquisitions[0].admitted
    assert not proposed.acquisitions[1].admitted
    assert state.revision == 1


def test_settlement_releases_owner_and_admits_waiter_in_the_same_revision() -> None:
    state = claim(running(A, B), snapshot((A, (FILE,)), (B, (FILE,))))
    released = settle_success(state, A)
    assert released.resources is not None
    assert tuple(item.node_id for item in released.resources.acquisitions) == (B,)
    assert released.resources.acquisitions[0].admitted
    assert released.resources.resources[0].owner == B
    assert released.execution == state.execution
    assert released.revision == state.revision + 1


def test_failure_uses_the_same_release_and_waiter_progression() -> None:
    state = claim(running(A, B), snapshot((A, (FILE,)), (B, (FILE,))))
    assert state.execution is not None
    failed = reduce_graph_run(
        state,
        SettleGraphNode(
            state.revision,
            state.execution.token,
            FailedGraphNodeOutcome(A, GraphFailure("failed")),
        ),
    )
    assert failed.resources is not None
    assert failed.resources.acquisitions[0].node_id == B
    assert failed.resources.acquisitions[0].admitted

    terminal = settle_success(failed, B)
    assert terminal.status is GraphRunStatus.FAILED
    assert terminal.execution is terminal.resources is None


def test_release_advances_a_waiters_multi_resource_prefix() -> None:
    proposed = snapshot((A, (NETWORK,)), (B, (FILE, NETWORK)))
    state = claim(running(A, B), proposed)
    waiting = state.resources.acquisitions[1] if state.resources is not None else None
    assert waiting is not None and waiting.acquired == (FILE,) and waiting.waiting_for == NETWORK
    released = settle_success(state, A)
    assert released.resources is not None
    assert released.resources.acquisitions[0].acquired == (FILE, NETWORK)
    assert released.resources.acquisitions[0].admitted


def test_settling_a_resource_free_node_preserves_all_live_acquisitions() -> None:
    proposed = snapshot((A, (FILE,)), (B, (FILE,)))
    state = claim(running(A, B, C), proposed)

    settled = settle_success(state, C)

    assert settled.resources is state.resources
    assert settled.execution == state.execution
    assert settled.resources == proposed


def test_settling_one_owner_advances_only_its_waiter_and_preserves_an_independent_owner() -> None:
    proposed = snapshot((A, (FILE,)), (B, (NETWORK,)), (C, (FILE,)))
    state = claim(running(A, B, C), proposed)

    settled = settle_success(state, A)

    assert settled.resources is not None
    acquisitions = {item.node_id: item for item in settled.resources.acquisitions}
    assert acquisitions[B].admitted
    assert acquisitions[C].admitted
    assert settled.resources.resources == (
        ResourceLock(FILE, C),
        ResourceLock(NETWORK, B),
    )


def test_settling_a_multi_resource_owner_admits_each_independent_waiter_atomically() -> None:
    proposed = snapshot((A, (FILE, NETWORK)), (B, (FILE,)), (C, (NETWORK,)))
    state = claim(running(A, B, C), proposed)

    settled = settle_success(state, A)

    assert settled.resources is not None
    assert tuple(item.node_id for item in settled.resources.acquisitions) == (B, C)
    assert all(item.admitted for item in settled.resources.acquisitions)
    assert settled.resources.resources == (
        ResourceLock(FILE, B),
        ResourceLock(NETWORK, C),
    )
    assert settled.revision == state.revision + 1


def test_last_resource_participant_normalizes_snapshot_to_none() -> None:
    state = claim(running(A), snapshot((A, (FILE,))))
    settled = settle_success(state, A)
    assert settled.resources is None
    assert settled.execution is None


def test_authoritative_state_rejects_empty_or_detached_resource_snapshot() -> None:
    state = running(A)
    empty = ResourceSnapshot((ResourceLock(FILE),))
    with pytest.raises(GraphStateTransitionError, match="empty resource snapshot"):
        claim(state, empty)
    with pytest.raises(GraphStateTransitionError, match="active execution"):
        validate_graph_run_state(replace(state, resources=snapshot((A, (FILE,)))))


def test_claim_rejects_resource_participant_outside_pending_frontier() -> None:
    with pytest.raises(GraphStateTransitionError, match="outside current pending"):
        claim(running(A), snapshot((B, (FILE,))))


def test_waiting_participant_cannot_report_completion() -> None:
    state = claim(running(A, B), snapshot((A, (FILE,)), (B, (FILE,))))
    assert state.execution is not None
    with pytest.raises(GraphStateTransitionError, match="release"):
        reduce_graph_run(
            state,
            SettleGraphNode(
                state.revision,
                state.execution.token,
                SucceededGraphNodeOutcome(B, ContinueGraphRouting()),
            ),
        )
