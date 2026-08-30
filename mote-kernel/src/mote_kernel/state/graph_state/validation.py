"""Authoritative invariants for recovered graph-run state."""

from mote_kernel.state.graph_state.frontier_model import (
    FailedGraphNode,
    GraphFrontierState,
    GraphNodeInterruptIdentity,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    PendingGraphNode,
    SkippedGraphNode,
    SucceededGraphNode,
    UseStepRequestInput,
    frontier_status,
    pending_node_ids,
)
from mote_kernel.state.graph_state.identity import child_graph_run_id
from mote_kernel.state.graph_state.model import GraphJoinProgress, GraphRunState, GraphRunStatus
from mote_kernel.state.graph_state.resource_reducer import ResourceTransitionError, validate_resource_snapshot
from mote_kernel.state.graph_state.routing import ContinueGraphRouting, GraphRoutingContribution, SelectGraphRoute


class GraphStateTransitionError(ValueError):
    """A graph command or recovered state violates graph-run invariants."""


def _require_identity(value: str, field: str) -> None:
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise GraphStateTransitionError(f"{field} must be non-empty and trimmed")


def _validate_join_progress(progress: tuple[GraphJoinProgress, ...]) -> None:
    if progress != tuple(sorted(progress, key=lambda item: (item.sources, item.target))):
        raise GraphStateTransitionError("join progress must use canonical order")
    seen: set[tuple[tuple[str, ...], str]] = set()
    for join in progress:
        if not join.sources or join.sources != tuple(sorted(set(join.sources))):
            raise GraphStateTransitionError("join progress requires distinct canonical sources")
        if join.target in join.sources:
            raise GraphStateTransitionError("join target cannot be a source")
        for source in join.sources:
            _require_identity(source, "join source identity")
        _require_identity(join.target, "join target identity")
        if not join.arrived or not join.arrived < frozenset(join.sources):
            raise GraphStateTransitionError("join progress must contain partial arrivals")
        key = (join.sources, join.target)
        if key in seen:
            raise GraphStateTransitionError("graph state repeats join progress")
        seen.add(key)


def _validate_interrupt_identity(state: GraphRunState, identity: GraphNodeInterruptIdentity) -> None:
    if (
        identity.run_id != state.run_id
        or identity.superstep != state.superstep
        or identity.execution_generation < 1
        or identity.execution_generation > state.execution_sequence
    ):
        raise GraphStateTransitionError("interrupt identity does not match its current activation")


def _validate_routing(routing: GraphRoutingContribution) -> None:
    match routing:
        case SelectGraphRoute(route=route):
            _require_identity(route, "graph route identity")
        case ContinueGraphRouting():
            pass
        case _:
            raise GraphStateTransitionError("frontier node has an unsupported routing contribution")


def validate_graph_frontier(state: GraphRunState, frontier: GraphFrontierState) -> None:
    """Validate one durable or transition-local Frontier against its run coordinates."""

    node_ids = tuple(node.node_id for node in frontier.nodes)
    if node_ids != tuple(sorted(set(node_ids))):
        raise GraphStateTransitionError("frontier node identities must be distinct and canonical")
    needs_codec = False
    for node in frontier.nodes:
        _require_identity(node.node_id, "frontier node identity")
        # The wildcard also rejects malformed values reconstructed outside the
        # statically typed in-process construction path.
        match node.settlement:
            case PendingGraphNode(input=node_input):
                match node_input:
                    case OverrideGraphNodeInput(payload=payload):
                        match payload:
                            case bytes():
                                needs_codec = True
                            case _:
                                raise GraphStateTransitionError("resume input payload must be opaque bytes")
                    case UseStepRequestInput():
                        pass
                    case _:
                        raise GraphStateTransitionError("pending node has an unsupported input binding")
            case SucceededGraphNode(routing=routing):
                _validate_routing(routing)
            case FailedGraphNode(failure=failure):
                _require_identity(failure, "graph failure")
            case InterruptedGraphNode(interrupt=interrupt):
                needs_codec = True
                identity = interrupt.identity
                match interrupt.request_payload:
                    case bytes():
                        pass
                    case _:
                        raise GraphStateTransitionError("interrupt request payload must be opaque bytes")
                if identity.node_id != node.node_id:
                    raise GraphStateTransitionError("interrupt identity node does not match its frontier node")
                _validate_interrupt_identity(state, identity)
            case SkippedGraphNode(failure=failure, reason=reason, routing=routing):
                _require_identity(failure, "skipped graph failure")
                _require_identity(reason, "graph skip reason")
                _validate_routing(routing)
            case _:
                raise GraphStateTransitionError("frontier node has an unsupported settlement")
    codec = state.resume_input_codec
    if needs_codec and codec is None:
        raise GraphStateTransitionError("override or interrupt settlement requires a resume input codec")
    if codec is not None:
        _require_identity(codec.codec_id, "resume input codec identity")
        if codec.version < 1:
            raise GraphStateTransitionError("resume input codec version must be positive")


