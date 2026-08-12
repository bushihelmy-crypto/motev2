from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace

import pytest

from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AcquireResources,
    AdvanceGraphRun,
    ClaimGraphExecution,
    CompleteGraphRun,
    FailGraphExecution,
    FenceGraphExecution,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphInterruptId,
    GraphInterruptIdentity,
    GraphInterruptLifecycle,
    GraphInterruptPayload,
    GraphInterruptReceipt,
    GraphInterruptRecord,
    GraphJoinProgress,
    GraphNodeId,
    GraphResolutionCodec,
    GraphResolutionCodecId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    GraphTaskId,
    ParentGraphTask,
    ParticipantId,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    StartGraphRun,
    UpdateGraphResources,
    reduce_graph_run,
    reduce_resources,
)
from mote_kernel.state.graph_state.command import RequestGraphRunInterrupt, ResolveGraphRunInterrupt
from mote_kernel.state.graph_state.validation import validate_graph_run_state

FILE = ResourceId("file")
DATABASE = ResourceId("database")
CODEC = GraphResolutionCodec(GraphResolutionCodecId("input.v1"), 1)
ATTEMPT = GraphExecutionAttemptId("attempt-a")
TASK = GraphTaskId("task-a")
INVALID_JOIN_PROGRESS = (
    GraphJoinProgress((), GraphNodeId("c"), frozenset({GraphNodeId("a")})),
    GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("a")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    ),
    GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("a"),
        frozenset({GraphNodeId("b")}),
    ),
    GraphJoinProgress(
        (GraphNodeId(" a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId(" a")}),
    ),
    GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId(" c"),
        frozenset({GraphNodeId("a")}),
    ),
    GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset(),
    ),
    GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a"), GraphNodeId("b")}),
    ),
    GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("unknown")}),
    ),
)


def interrupt(
    lifecycle: GraphInterruptLifecycle,
    *,
    generation: int = 2,
    receipt: GraphInterruptReceipt | None = None,
) -> GraphInterruptRecord:
    return GraphInterruptRecord(
        GraphInterruptIdentity(GraphRunId("run"), GraphInterruptId("pause"), generation),
        GraphInterruptPayload(b"request"),
        CODEC,
        lifecycle,
        (
            GraphInterruptPayload(b"resolution")
            if lifecycle in {GraphInterruptLifecycle.RESOLVED, GraphInterruptLifecycle.CONSUMED}
            else None
        ),
        receipt,
    )


def running_state(
    *,
    superstep: int = 0,
    frontier: tuple[GraphNodeId, ...] = (GraphNodeId("a"),),
    resources: ResourceSnapshot | None = None,
    execution_sequence: int = 0,
    execution: GraphExecutionLease | None = None,
    interrupt_record: GraphInterruptRecord | None = None,
    resolution_codec: GraphResolutionCodec | None = None,
) -> GraphRunState:
    return GraphRunState(
        GraphRunId("run"),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        superstep,
        frontier,
        resources=resources,
        execution_sequence=execution_sequence,
        execution=execution,
        interrupt=interrupt_record,
        resolution_codec=resolution_codec,
    )


def recovered_state(status: GraphRunStatus) -> GraphRunState:
    if status is GraphRunStatus.RUNNING:
        return running_state()
    if status is GraphRunStatus.SUSPENDED:
        return replace(
            running_state(
                interrupt_record=interrupt(GraphInterruptLifecycle.REQUESTED),
                resolution_codec=CODEC,
            ),
            status=status,
        )
    if status is GraphRunStatus.COMPLETED:
        return replace(running_state(), status=status, frontier=())
    return replace(
        running_state(),
        status=status,
        frontier=(),
        failure=GraphFailure("failed"),
    )


def with_negative_superstep(state: GraphRunState) -> GraphRunState:
    return replace(state, superstep=-1)


def with_duplicate_frontier(state: GraphRunState) -> GraphRunState:
    return replace(state, frontier=(GraphNodeId("a"), GraphNodeId("a")))


def claim(
    state: GraphRunState,
    *,
    attempt_id: GraphExecutionAttemptId = ATTEMPT,
    task_ids: tuple[GraphTaskId, ...] = (TASK,),
) -> GraphRunState:
    return reduce_graph_run(
        state,
        ClaimGraphExecution(
            state.revision,
            attempt_id,
            task_ids,
        ),
    )


def token(state: GraphRunState) -> GraphExecutionToken:
    assert state.execution is not None
    return state.execution.token


def acquired(task_id: GraphTaskId = TASK) -> ResourceSnapshot:
    participant = ParticipantId(task_id)
    return ResourceSnapshot(
        (ResourceLock(FILE, participant),),
        (ResourceAcquisition(participant, (FILE,), (FILE,)),),
    )


def released() -> ResourceSnapshot:
    return ResourceSnapshot((ResourceLock(FILE),))


