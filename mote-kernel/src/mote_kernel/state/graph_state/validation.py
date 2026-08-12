"""Authoritative invariants for recovered graph-run state."""

from mote_kernel.state.graph_state.model import (
    GraphInterruptLifecycle,
    GraphInterruptRecord,
    GraphJoinProgress,
    GraphResolutionCodec,
    GraphRunState,
    GraphRunStatus,
)
from mote_kernel.state.graph_state.resource_reducer import ResourceTransitionError, validate_resource_snapshot


class GraphStateTransitionError(ValueError):
    """A graph command or recovered state violates graph-run invariants."""


def _require_identity(value: str, field: str) -> None:
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise GraphStateTransitionError(f"{field} must be non-empty and trimmed")


def _validate_frontier(frontier: tuple[str, ...], *, required: bool) -> None:
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
    """Reject a recovered graph-run state that violates durable invariants."""

    _require_identity(state.run_id, "graph run identity")
    _require_identity(state.definition_id, "graph definition identity")
    if state.definition_version < 1:
        raise GraphStateTransitionError("graph definition version must be positive")
    if state.superstep < 0:
        raise GraphStateTransitionError("graph superstep cannot be negative")
    if state.revision < 0:
        raise GraphStateTransitionError("graph revision cannot be negative")
    if state.execution_sequence < 0:
        raise GraphStateTransitionError("graph execution sequence cannot be negative")
    _validate_resolution_codec(state.resolution_codec)
    if state.parent is not None:
        _require_identity(state.parent.run_id, "parent graph run identity")
        _require_identity(state.parent.task_id, "parent graph task identity")
        if state.parent.run_id == state.run_id:
            raise GraphStateTransitionError("a graph run cannot be its own parent")
    _validate_frontier(
        state.frontier,
        required=state.status in {GraphRunStatus.RUNNING, GraphRunStatus.SUSPENDED},
    )
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
    if state.resources is not None:
        try:
            validate_resource_snapshot(state.resources)
        except ResourceTransitionError as error:
            raise GraphStateTransitionError("graph resources state is invalid") from error
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED} and state.frontier:
        raise GraphStateTransitionError("a terminal graph cannot retain a frontier")
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED} and state.join_progress:
        raise GraphStateTransitionError("a terminal graph cannot retain join progress")
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED} and state.resources is not None:
        raise GraphStateTransitionError("a terminal graph cannot retain resources state")
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
        if state.resources is not None or execution is not None:
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


def validated_graph_run_state(state: GraphRunState) -> GraphRunState:
    """Validate and return one transition result."""

    validate_graph_run_state(state)
    return state


__all__ = [
    "GraphStateTransitionError",
    "validate_graph_interrupt_record",
    "validate_graph_run_state",
]
