"""Pure graph-run state transitions."""

from dataclasses import replace

from mote_kernel.parallel import (
    AcquireResources,
    ParallelSnapshot,
    ParallelTransitionError,
    ResourceLock,
    reduce_parallel,
    validate_parallel_snapshot,
)
from mote_kernel.state.graph_state.command import (
    AdvanceGraphRun,
    ClaimGraphExecution,
    CompleteGraphRun,
    FailGraphExecution,
    FenceGraphExecution,
    GraphRunCommand,
    RequestGraphRunInterrupt,
    ResolveGraphRunInterrupt,
    StartGraphRun,
    UpdateGraphParallel,
)
from mote_kernel.state.graph_state.model import (
    GraphExecutionLease,
    GraphExecutionToken,
    GraphInterruptLifecycle,
    GraphInterruptReceipt,
    GraphInterruptRecord,
    GraphJoinProgress,
    GraphResolutionCodec,
    GraphRunState,
    GraphRunStatus,
)


class GraphStateTransitionError(ValueError):
    """A graph command is invalid for the current committed state."""


def _require_identity(value: str, field: str) -> None:
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise GraphStateTransitionError(f"{field} must be non-empty and trimmed")


def _validate_frontier(frontier: tuple[str, ...], *, required: bool = True) -> None:
    if required and not frontier:
        raise GraphStateTransitionError("a running graph requires a non-empty frontier")
    if len(frontier) != len(set(frontier)):
        raise GraphStateTransitionError("a graph frontier cannot contain duplicate nodes")
    for node_id in frontier:
        _require_identity(node_id, "frontier node identity")


def _validate_join_progress(progress: tuple[GraphJoinProgress, ...]) -> None:
    seen: set[tuple[tuple[str, ...], str]] = set()
    for join in progress:
        if not join.sources or len(join.sources) != len(set(join.sources)):
            raise GraphStateTransitionError("join progress requires distinct sources")
        if join.sources != tuple(sorted(join.sources)):
            raise GraphStateTransitionError("join progress sources must use canonical order")
        for source in join.sources:
            _require_identity(source, "join source identity")
        _require_identity(join.target, "join target identity")
        if join.target in join.sources:
            raise GraphStateTransitionError("join target cannot be a source")
        if not join.arrived or not join.arrived < frozenset(join.sources):
            raise GraphStateTransitionError("join progress must contain partial arrivals")
        key = (join.sources, join.target)
        if key in seen:
            raise GraphStateTransitionError("graph state repeats join progress")
        seen.add(key)


def _join_sort_key(progress: GraphJoinProgress) -> tuple[tuple[str, ...], str]:
    return (progress.sources, progress.target)


def _validate_admission_transition(
    previous: ParallelSnapshot | None,
    proposed: ParallelSnapshot,
) -> None:
    if previous is None:
        replayed = ParallelSnapshot(tuple(ResourceLock(resource.resource_id) for resource in proposed.resources))
        prior_acquisitions = 0
    else:
        replayed = previous
        prior_acquisitions = len(previous.acquisitions)
        if proposed.acquisitions[:prior_acquisitions] != previous.acquisitions:
            raise GraphStateTransitionError("resource admission cannot rewrite committed acquisitions")
    try:
        for acquisition in proposed.acquisitions[prior_acquisitions:]:
            replayed = reduce_parallel(
                replayed,
                AcquireResources(acquisition.participant_id, acquisition.required),
            )
    except ParallelTransitionError as error:
        raise GraphStateTransitionError("resource admission is not a legal acquisition sequence") from error
    if replayed != proposed:
        raise GraphStateTransitionError("resource admission does not match its replayed acquisition sequence")


def _validate_resolution_codec(codec: GraphResolutionCodec | None) -> None:
    if codec is None:
        return
    _require_identity(codec.codec_id, "resolution codec identity")
    if codec.version < 1:
        raise GraphStateTransitionError("resolution codec version must be positive")