def test_interrupt_request_and_resolution_are_pure_graph_run_transitions() -> None:
    initial = running_state(resolution_codec=CODEC)
    identity = GraphInterruptIdentity(initial.run_id, GraphInterruptId("pause"), 1)

    suspended = reduce_graph_run(
        initial,
        RequestGraphRunInterrupt(initial.revision, identity, GraphInterruptPayload(b"request")),
    )
    resumed = reduce_graph_run(
        suspended,
        ResolveGraphRunInterrupt(
            suspended.revision,
            identity,
            GraphInterruptPayload(b"approved"),
        ),
    )

    assert initial.status is GraphRunStatus.RUNNING
    assert suspended.status is GraphRunStatus.SUSPENDED
    assert suspended.interrupt == GraphInterruptRecord(
        identity,
        GraphInterruptPayload(b"request"),
        CODEC,
        GraphInterruptLifecycle.REQUESTED,
    )
    assert resumed.status is GraphRunStatus.RUNNING
    assert resumed.interrupt == GraphInterruptRecord(
        identity,
        GraphInterruptPayload(b"request"),
        CODEC,
        GraphInterruptLifecycle.RESOLVED,
        GraphInterruptPayload(b"approved"),
    )


def test_interrupt_round_trip_preserves_the_recoverable_graph_position() -> None:
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    )
    parent = ParentGraphTask(GraphRunId("parent"), GraphTaskId("parent-task"))
    initial = replace(
        running_state(superstep=4, frontier=(GraphNodeId("b"),), resolution_codec=CODEC),
        parent=parent,
        join_progress=(progress,),
    )
    identity = GraphInterruptIdentity(initial.run_id, GraphInterruptId("pause"), 1)

    suspended = reduce_graph_run(
        initial,
        RequestGraphRunInterrupt(initial.revision, identity, GraphInterruptPayload(b"request")),
    )
    resumed = reduce_graph_run(
        suspended,
        ResolveGraphRunInterrupt(suspended.revision, identity, GraphInterruptPayload(b"resolution")),
    )

    for state in (suspended, resumed):
        assert state.superstep == 4
        assert state.frontier == (GraphNodeId("b"),)
        assert state.join_progress == (progress,)
        assert state.parent == parent


def test_interrupt_request_requires_codec_fenced_execution_and_monotonic_identity() -> None:
    identity = GraphInterruptIdentity(GraphRunId("run"), GraphInterruptId("pause"), 1)
    command = RequestGraphRunInterrupt(0, identity, GraphInterruptPayload(b"request"))
    invalid_states = (
        running_state(),
        claim(running_state(resolution_codec=CODEC)),
    )
    for invalid in invalid_states:
        with pytest.raises(GraphStateTransitionError):
            reduce_graph_run(invalid, command)

    consumed = interrupt(
        GraphInterruptLifecycle.CONSUMED,
        generation=2,
        receipt=GraphInterruptReceipt(0),
    )
    with pytest.raises(GraphStateTransitionError, match="monotonically"):
        reduce_graph_run(
            running_state(superstep=1, interrupt_record=consumed, resolution_codec=CODEC),
            RequestGraphRunInterrupt(0, identity, GraphInterruptPayload(b"request")),
        )


def test_interrupt_request_atomically_releases_unclaimed_resource_admission() -> None:
    initial = running_state(resources=acquired(), resolution_codec=CODEC)
    identity = GraphInterruptIdentity(initial.run_id, GraphInterruptId("pause"), 1)

    suspended = reduce_graph_run(
        initial,
        RequestGraphRunInterrupt(0, identity, GraphInterruptPayload(b"request")),
    )

    assert suspended.status is GraphRunStatus.SUSPENDED
    assert suspended.resources is None


@pytest.mark.parametrize(
    "identity",
    [
        GraphInterruptIdentity(GraphRunId(""), GraphInterruptId("pause"), 1),
        GraphInterruptIdentity(GraphRunId("run"), GraphInterruptId(""), 1),
        GraphInterruptIdentity(GraphRunId("run"), GraphInterruptId("pause"), 0),
    ],
)
def test_interrupt_request_rejects_invalid_durable_identity(identity: GraphInterruptIdentity) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(
            running_state(resolution_codec=CODEC),
            RequestGraphRunInterrupt(0, identity, GraphInterruptPayload(b"request")),
        )


def test_interrupt_resolution_requires_exact_suspended_generation() -> None:
    initial = running_state(resolution_codec=CODEC)
    identity = GraphInterruptIdentity(initial.run_id, GraphInterruptId("pause"), 1)
    suspended = reduce_graph_run(
        initial,
        RequestGraphRunInterrupt(0, identity, GraphInterruptPayload(b"request")),
    )

    with pytest.raises(GraphStateTransitionError, match="suspended generation"):
        reduce_graph_run(
            suspended,
            ResolveGraphRunInterrupt(
                suspended.revision,
                GraphInterruptIdentity(initial.run_id, GraphInterruptId("other"), 1),
                GraphInterruptPayload(b"approved"),
            ),
        )
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(
            suspended,
            ResolveGraphRunInterrupt(0, identity, GraphInterruptPayload(b"approved")),
        )

    resolved = reduce_graph_run(
        suspended,
        ResolveGraphRunInterrupt(suspended.revision, identity, GraphInterruptPayload(b"approved")),
    )
    with pytest.raises(GraphStateTransitionError, match="suspended generation"):
        reduce_graph_run(
            resolved,
            ResolveGraphRunInterrupt(resolved.revision, identity, GraphInterruptPayload(b"duplicate")),
        )


