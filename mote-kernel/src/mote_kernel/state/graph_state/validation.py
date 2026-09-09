"""Authoritative invariants for recovered graph-run state."""

from mote_kernel.state.graph_state.frontier_model import (
    FailedGraphNode,
    GraphActivationCause,
    GraphFrontierState,
    GraphFrontierStatus,
    GraphNodeInterruptIdentity,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    PendingGraphNode,
    RoutedActivationCause,
    StartActivationCause,
    SucceededGraphNode,
    UseStepRequestInput,
    frontier_node,
    frontier_status,
    pending_node_ids,
)
from mote_kernel.state.graph_state.identity import (
    ActivationReference,
    GraphActivationIdentity,
    GraphJoinIdentity,
    GraphJoinOccurrenceIdentity,
    GraphNodeId,
    child_graph_run_id,
    is_canonical_identity,
)
from mote_kernel.state.graph_state.model import GraphJoinProgress, GraphRunState, GraphRunStatus
from mote_kernel.state.graph_state.resource_reducer import ResourceTransitionError, validate_resource_snapshot
from mote_kernel.state.graph_state.routing import ContinueGraphRouting, GraphRoutingContribution, SelectGraphRoute


class GraphStateTransitionError(ValueError):
    """A graph command or recovered state violates graph-run invariants."""


def _require_identity(value: str, field: str) -> None:
    if not is_canonical_identity(value):
        raise GraphStateTransitionError(f"{field} must be non-empty and trimmed")


def _canonical_references(
    references: tuple[ActivationReference, ...],
    field: str,
    *,
    unhashable_message: str | None = None,
) -> tuple[ActivationReference, ...]:
    """Admit one canonical reference collection at the state boundary."""

    for reference in references:
        if type(reference) is not ActivationReference:
            raise GraphStateTransitionError(f"{field} contains an invalid reference")
        try:
            activation = reference.activation
        except (AttributeError, TypeError) as error:
            raise GraphStateTransitionError(f"{field} contains an invalid reference") from error
        if type(activation) is not GraphActivationIdentity:
            raise GraphStateTransitionError(f"{field} contains an invalid activation identity")
    try:
        canonical = tuple(sorted(set(references), key=ActivationReference.canonical_key))
    except AttributeError as error:
        raise GraphStateTransitionError(f"{field} contains an invalid activation identity") from error
    except TypeError as error:
        raise GraphStateTransitionError(unhashable_message or f"{field} contains an unhashable value") from error
    if references != canonical:
        raise GraphStateTransitionError(f"{field} must be canonical and distinct")
    return canonical


def _reference_activation(reference: ActivationReference, field: str) -> GraphActivationIdentity:
    """Validate identity fields after collection admission has fixed the shape."""

    try:
        activation = reference.activation
        route = reference.route
    except (AttributeError, TypeError) as error:
        raise GraphStateTransitionError(f"{field} contains an invalid reference") from error
    _require_identity(activation.run_id, f"{field} run identity")
    if type(activation.superstep) is not int or activation.superstep < 0:
        raise GraphStateTransitionError(f"{field} superstep must be a non-negative integer")
    _require_identity(activation.node_id, f"{field} node identity")
    if route is not None:
        _require_identity(route, f"{field} route identity")
    return activation


def _validate_settled_activations(state: GraphRunState) -> None:
    """Validate the committed success ledger used by historical causes.

    A coordinate in a cause is not proof that its producer actually ran.  The
    reducer records one route-bearing reference when a node success is
    committed; later causes and Join arrivals must point at that exact entry.
    The ledger is intentionally part of ``GraphRunState`` so the reducer can
    enforce that local invariant.  Authenticity of historical entries is a
    persistence/evidence concern and is checked at the compiled-graph
    admission boundary when that evidence is available.
    """

    evidence = state.settled_activations
    if type(evidence) is not tuple:
        raise GraphStateTransitionError("settled activation evidence must be a tuple")
    _canonical_references(evidence, "settled activation evidence")
    activation_ids = tuple(reference.activation for reference in evidence)
    if len(activation_ids) != len(set(activation_ids)):
        raise GraphStateTransitionError("settled activation evidence repeats one activation")
    for reference in evidence:
        activation = _reference_activation(reference, "settled activation evidence")
        if activation.run_id != state.run_id or activation.superstep > state.superstep:
            raise GraphStateTransitionError("settled activation evidence has an invalid coordinate")
        if activation.superstep == state.superstep:
            current = frontier_node(state.frontier, activation.node_id)
            if current is None or not isinstance(current.settlement, SucceededGraphNode):
                raise GraphStateTransitionError("current settled activation evidence has no successful frontier node")
            selected = (
                current.settlement.routing.route if isinstance(current.settlement.routing, SelectGraphRoute) else None
            )
            if reference.route != selected:
                raise GraphStateTransitionError("settled activation evidence route does not match its settlement")