def validate_graph_interrupt_record(
    record: GraphInterruptRecord,
    resolution_codec: GraphResolutionCodec | None,
    maximum_receipt_superstep: int,
) -> None:
    """Validate the single durable interrupt-record representation."""

    identity = record.identity
    _require_identity(identity.root_run_id, "interrupt root graph run identity")
    _require_identity(identity.interrupt_id, "interrupt identity")
    _validate_resolution_codec(record.resolution_codec)
    if identity.generation < 1:
        raise GraphStateTransitionError("interrupt generation must be positive")
    if record.resolution_codec != resolution_codec:
        raise GraphStateTransitionError("interrupt codec must match its graph definition")
    if record.lifecycle is GraphInterruptLifecycle.REQUESTED:
        if record.resolution_payload is not None or record.receipt is not None:
            raise GraphStateTransitionError("requested interrupt cannot retain resolution state")
    elif record.lifecycle is GraphInterruptLifecycle.RESOLVED:
        if record.resolution_payload is None or record.receipt is not None:
            raise GraphStateTransitionError("resolved interrupt requires an unconsumed payload")
    elif record.lifecycle is GraphInterruptLifecycle.CONSUMED:
        if record.resolution_payload is None or record.receipt is None:
            raise GraphStateTransitionError("consumed interrupt requires its payload and receipt")
    elif record.lifecycle is GraphInterruptLifecycle.CANCELLED and record.receipt is None:
        raise GraphStateTransitionError("cancelled interrupt requires a terminal receipt")
    if record.receipt is not None and (
        record.receipt.superstep < 0 or record.receipt.superstep > maximum_receipt_superstep
    ):
        raise GraphStateTransitionError("interrupt receipt references an invalid superstep")


def validate_graph_run_state(state: GraphRunState) -> None:
    _require_identity(state.run_id, "graph run identity")
    _require_identity(state.definition_id, "graph definition identity")
    if state.definition_version < 1:
        raise GraphStateTransitionError("graph definition version must be positive")
    if state.superstep < 0:
        raise GraphStateTransitionError("graph superstep cannot be negative")
    if state.execution_sequence < 0:
        raise GraphStateTransitionError("graph execution sequence cannot be negative")
    _validate_resolution_codec(state.resolution_codec)
    if state.parent is not None:
        _require_identity(state.parent.run_id, "parent graph run identity")
        _require_identity(state.parent.task_id, "parent graph task identity")
        if state.parent.run_id == state.run_id:
            raise GraphStateTransitionError("a graph run cannot be its own parent")
    _validate_frontier(state.frontier, required=state.status in {GraphRunStatus.RUNNING, GraphRunStatus.SUSPENDED})
    _validate_join_progress(state.join_progress)
    execution = state.execution
    if execution is not None:
        if execution.token.generation < 1 or execution.token.generation != state.execution_sequence:
            raise GraphStateTransitionError("execution lease generation must match the graph sequence")
        _require_identity(execution.token.attempt_id, "execution attempt identity")
        if not execution.task_ids or len(execution.task_ids) != len(frozenset(execution.task_ids)):
            raise GraphStateTransitionError("execution lease requires distinct task identities")
        for task_id in execution.task_ids:
            _require_identity(task_id, "execution lease task identity")
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph may retain an execution lease")
    if state.parallel is not None:
        try:
            validate_parallel_snapshot(state.parallel)
        except ParallelTransitionError as error:
            raise GraphStateTransitionError("graph parallel state is invalid") from error
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED} and state.frontier:
        raise GraphStateTransitionError("a terminal graph cannot retain a frontier")
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED} and state.join_progress:
        raise GraphStateTransitionError("a terminal graph cannot retain join progress")
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED} and state.parallel is not None:
        raise GraphStateTransitionError("a terminal graph cannot retain parallel state")
    interrupt = state.interrupt
    if interrupt is not None:
        validate_graph_interrupt_record(interrupt, state.resolution_codec, state.superstep)
        if (
            state.status is GraphRunStatus.RUNNING
            and interrupt.lifecycle is GraphInterruptLifecycle.CONSUMED
            and interrupt.receipt is not None
            and interrupt.receipt.superstep >= state.superstep
        ):
            raise GraphStateTransitionError("running graph requires a consumed interrupt from an earlier superstep")
    if state.status is GraphRunStatus.SUSPENDED:
        if interrupt is None or interrupt.lifecycle is not GraphInterruptLifecycle.REQUESTED:
            raise GraphStateTransitionError("suspended graph requires a requested interrupt")
        if state.parallel is not None or execution is not None:
            raise GraphStateTransitionError("suspended graph must be scheduler-quiescent")
    elif interrupt is not None and interrupt.lifecycle is GraphInterruptLifecycle.REQUESTED:
        raise GraphStateTransitionError("only a suspended graph may retain a requested interrupt")
    if (
        state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED}
        and interrupt is not None
        and interrupt.lifecycle not in {GraphInterruptLifecycle.CONSUMED, GraphInterruptLifecycle.CANCELLED}
    ):
        raise GraphStateTransitionError("terminal graph can only retain a finalized interrupt")
    if (
        state.status not in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED}
        and interrupt is not None
        and interrupt.lifecycle is GraphInterruptLifecycle.CANCELLED
    ):
        raise GraphStateTransitionError("cancelled interrupt requires a terminal graph")
    if state.status is GraphRunStatus.FAILED:
        if state.failure is None:
            raise GraphStateTransitionError("a failed graph requires a failure")
        _require_identity(state.failure, "graph failure")
    elif state.failure is not None:
        raise GraphStateTransitionError("only a failed graph may retain a failure")