def test_interrupt_request_rejects_wrong_status_stale_step_and_unfinished_resolution() -> None:
    identity = GraphInterruptIdentity(GraphRunId("run"), GraphInterruptId("pause"), 2)
    command = RequestGraphRunInterrupt(0, identity, GraphInterruptPayload(b"request"))
    completed = replace(
        running_state(resolution_codec=CODEC),
        status=GraphRunStatus.COMPLETED,
        frontier=(),
    )
    with pytest.raises(GraphStateTransitionError, match="only a running"):
        reduce_graph_run(completed, command)
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(
            running_state(resolution_codec=CODEC),
            RequestGraphRunInterrupt(1, identity, GraphInterruptPayload(b"request")),
        )
    unresolved = interrupt(GraphInterruptLifecycle.RESOLVED, generation=1)
    with pytest.raises(GraphStateTransitionError, match="unfinished"):
        reduce_graph_run(
            running_state(interrupt_record=unresolved, resolution_codec=CODEC),
            command,
        )


@pytest.mark.parametrize(
    "prepared_command",
    [
        ClaimGraphExecution(0, ATTEMPT, (TASK,)),
        UpdateGraphResources(0, released()),
    ],
)
def test_revision_fences_prepared_claim_and_admission_across_interrupt_round_trip(
    prepared_command: ClaimGraphExecution | UpdateGraphResources,
) -> None:
    initial = running_state(resolution_codec=CODEC)
    identity = GraphInterruptIdentity(initial.run_id, GraphInterruptId("pause"), 1)
    suspended = reduce_graph_run(
        initial,
        RequestGraphRunInterrupt(0, identity, GraphInterruptPayload(b"request")),
    )

    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(suspended, prepared_command)

    resumed = reduce_graph_run(
        suspended,
        ResolveGraphRunInterrupt(suspended.revision, identity, GraphInterruptPayload(b"resolution")),
    )
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(resumed, prepared_command)


def test_start_creates_sorted_running_state_with_fixed_codec() -> None:
    command = StartGraphRun(
        GraphRunId("child"),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(2),
        (GraphNodeId("b"), GraphNodeId("a")),
        ParentGraphTask(GraphRunId("root"), GraphTaskId("parent-task")),
        CODEC,
    )

    state = reduce_graph_run(None, command)

    assert state.status is GraphRunStatus.RUNNING
    assert state.frontier == (GraphNodeId("a"), GraphNodeId("b"))
    assert state.parent == command.parent
    assert state.resolution_codec == CODEC


def test_start_creates_an_immutable_normalized_running_state() -> None:
    state = reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId("b"), GraphNodeId("a")),
        ),
    )

    assert state.frontier == (GraphNodeId("a"), GraphNodeId("b"))
    with pytest.raises(FrozenInstanceError):
        state.superstep = 1  # type: ignore[misc]


def test_start_preserves_valid_parent_linkage() -> None:
    parent = ParentGraphTask(GraphRunId("parent"), GraphTaskId("parent-task"))
    state = reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("child"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId("a"),),
            parent,
        ),
    )

    assert state.parent == parent


@pytest.mark.parametrize(
    "command",
    [
        StartGraphRun(GraphRunId(""), GraphDefinitionId("graph"), GraphDefinitionVersion(1), (GraphNodeId("a"),)),
        StartGraphRun(GraphRunId(" run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), (GraphNodeId("a"),)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId(""), GraphDefinitionVersion(1), (GraphNodeId("a"),)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId(" graph"), GraphDefinitionVersion(1), (GraphNodeId("a"),)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(0), (GraphNodeId("a"),)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(-1), (GraphNodeId("a"),)),
        StartGraphRun(GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), ()),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId("a"), GraphNodeId("a")),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId(""),),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId(" a"),),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId("a"),),
            ParentGraphTask(GraphRunId("run"), GraphTaskId("task")),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId("a"),),
            ParentGraphTask(GraphRunId(""), GraphTaskId("task")),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId("a"),),
            ParentGraphTask(GraphRunId("parent"), GraphTaskId("")),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId("a"),),
            resolution_codec=GraphResolutionCodec(GraphResolutionCodecId(""), 1),
        ),
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (GraphNodeId("a"),),
            resolution_codec=GraphResolutionCodec(GraphResolutionCodecId("codec"), 0),
        ),
    ],
)
def test_start_rejects_invalid_identity_topology_and_codec(command: StartGraphRun) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(None, command)


def test_start_rejects_existing_state_and_other_commands_require_a_state() -> None:
    command = StartGraphRun(
        GraphRunId("run"), GraphDefinitionId("graph"), GraphDefinitionVersion(1), (GraphNodeId("a"),)
    )
    with pytest.raises(GraphStateTransitionError, match="existing"):
        reduce_graph_run(running_state(), command)
    with pytest.raises(GraphStateTransitionError, match="started"):
        reduce_graph_run(None, AbortGraphRun(0, GraphFailure("abort")))