def _validate_join_occurrence(
    state: GraphRunState,
    occurrence: GraphJoinOccurrenceIdentity,
) -> None:
    if type(occurrence) is not GraphJoinOccurrenceIdentity:
        raise GraphStateTransitionError("join occurrence identity is malformed")
    join = occurrence.join
    if type(join) is not GraphJoinIdentity:
        raise GraphStateTransitionError("join definition identity is malformed")
    sources = join.sources
    if (
        type(sources) is not tuple
        or len(sources) < 2
        or any(not is_canonical_identity(source) for source in sources)
        or sources != tuple(sorted(set(sources)))
    ):
        raise GraphStateTransitionError("join definition sources must be distinct and canonical")
    if not is_canonical_identity(join.target) or join.target in sources:
        raise GraphStateTransitionError("join definition target is invalid")
    if occurrence.run_id != state.run_id or not is_canonical_identity(occurrence.run_id):
        raise GraphStateTransitionError("join occurrence belongs to the wrong graph run")
    if type(occurrence.target_superstep) is not int or occurrence.target_superstep < 1:
        raise GraphStateTransitionError("join occurrence target superstep must be positive")


def _validate_join_progress(state: GraphRunState) -> None:
    progress = state.join_progress
    if type(progress) is not tuple or any(type(item) is not GraphJoinProgress for item in progress):
        raise GraphStateTransitionError("join progress must contain typed records")
    for item in progress:
        _validate_join_occurrence(state, item.occurrence)
    if progress != tuple(sorted(progress, key=lambda item: item.occurrence)):
        raise GraphStateTransitionError("join progress must use canonical order")
    seen: set[GraphJoinOccurrenceIdentity] = set()
    for join in progress:
        occurrence = join.occurrence
        sources = occurrence.join.sources
        if occurrence.target_superstep <= state.superstep:
            raise GraphStateTransitionError("pending join occurrence must target a future superstep")
        arrived = join.arrived
        if type(arrived) is not tuple or not arrived:
            raise GraphStateTransitionError("join progress arrivals must be canonical and distinct")
        _canonical_references(
            arrived,
            "join progress arrivals",
            unhashable_message="join progress arrivals contain an unhashable value",
        )
        arrived_sources = tuple(reference.activation.node_id for reference in arrived)
        if len(arrived_sources) != len(set(arrived_sources)) or not set(arrived_sources) < set(sources):
            raise GraphStateTransitionError("join progress must contain partial arrivals")
        for reference in arrived:
            activation = _reference_activation(reference, "join progress arrivals")
            if (
                activation.run_id != occurrence.run_id
                or activation.superstep >= state.superstep
                or activation.superstep >= occurrence.target_superstep
                or activation.node_id not in sources
            ):
                raise GraphStateTransitionError("join progress contains an invalid predecessor arrival")
            if reference not in state.settled_activations:
                raise GraphStateTransitionError("join progress arrival lacks committed settlement evidence")
        if occurrence in seen:
            raise GraphStateTransitionError("graph state repeats join progress")
        seen.add(occurrence)


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