def _validated(state: GraphRunState) -> GraphRunState:
    validate_graph_run_state(state)
    return state


def _start(command: StartGraphRun) -> GraphRunState:
    return _validated(
        GraphRunState(
            run_id=command.run_id,
            definition_id=command.definition_id,
            definition_version=command.definition_version,
            status=GraphRunStatus.RUNNING,
            superstep=0,
            frontier=tuple(sorted(command.frontier)),
            parent=command.parent,
            resolution_codec=command.resolution_codec,
        )
    )


def _interrupt_generation(state: GraphRunState) -> int | None:
    return state.interrupt.identity.generation if state.interrupt is not None else None


def _require_interrupt_snapshot(state: GraphRunState, expected_generation: int | None) -> None:
    if expected_generation != _interrupt_generation(state):
        raise GraphStateTransitionError("graph command was based on a stale interrupt generation")


def _require_execution(state: GraphRunState, token: GraphExecutionToken) -> GraphExecutionLease:
    execution = state.execution
    if execution is None or execution.token != token:
        raise GraphStateTransitionError("graph command does not own the active execution lease")
    return execution


def _consume_resolution(state: GraphRunState) -> GraphInterruptRecord | None:
    interrupt = state.interrupt
    if interrupt is None or interrupt.lifecycle is GraphInterruptLifecycle.CONSUMED:
        return interrupt
    return replace(
        interrupt,
        lifecycle=GraphInterruptLifecycle.CONSUMED,
        receipt=GraphInterruptReceipt(state.superstep),
    )


def _cancel_interrupt(state: GraphRunState) -> GraphInterruptRecord | None:
    interrupt = state.interrupt
    if interrupt is None or interrupt.lifecycle in {
        GraphInterruptLifecycle.CONSUMED,
        GraphInterruptLifecycle.CANCELLED,
    }:
        return interrupt
    return replace(
        interrupt,
        lifecycle=GraphInterruptLifecycle.CANCELLED,
        receipt=GraphInterruptReceipt(state.superstep),
    )