def test_claim_is_the_only_way_to_obtain_execution_ownership() -> None:
    state = claim(running_state())

    assert state.execution_sequence == 1
    assert state.execution == GraphExecutionLease(GraphExecutionToken(1, ATTEMPT), (TASK,))


@pytest.mark.parametrize(
    "command",
    [
        ClaimGraphExecution(1, ATTEMPT, (TASK,)),
        ClaimGraphExecution(0, GraphExecutionAttemptId(""), (TASK,)),
        ClaimGraphExecution(0, ATTEMPT, ()),
        ClaimGraphExecution(0, ATTEMPT, (TASK, TASK)),
        ClaimGraphExecution(0, ATTEMPT, (GraphTaskId("bad\n"),)),
    ],
)
def test_claim_rejects_stale_or_invalid_commands(command: ClaimGraphExecution) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(running_state(), command)


def test_claim_rejects_terminal_and_existing_execution() -> None:
    terminal = replace(running_state(), status=GraphRunStatus.COMPLETED, frontier=())
    with pytest.raises(GraphStateTransitionError, match="running"):
        reduce_graph_run(terminal, ClaimGraphExecution(0, ATTEMPT, (TASK,)))
    claimed = claim(running_state())
    with pytest.raises(GraphStateTransitionError, match="already"):
        reduce_graph_run(claimed, ClaimGraphExecution(claimed.revision, ATTEMPT, (GraphTaskId("other"),)))


def test_fence_clears_only_the_exact_active_execution() -> None:
    claimed = claim(running_state())
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, token(claimed)))

    assert fenced.execution is None
    assert fenced.execution_sequence == 1
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(claimed, FenceGraphExecution(0, token(claimed)))
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(
            claimed,
            FenceGraphExecution(
                claimed.revision,
                GraphExecutionToken(1, GraphExecutionAttemptId("other")),
            ),
        )
    terminal = replace(running_state(), status=GraphRunStatus.COMPLETED, frontier=())
    with pytest.raises(GraphStateTransitionError, match="running"):
        reduce_graph_run(terminal, FenceGraphExecution(0, GraphExecutionToken(1, ATTEMPT)))


def test_reclaim_after_fencing_advances_the_execution_generation() -> None:
    first = claim(running_state())
    fenced = reduce_graph_run(first, FenceGraphExecution(first.revision, token(first)))
    second = claim(fenced, attempt_id=GraphExecutionAttemptId("attempt-b"))

    assert token(first) == GraphExecutionToken(1, ATTEMPT)
    assert token(second) == GraphExecutionToken(2, GraphExecutionAttemptId("attempt-b"))
    with pytest.raises(GraphStateTransitionError, match="own"):
        reduce_graph_run(second, CompleteGraphRun(second.revision, token(first)))


def test_resource_admission_uses_revision_cas() -> None:
    state = running_state(interrupt_record=interrupt(GraphInterruptLifecycle.RESOLVED), resolution_codec=CODEC)
    admitted = reduce_graph_run(state, UpdateGraphResources(state.revision, acquired()))

    assert admitted.resources == acquired()
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(admitted, UpdateGraphResources(state.revision, released()))
    with pytest.raises(GraphStateTransitionError, match="during execution"):
        claimed = claim(state)
        reduce_graph_run(claimed, UpdateGraphResources(claimed.revision, acquired()))


def test_resource_admission_clears_only_when_the_claimed_superstep_settles() -> None:
    admitted = reduce_graph_run(
        running_state(),
        UpdateGraphResources(0, acquired()),
    )
    claimed = claim(admitted)
    advanced = reduce_graph_run(
        claimed,
        AdvanceGraphRun(claimed.revision, token(claimed), (GraphNodeId("b"),)),
    )

    assert admitted.resources == acquired()
    assert claimed.resources == acquired()
    assert advanced.resources is None


def test_resource_admission_rejects_invalid_parallel_state_and_status() -> None:
    invalid = ResourceSnapshot((ResourceLock(FILE), ResourceLock(FILE)))
    with pytest.raises(GraphStateTransitionError, match="invalid"):
        reduce_graph_run(running_state(), UpdateGraphResources(0, invalid))
    terminal = replace(running_state(), status=GraphRunStatus.COMPLETED, frontier=())
    with pytest.raises(GraphStateTransitionError, match="running"):
        reduce_graph_run(terminal, UpdateGraphResources(0, released()))


def test_resource_admission_cannot_replace_a_committed_owner() -> None:
    committed = acquired()
    replacement = acquired(GraphTaskId("task-b"))
    state = running_state(resources=committed)

    with pytest.raises(GraphStateTransitionError, match="rewrite committed acquisitions"):
        reduce_graph_run(state, UpdateGraphResources(0, replacement))