def validate_graph_run_state(state: GraphRunState) -> None:
    """Reject a recovered graph-run state that violates durable invariants."""

    _require_identity(state.run_id, "graph run identity")
    _require_identity(state.definition_id, "graph definition identity")
    if state.definition_version < 1:
        raise GraphStateTransitionError("graph definition version must be positive")
    if state.superstep < 0 or state.revision < 0 or state.execution_sequence < 0:
        raise GraphStateTransitionError("graph counters cannot be negative")
    if state.parent is not None:
        _require_identity(state.parent.run_id, "parent graph run identity")
        _require_identity(state.parent.node_id, "parent graph node identity")
        if state.parent.superstep < 0 or state.parent.run_id == state.run_id:
            raise GraphStateTransitionError("parent graph activation is invalid")
        if state.run_id != child_graph_run_id(
            state.parent.run_id,
            state.parent.superstep,
            state.parent.node_id,
        ):
            raise GraphStateTransitionError("child graph run identity does not match its parent activation")
    _validate_join_progress(state.join_progress)
    validate_graph_frontier(state, state.frontier)
    if state.resources is not None:
        try:
            validate_resource_snapshot(state.resources)
        except ResourceTransitionError as error:
            raise GraphStateTransitionError("graph resources state is invalid") from error
        if not state.resources.acquisitions:
            raise GraphStateTransitionError("authoritative graph resources cannot be empty")
        if state.execution is None:
            raise GraphStateTransitionError("graph resources require an active execution lease")
        pending = frozenset(pending_node_ids(state.frontier))
        if not pending:
            raise GraphStateTransitionError("resource admission requires current pending nodes")
        if not frozenset(acquisition.node_id for acquisition in state.resources.acquisitions) <= pending:
            raise GraphStateTransitionError("resource participant is outside current pending nodes")

    execution = state.execution
    if execution is not None:
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph may retain an execution lease")
        if execution.token.generation != state.execution_sequence or execution.token.generation < 1:
            raise GraphStateTransitionError("execution lease generation must match the graph sequence")
        _require_identity(execution.token.attempt_id, "execution attempt identity")
        if not pending_node_ids(state.frontier):
            raise GraphStateTransitionError("an active execution lease requires pending nodes")

    match state.status:
        case GraphRunStatus.RUNNING:
            if not state.frontier.nodes:
                raise GraphStateTransitionError("a running graph requires a non-empty frontier")
            frontier_status(state.frontier)
            if state.abort is not None:
                raise GraphStateTransitionError("a running graph cannot retain an abort")
            # The resource and execution checks above already require a Pending
            # node for every non-empty durable lease/snapshot, so AWAITING_RESUME
            # and SETTLED states are necessarily quiescent here.
        case GraphRunStatus.COMPLETED:
            if state.frontier.nodes or state.join_progress or state.resources is not None:
                raise GraphStateTransitionError("a completed graph must use the canonical empty position")
            if state.abort is not None:
                raise GraphStateTransitionError("a completed graph cannot retain an abort")
        case GraphRunStatus.ABORTED:
            if not state.frontier.nodes or state.abort is None or state.resources is not None:
                raise GraphStateTransitionError("an aborted graph must retain one quiescent diagnostic frontier")
            _require_identity(state.abort.reason, "graph abort reason")
        case _:
            raise GraphStateTransitionError("graph run has an unsupported lifecycle status")


def validated_graph_run_state(state: GraphRunState) -> GraphRunState:
    validate_graph_run_state(state)
    return state


__all__ = ["GraphStateTransitionError", "validate_graph_run_state"]