def reduce_graph_run(state: GraphRunState | None, command: GraphRunCommand) -> GraphRunState:
    """Return a new graph-run state without mutating the prior state."""

    if isinstance(command, StartGraphRun):
        if state is not None:
            raise GraphStateTransitionError("an existing graph run cannot be started again")
        return _start(command)
    if state is None:
        raise GraphStateTransitionError("a graph run must be started before it can transition")
    validate_graph_run_state(state)
    if isinstance(command, ClaimGraphExecution):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can claim execution")
        if state.execution is not None:
            raise GraphStateTransitionError("graph already has an active execution lease")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("execution claim was based on a stale superstep")
        if command.expected_execution_sequence != state.execution_sequence:
            raise GraphStateTransitionError("execution claim was based on a stale execution sequence")
        if command.expected_parallel != state.parallel:
            raise GraphStateTransitionError("execution claim was based on a stale parallel snapshot")
        _require_interrupt_snapshot(state, command.expected_interrupt_generation)
        token = GraphExecutionToken(state.execution_sequence + 1, command.attempt_id)
        return _validated(
            replace(
                state,
                execution_sequence=token.generation,
                execution=GraphExecutionLease(token, tuple(sorted(command.task_ids))),
            )
        )
    if isinstance(command, FenceGraphExecution):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can fence execution")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("execution fence was based on a stale superstep")
        _require_execution(state, command.execution)
        return _validated(replace(state, execution=None))
    if isinstance(command, RequestGraphRunInterrupt):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can request an interrupt")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("interrupt request was based on a stale superstep")
        if state.execution is not None:
            raise GraphStateTransitionError("graph execution must drain before interruption")
        codec = state.resolution_codec
        if codec is None:
            raise GraphStateTransitionError("an interrupted graph requires a durable resolution codec")
        identity = command.identity
        prior_generation = state.interrupt.identity.generation if state.interrupt is not None else 0
        if identity.generation <= prior_generation:
            raise GraphStateTransitionError("interrupt generation must advance monotonically")
        if state.interrupt is not None and state.interrupt.lifecycle not in {
            GraphInterruptLifecycle.CONSUMED,
            GraphInterruptLifecycle.CANCELLED,
        }:
            raise GraphStateTransitionError("graph run has an unfinished interrupt generation")
        return _validated(
            replace(
                state,
                status=GraphRunStatus.SUSPENDED,
                parallel=None,
                interrupt=GraphInterruptRecord(
                    identity,
                    command.request_payload,
                    codec,
                    GraphInterruptLifecycle.REQUESTED,
                ),
            ),
        )
    if isinstance(command, ResolveGraphRunInterrupt):
        interrupt = state.interrupt
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("interrupt resolution was based on a stale superstep")
        if (
            state.status is not GraphRunStatus.SUSPENDED
            or interrupt is None
            or interrupt.identity != command.identity
            or interrupt.lifecycle is not GraphInterruptLifecycle.REQUESTED
        ):
            raise GraphStateTransitionError("interrupt resolution does not match the suspended generation")
        return _validated(
            replace(
                state,
                status=GraphRunStatus.RUNNING,
                interrupt=replace(
                    interrupt,
                    lifecycle=GraphInterruptLifecycle.RESOLVED,
                    resolution_payload=command.resolution_payload,
                ),
            ),
        )
    if isinstance(command, UpdateGraphParallel):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can update resource admission")
        if state.execution is not None:
            raise GraphStateTransitionError("resource admission cannot change during execution")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("resource admission was based on a stale superstep")
        if command.expected_parallel != state.parallel:
            raise GraphStateTransitionError("resource admission was based on a stale snapshot")
        _require_interrupt_snapshot(state, command.expected_interrupt_generation)
        _validate_admission_transition(state.parallel, command.parallel)
        return _validated(replace(state, parallel=command.parallel))
    if isinstance(command, AdvanceGraphRun):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can advance")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("advance command was based on a stale superstep")
        _require_execution(state, command.execution)
        _require_interrupt_snapshot(state, command.expected_interrupt_generation)
        return _validated(
            replace(
                state,
                superstep=state.superstep + 1,
                frontier=tuple(sorted(command.frontier)),
                join_progress=tuple(sorted(command.join_progress, key=_join_sort_key)),
                parallel=None,
                execution=None,
                interrupt=_consume_resolution(state),
            )
        )
    if isinstance(command, CompleteGraphRun):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can complete")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("complete command was based on a stale superstep")
        _require_execution(state, command.execution)
        _require_interrupt_snapshot(state, command.expected_interrupt_generation)
        if state.join_progress:
            raise GraphStateTransitionError("a graph cannot complete with unresolved join progress")
        return _validated(
            replace(
                state,
                status=GraphRunStatus.COMPLETED,
                frontier=(),
                join_progress=(),
                parallel=None,
                execution=None,
                interrupt=_consume_resolution(state),
            )
        )
    if isinstance(command, FailGraphExecution):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph execution can fail")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("execution failure was based on a stale superstep")
        _require_execution(state, command.execution)
        _require_interrupt_snapshot(state, command.expected_interrupt_generation)
        return _validated(
            replace(
                state,
                status=GraphRunStatus.FAILED,
                frontier=(),
                failure=command.failure,
                join_progress=(),
                parallel=None,
                execution=None,
                interrupt=_consume_resolution(state),
            )
        )
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED}:
        raise GraphStateTransitionError("a terminal graph cannot abort again")
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("abort command was based on a stale superstep")
    _require_interrupt_snapshot(state, command.expected_interrupt_generation)
    if state.execution is not None or state.parallel is not None:
        raise GraphStateTransitionError("graph execution and resources must be fenced before abort")
    return _validated(
        replace(
            state,
            status=GraphRunStatus.FAILED,
            frontier=(),
            failure=command.failure,
            join_progress=(),
            interrupt=_cancel_interrupt(state),
        )
    )


__all__ = [
    "GraphStateTransitionError",
    "reduce_graph_run",
    "validate_graph_interrupt_record",
    "validate_graph_run_state",
]