def test_resource_admission_accepts_only_a_replayable_extension() -> None:
    first_participant = ParticipantId(TASK)
    second_participant = ParticipantId("task-b")
    empty = ResourceSnapshot((ResourceLock(FILE), ResourceLock(DATABASE)))
    committed = reduce_resources(empty, AcquireResources(first_participant, (FILE,)))
    extended = reduce_resources(committed, AcquireResources(second_participant, (DATABASE,)))
    state = running_state(resources=committed)

    updated = reduce_graph_run(state, UpdateGraphResources(0, extended))

    assert updated.resources == extended


def test_resource_admission_rejects_an_unreplayable_acquisition() -> None:
    participant = ParticipantId("task-b")
    proposed = ResourceSnapshot(
        (ResourceLock(FILE),),
        (ResourceAcquisition(participant, (DATABASE,), (), DATABASE),),
    )

    with pytest.raises(GraphStateTransitionError, match="legal acquisition sequence"):
        reduce_graph_run(running_state(), UpdateGraphResources(0, proposed))


def test_resource_admission_rejects_a_valid_snapshot_with_non_fifo_history() -> None:
    owner = ParticipantId("owner")
    waiter = ParticipantId("waiter")
    proposed = ResourceSnapshot(
        (ResourceLock(FILE, owner, (waiter,)),),
        (
            ResourceAcquisition(waiter, (FILE,), (), FILE),
            ResourceAcquisition(owner, (FILE,), (FILE,)),
        ),
    )

    with pytest.raises(GraphStateTransitionError, match="replayed acquisition sequence"):
        reduce_graph_run(running_state(), UpdateGraphResources(0, proposed))
    with pytest.raises(GraphStateTransitionError, match="resources state is invalid"):
        validate_graph_run_state(running_state(resources=proposed))


def test_progress_requires_exact_execution_token() -> None:
    state = claim(running_state())
    commands = (
        AdvanceGraphRun(state.revision, GraphExecutionToken(1, GraphExecutionAttemptId("other")), (GraphNodeId("b"),)),
        CompleteGraphRun(state.revision, GraphExecutionToken(1, GraphExecutionAttemptId("other"))),
        FailGraphExecution(
            state.revision,
            GraphExecutionToken(1, GraphExecutionAttemptId("other")),
            GraphFailure("failed"),
        ),
    )
    for command in commands:
        with pytest.raises(GraphStateTransitionError, match="own"):
            reduce_graph_run(state, command)


def test_progress_transition_rejects_a_stale_revision() -> None:
    state = claim(
        running_state(
            interrupt_record=interrupt(GraphInterruptLifecycle.RESOLVED),
            resolution_codec=CODEC,
        )
    )

    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(state, AdvanceGraphRun(0, token(state), (GraphNodeId("b"),)))


def test_advance_commits_frontier_join_progress_and_consumption_receipt() -> None:
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("c")),
        GraphNodeId("d"),
        frozenset({GraphNodeId("a")}),
    )
    state = claim(running_state(interrupt_record=interrupt(GraphInterruptLifecycle.RESOLVED), resolution_codec=CODEC))
    advanced = reduce_graph_run(
        state,
        AdvanceGraphRun(state.revision, token(state), (GraphNodeId("b"),), (progress,)),
    )

    assert advanced.superstep == 1
    assert advanced.frontier == (GraphNodeId("b"),)
    assert advanced.join_progress == (progress,)
    assert advanced.execution is None
    assert advanced.interrupt == interrupt(
        GraphInterruptLifecycle.CONSUMED,
        receipt=GraphInterruptReceipt(0),
    )


def test_advance_is_pure_and_guards_expected_revision() -> None:
    claimed = claim(running_state(superstep=2))
    advanced = reduce_graph_run(
        claimed,
        AdvanceGraphRun(claimed.revision, token(claimed), (GraphNodeId("b"),)),
    )

    assert claimed.superstep == 2
    assert claimed.frontier == (GraphNodeId("a"),)
    assert claimed.execution is not None
    assert advanced.superstep == 3
    assert advanced.frontier == (GraphNodeId("b"),)
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(claimed, AdvanceGraphRun(0, token(claimed), (GraphNodeId("b"),)))


def test_advance_normalizes_join_progress_order() -> None:
    first = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    )
    second = GraphJoinProgress(
        (GraphNodeId("d"), GraphNodeId("e")),
        GraphNodeId("f"),
        frozenset({GraphNodeId("d")}),
    )
    claimed = claim(running_state())

    advanced = reduce_graph_run(
        claimed,
        AdvanceGraphRun(claimed.revision, token(claimed), (GraphNodeId("next"),), (second, first)),
    )

    assert advanced.join_progress == (first, second)


@pytest.mark.parametrize("progress", INVALID_JOIN_PROGRESS)
def test_advance_rejects_each_invalid_join_progress_shape(progress: GraphJoinProgress) -> None:
    claimed = claim(running_state())
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(
            claimed,
            AdvanceGraphRun(claimed.revision, token(claimed), (GraphNodeId("next"),), (progress,)),
        )


def test_advance_rejects_duplicate_join_progress() -> None:
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    )
    claimed = claim(running_state())
    with pytest.raises(GraphStateTransitionError, match="repeats"):
        reduce_graph_run(
            claimed,
            AdvanceGraphRun(claimed.revision, token(claimed), (GraphNodeId("next"),), (progress, progress)),
        )