def _validate_activation_cause(
    state: GraphRunState,
    node_id: GraphNodeId,
    cause: GraphActivationCause,
) -> None:
    if type(cause) is StartActivationCause:
        if state.superstep != 0:
            raise GraphStateTransitionError("START activation cause is valid only at superstep zero")
        return
    if type(cause) is not RoutedActivationCause:
        raise GraphStateTransitionError("frontier node has an unsupported activation cause")
    if state.superstep == 0:
        raise GraphStateTransitionError("initial frontier nodes must carry the START cause")
    references = cause.references
    if type(references) is not tuple or not references:
        raise GraphStateTransitionError("routed activation cause requires non-empty references")
    _canonical_references(references, "routed activation cause references")
    for reference in references:
        activation = _reference_activation(reference, "routed activation cause")
        if activation.run_id != state.run_id or activation.superstep >= state.superstep:
            raise GraphStateTransitionError("routed activation cause references a non-predecessor activation")
        if reference not in state.settled_activations:
            raise GraphStateTransitionError("routed activation cause lacks committed settlement evidence")
    occurrence = cause.join_occurrence
    if occurrence is None:
        if len(references) != 1:
            raise GraphStateTransitionError("non-Join routed cause requires exactly one reference")
        return
    _validate_join_occurrence(state, occurrence)
    if occurrence.join.target != node_id or occurrence.target_superstep != state.superstep:
        raise GraphStateTransitionError("routed Join cause does not match its target activation")
    source_ids = tuple(reference.activation.node_id for reference in references)
    if len(source_ids) != len(set(source_ids)) or set(source_ids) != set(occurrence.join.sources):
        raise GraphStateTransitionError("routed Join cause must exactly cover its occurrence sources")


def validate_graph_frontier(state: GraphRunState, frontier: GraphFrontierState) -> None:
    """Validate one durable or transition-local Frontier against its run coordinates."""

    node_ids = tuple(node.node_id for node in frontier.nodes)
    if node_ids != tuple(sorted(set(node_ids))):
        raise GraphStateTransitionError("frontier node identities must be distinct and canonical")
    needs_codec = False
    for node in frontier.nodes:
        _require_identity(node.node_id, "frontier node identity")
        _validate_activation_cause(state, node.node_id, node.cause)
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
    try:
        _ = state.config_cursor
    except (AttributeError, TypeError, ValueError) as error:
        raise GraphStateTransitionError("graph Config cursor is malformed") from error
    _validate_settled_activations(state)
    if state.completion_route is not None:
        _require_identity(state.completion_route, "graph completion route identity")
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
    _validate_join_progress(state)
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
            if frontier_status(state.frontier) is GraphFrontierStatus.FAILED:
                raise GraphStateTransitionError("a quiescent failed frontier requires terminal failed status")
            if state.abort is not None:
                raise GraphStateTransitionError("a running graph cannot retain an abort")
            if state.completion_route is not None:
                raise GraphStateTransitionError("a running graph cannot retain a completion route")
            # The resource and execution checks above already require a Pending
            # node for every non-empty durable lease/snapshot, so AWAITING_RESUME
            # and SETTLED states are necessarily quiescent here.
        case GraphRunStatus.COMPLETED:
            if state.frontier.nodes or state.join_progress or state.resources is not None:
                raise GraphStateTransitionError("a completed graph must use the canonical empty position")
            if state.abort is not None:
                raise GraphStateTransitionError("a completed graph cannot retain an abort")
        case GraphRunStatus.FAILED:
            if (
                not state.frontier.nodes
                or frontier_status(state.frontier) is not GraphFrontierStatus.FAILED
                or state.resources is not None
                or state.execution is not None
                or state.abort is not None
                or state.completion_route is not None
            ):
                raise GraphStateTransitionError("a failed graph must retain one quiescent failed diagnostic frontier")
        case GraphRunStatus.ABORTED:
            if (
                not state.frontier.nodes
                or state.abort is None
                or state.resources is not None
                or state.completion_route is not None
            ):
                raise GraphStateTransitionError("an aborted graph must retain one quiescent diagnostic frontier")
            _require_identity(state.abort.reason, "graph abort reason")
        case _:
            raise GraphStateTransitionError("graph run has an unsupported lifecycle status")


def validated_graph_run_state(state: GraphRunState) -> GraphRunState:
    validate_graph_run_state(state)
    return state


__all__ = ["GraphStateTransitionError", "validate_graph_run_state"]