@pytest.mark.parametrize(
    "frontier,progress",
    [
        ((), ()),
        ((GraphNodeId("a"), GraphNodeId("a")), ()),
        ((GraphNodeId("bad\n"),), ()),
        (
            (GraphNodeId("b"),),
            (
                GraphJoinProgress(
                    (GraphNodeId("a"), GraphNodeId("a")),
                    GraphNodeId("d"),
                    frozenset({GraphNodeId("a")}),
                ),
            ),
        ),
    ],
)
def test_advance_rejects_invalid_recoverable_position(
    frontier: tuple[GraphNodeId, ...], progress: tuple[GraphJoinProgress, ...]
) -> None:
    state = claim(running_state())
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state, AdvanceGraphRun(state.revision, token(state), frontier, progress))


def test_complete_clears_the_frontier_only_after_a_claim() -> None:
    completed_state = claim(running_state(superstep=3))
    completed = reduce_graph_run(completed_state, CompleteGraphRun(completed_state.revision, token(completed_state)))

    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.frontier == ()
    assert completed.execution is None


def test_complete_rejects_unresolved_join_progress() -> None:
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    )
    claimed = claim(replace(running_state(), join_progress=(progress,)))

    with pytest.raises(GraphStateTransitionError, match="unresolved join progress"):
        reduce_graph_run(claimed, CompleteGraphRun(claimed.revision, token(claimed)))


def test_execution_failure_clears_the_frontier_only_after_a_claim() -> None:
    failed_state = claim(running_state(superstep=4))
    failed = reduce_graph_run(
        failed_state,
        FailGraphExecution(failed_state.revision, token(failed_state), GraphFailure("node failed")),
    )

    assert failed.status is GraphRunStatus.FAILED
    assert failed.frontier == ()
    assert failed.failure == GraphFailure("node failed")
    assert failed.execution is None


def test_execution_failure_consumes_a_resolution_only_after_the_claimed_node_ran() -> None:
    resolved = running_state(
        interrupt_record=interrupt(GraphInterruptLifecycle.RESOLVED),
        resolution_codec=CODEC,
    )
    claimed = claim(resolved)

    failed = reduce_graph_run(
        claimed,
        FailGraphExecution(claimed.revision, token(claimed), GraphFailure("node failed")),
    )

    assert failed.interrupt == interrupt(
        GraphInterruptLifecycle.CONSUMED,
        receipt=GraphInterruptReceipt(0),
    )


def test_progress_rejects_stale_revision_status_and_invalid_failure() -> None:
    resolved = claim(
        running_state(interrupt_record=interrupt(GraphInterruptLifecycle.RESOLVED), resolution_codec=CODEC)
    )
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(resolved, CompleteGraphRun(0, token(resolved)))
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(resolved, FailGraphExecution(resolved.revision, token(resolved), GraphFailure("")))
    terminal = replace(running_state(), status=GraphRunStatus.COMPLETED, frontier=())
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(terminal, CompleteGraphRun(0, GraphExecutionToken(1, ATTEMPT)))
    with pytest.raises(GraphStateTransitionError, match="running"):
        reduce_graph_run(
            terminal,
            AdvanceGraphRun(0, GraphExecutionToken(1, ATTEMPT), (GraphNodeId("b"),)),
        )
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(resolved, AdvanceGraphRun(0, token(resolved), (GraphNodeId("b"),)))
    with pytest.raises(GraphStateTransitionError, match="running"):
        reduce_graph_run(
            terminal,
            FailGraphExecution(0, GraphExecutionToken(1, ATTEMPT), GraphFailure("failed")),
        )
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(resolved, FailGraphExecution(0, token(resolved), GraphFailure("failed")))


def test_abort_cancels_unconsumed_resolution_without_claim() -> None:
    state = running_state(
        interrupt_record=interrupt(GraphInterruptLifecycle.RESOLVED),
        resolution_codec=CODEC,
    )
    aborted = reduce_graph_run(state, AbortGraphRun(state.revision, GraphFailure("operator abort")))

    assert aborted.status is GraphRunStatus.FAILED
    assert aborted.interrupt == replace(
        interrupt(GraphInterruptLifecycle.RESOLVED),
        lifecycle=GraphInterruptLifecycle.CANCELLED,
        receipt=GraphInterruptReceipt(0),
    )


def test_abort_cancels_a_requested_suspended_interrupt() -> None:
    requested = interrupt(GraphInterruptLifecycle.REQUESTED)
    suspended = replace(
        running_state(interrupt_record=requested, resolution_codec=CODEC),
        status=GraphRunStatus.SUSPENDED,
    )

    aborted = reduce_graph_run(suspended, AbortGraphRun(suspended.revision, GraphFailure("operator abort")))

    assert aborted.status is GraphRunStatus.FAILED
    assert aborted.frontier == ()
    assert aborted.interrupt == replace(
        requested,
        lifecycle=GraphInterruptLifecycle.CANCELLED,
        receipt=GraphInterruptReceipt(0),
    )


def test_abort_preserves_finalized_interrupt_and_rejects_unsafe_state() -> None:
    consumed = interrupt(GraphInterruptLifecycle.CONSUMED, receipt=GraphInterruptReceipt(0))
    state = running_state(superstep=1, interrupt_record=consumed, resolution_codec=CODEC)
    assert reduce_graph_run(state, AbortGraphRun(state.revision, GraphFailure("abort"))).interrupt == consumed
    for unsafe in (
        claim(running_state()),
        running_state(resources=released()),
        replace(running_state(), status=GraphRunStatus.COMPLETED, frontier=()),
    ):
        with pytest.raises(GraphStateTransitionError):
            reduce_graph_run(unsafe, AbortGraphRun(unsafe.revision, GraphFailure("abort")))
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(running_state(), AbortGraphRun(1, GraphFailure("abort")))
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(running_state(), AbortGraphRun(0, GraphFailure("")))


@pytest.mark.parametrize(
    "state",
    [
        replace(running_state(), definition_version=GraphDefinitionVersion(0)),
        replace(running_state(), definition_version=GraphDefinitionVersion(-1)),
        replace(running_state(), superstep=-1),
        replace(running_state(), revision=-1),
        replace(running_state(), execution_sequence=-1),
        replace(running_state(), frontier=(GraphNodeId(""),)),
        replace(running_state(), parent=ParentGraphTask(GraphRunId("run"), GraphTaskId("task"))),
        replace(
            running_state(),
            execution_sequence=2,
            execution=GraphExecutionLease(GraphExecutionToken(1, ATTEMPT), (TASK,)),
        ),
        replace(
            running_state(),
            execution_sequence=1,
            execution=GraphExecutionLease(GraphExecutionToken(1, ATTEMPT), ()),
        ),
        replace(running_state(), resources=ResourceSnapshot((ResourceLock(FILE), ResourceLock(FILE)))),
        replace(running_state(), status=GraphRunStatus.COMPLETED),
        replace(
            running_state(),
            status=GraphRunStatus.COMPLETED,
            frontier=(),
            join_progress=(
                GraphJoinProgress(
                    (GraphNodeId("a"), GraphNodeId("b")),
                    GraphNodeId("c"),
                    frozenset({GraphNodeId("a")}),
                ),
            ),
        ),
        replace(running_state(resources=released()), status=GraphRunStatus.COMPLETED, frontier=()),
        replace(running_state(), failure=GraphFailure("failed")),
        running_state(
            interrupt_record=interrupt(
                GraphInterruptLifecycle.CANCELLED,
                receipt=GraphInterruptReceipt(0),
            ),
            resolution_codec=CODEC,
        ),
        replace(running_state(), status=GraphRunStatus.FAILED, frontier=(), failure=None),
        replace(running_state(), status=GraphRunStatus.FAILED, frontier=(), failure=GraphFailure("")),
        replace(running_state(), status=GraphRunStatus.FAILED, frontier=(), failure=GraphFailure("  ")),
        replace(
            running_state(),
            status=GraphRunStatus.FAILED,
            frontier=(),
            failure=GraphFailure("failed"),
            join_progress=(
                GraphJoinProgress(
                    (GraphNodeId("a"), GraphNodeId("b")),
                    GraphNodeId("c"),
                    frozenset({GraphNodeId("a")}),
                ),
            ),
        ),
    ],
)
def test_state_validation_rejects_corrupted_core_state(state: GraphRunState) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(state)


@pytest.mark.parametrize(
    "state",
    [
        replace(running_state(), run_id=GraphRunId("")),
        replace(running_state(), run_id=GraphRunId(" run")),
        replace(running_state(), definition_id=GraphDefinitionId("")),
        replace(running_state(), definition_id=GraphDefinitionId("graph ")),
        replace(running_state(), parent=ParentGraphTask(GraphRunId(""), GraphTaskId("task"))),
        replace(running_state(), parent=ParentGraphTask(GraphRunId("parent"), GraphTaskId(""))),
        replace(running_state(), parent=ParentGraphTask(GraphRunId("parent"), GraphTaskId(" task"))),
    ],
)
def test_recovered_state_rejects_invalid_identity_or_parent(state: GraphRunState) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(state)


@pytest.mark.parametrize(
    "frontier",
    [(), (GraphNodeId("a"), GraphNodeId("a")), (GraphNodeId(" bad"),)],
)
def test_recovered_running_state_rejects_an_invalid_frontier(frontier: tuple[GraphNodeId, ...]) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(running_state(), frontier=frontier))


@pytest.mark.parametrize("status", list(GraphRunStatus))
@pytest.mark.parametrize("corrupt", [with_negative_superstep, with_duplicate_frontier])
def test_recovered_structural_invariants_apply_to_every_lifecycle(
    status: GraphRunStatus,
    corrupt: Callable[[GraphRunState], GraphRunState],
) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(corrupt(recovered_state(status)))


@pytest.mark.parametrize(
    "lease",
    [
        GraphExecutionLease(GraphExecutionToken(1, ATTEMPT), ()),
        GraphExecutionLease(GraphExecutionToken(1, ATTEMPT), (TASK, TASK)),
        GraphExecutionLease(GraphExecutionToken(1, ATTEMPT), (GraphTaskId(" bad"),)),
        GraphExecutionLease(GraphExecutionToken(0, ATTEMPT), (TASK,)),
    ],
)
def test_recovered_running_state_rejects_an_invalid_execution_lease(lease: GraphExecutionLease) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(running_state(), execution_sequence=1, execution=lease))


def test_suspended_state_requires_a_recoverable_frontier() -> None:
    requested = interrupt(GraphInterruptLifecycle.REQUESTED)
    state = replace(
        running_state(interrupt_record=requested, resolution_codec=CODEC),
        status=GraphRunStatus.SUSPENDED,
        frontier=(),
    )

    with pytest.raises(GraphStateTransitionError, match="frontier"):
        validate_graph_run_state(state)


def test_valid_recovered_parent_linkage_can_transition() -> None:
    parent = ParentGraphTask(GraphRunId("parent"), GraphTaskId("task"))
    recovered = replace(running_state(), parent=parent)
    claimed = claim(recovered)
    advanced = reduce_graph_run(
        claimed,
        AdvanceGraphRun(claimed.revision, token(claimed), (GraphNodeId("b"),)),
    )

    assert advanced.parent == parent


@pytest.mark.parametrize("progress", INVALID_JOIN_PROGRESS)
def test_state_validation_rejects_invalid_join_relationships(progress: GraphJoinProgress) -> None:
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(running_state(), join_progress=(progress,)))


def test_state_validation_rejects_repeated_join_progress() -> None:
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    )
    with pytest.raises(GraphStateTransitionError, match="repeats"):
        validate_graph_run_state(replace(running_state(), join_progress=(progress, progress)))


def test_state_validation_rejects_permuted_sources_for_one_join() -> None:
    canonical = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    )
    permuted = replace(canonical, sources=tuple(reversed(canonical.sources)))

    with pytest.raises(GraphStateTransitionError, match="canonical order"):
        validate_graph_run_state(replace(running_state(), join_progress=(permuted,)))
    with pytest.raises(GraphStateTransitionError, match="canonical order"):
        validate_graph_run_state(replace(running_state(), join_progress=(canonical, permuted)))


@pytest.mark.parametrize(
    "record",
    [
        replace(
            interrupt(GraphInterruptLifecycle.REQUESTED),
            identity=replace(interrupt(GraphInterruptLifecycle.REQUESTED).identity, generation=0),
        ),
        replace(interrupt(GraphInterruptLifecycle.REQUESTED), resolution_payload=GraphInterruptPayload(b"bad")),
        replace(interrupt(GraphInterruptLifecycle.RESOLVED), resolution_payload=None),
        interrupt(GraphInterruptLifecycle.CONSUMED),
        interrupt(GraphInterruptLifecycle.CANCELLED),
        replace(
            interrupt(GraphInterruptLifecycle.RESOLVED),
            resolution_codec=GraphResolutionCodec(GraphResolutionCodecId("other"), 1),
        ),
        replace(
            interrupt(GraphInterruptLifecycle.CONSUMED, receipt=GraphInterruptReceipt(1)),
            receipt=GraphInterruptReceipt(1),
        ),
        interrupt(GraphInterruptLifecycle.CONSUMED, receipt=GraphInterruptReceipt(-1)),
    ],
)
def test_state_validation_rejects_corrupted_interrupt_lifecycle(record: GraphInterruptRecord) -> None:
    state = running_state(interrupt_record=record, resolution_codec=CODEC)
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(state)


def test_running_consumed_interrupt_requires_a_receipt_from_an_earlier_superstep() -> None:
    same_step = running_state(
        interrupt_record=interrupt(
            GraphInterruptLifecycle.CONSUMED,
            receipt=GraphInterruptReceipt(0),
        ),
        resolution_codec=CODEC,
    )

    with pytest.raises(GraphStateTransitionError, match="earlier superstep"):
        validate_graph_run_state(same_step)

    validate_graph_run_state(replace(same_step, superstep=1))


def test_suspended_and_terminal_states_require_quiescent_consistent_interrupts() -> None:
    requested = interrupt(GraphInterruptLifecycle.REQUESTED)
    suspended = replace(
        running_state(interrupt_record=requested, resolution_codec=CODEC),
        status=GraphRunStatus.SUSPENDED,
    )
    validate_graph_run_state(suspended)
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(replace(suspended, interrupt=None))
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(running_state(interrupt_record=requested, resolution_codec=CODEC))
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(
            replace(
                suspended,
                execution_sequence=1,
                execution=GraphExecutionLease(GraphExecutionToken(1, ATTEMPT), (TASK,)),
            )
        )
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        validate_graph_run_state(replace(suspended, resources=released()))
    with pytest.raises(GraphStateTransitionError):
        validate_graph_run_state(
            replace(
                running_state(
                    interrupt_record=interrupt(GraphInterruptLifecycle.RESOLVED),
                    resolution_codec=CODEC,
                ),
                status=GraphRunStatus.COMPLETED,
                frontier=(),
            )
        )
