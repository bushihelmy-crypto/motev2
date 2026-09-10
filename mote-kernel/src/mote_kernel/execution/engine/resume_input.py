"""Scoped node-input materialization and graph-local resume codecs."""

from typing import TypeVar

from mote_kernel.config import Config, ConfigContractError, require_config
from mote_kernel.execution.engine.routing import (
    binding_source_coordinate,
    frame_coordinate_available,
    graph_input_availability_coordinate,
    publication_availability_coordinate,
)
from mote_kernel.execution.errors import (
    GraphValueAdmissionError,
    GraphValueUnavailableError,
    InvalidRoutingCommandError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.ports import (
    CompiledPredecessorInput,
    GraphInputPort,
    MaterializationPlan,
    ResolvedInputBinding,
    ResolvedValueSource,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    NamedValue,
    NodeInputFrame,
    _frame_value,
    _GraphValues,
    _make_node_input_frame,
)
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation, stable_activation
from mote_kernel.execution.run_context import (
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameAvailability,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    GraphActivationCause,
    GraphActivationIdentity,
    GraphFrontierNode,
    GraphNodeId,
    GraphResumeInputPayload,
    GraphRunState,
    OverrideGraphNodeInput,
    PendingGraphNode,
    RoutedActivationCause,
    StartActivationCause,
    frontier_node,
)

GraphValueT = TypeVar("GraphValueT")


def _require_node_materialization(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
) -> MaterializationPlan[GraphValueT]:
    plan = graph.transition.materializations.get(node_id)
    if plan is None:
        raise SnapshotMismatchError("node input references an unknown compiled materialization")
    return plan


def _resume_input_coordinate(
    activation: StableActivation,
    plan: MaterializationPlan[GraphValueT],
) -> ResumeInputAvailabilityCoordinate[GraphValueT]:
    return ResumeInputAvailabilityCoordinate(
        activation,
        plan.descriptor.identity,
    )


def require_resume_input_binding(graph: CompiledGraph[GraphValueT], state: GraphRunState) -> None:
    binding = graph.resume_input
    codec = state.resume_input_codec
    if binding is None and codec is None:
        return
    if binding is None or codec is None or binding.codec_id != codec.codec_id or binding.version != codec.version:
        raise SnapshotMismatchError("compiled graph resume input codec does not match durable graph state")


def encode_resume_input(
    graph: CompiledGraph[GraphValueT],
    values: _GraphValues[GraphValueT],
) -> OverrideGraphNodeInput:
    binding = graph.resume_input
    if binding is None:
        raise SnapshotMismatchError("graph does not define a resume input codec")
    return OverrideGraphNodeInput(GraphResumeInputPayload(binding.encode(values)))


def decode_resume_input(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
    payload: bytes,
    *,
    activation_config: Config | None = None,
) -> NodeInputFrame[GraphValueT]:
    binding = graph.resume_input
    if binding is None:
        raise SnapshotMismatchError("input override is missing its compiled graph decoder")
    candidate = binding.decode(payload)
    plan = _require_node_materialization(graph, node_id)
    inherited = _select_activation_config(
        (activation_config, candidate.activation_config),
        conflict_message="resume input and activation cause carry different Config snapshots",
    )
    return _make_node_input_frame(
        tuple(NamedValue(name, value) for name, value in candidate.items()),
        plan.descriptor.declarations,
        activation_config=inherited,
    )


def _select_activation_config(
    candidates: tuple[Config | None, ...],
    *,
    conflict_message: str,
) -> Config | None:
    """Select one immutable Config from the current activation evidence."""

    selected: Config | None = None
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            require_config(candidate)
        except ConfigContractError as error:
            raise GraphValueAdmissionError("activation Config is malformed") from error
        if selected is None:
            selected = candidate
        elif selected != candidate:
            raise SnapshotMismatchError(conflict_message)
    return selected


def activation_config_for_cause(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    node: GraphFrontierNode,
    frames: ScopedFrameIndex[GraphValueT],
) -> Config | None:
    """Project the one Config carried by a pending activation's cause."""

    cause = node.cause
    if type(cause) is StartActivationCause:
        coordinates: tuple[
            GraphInputAvailabilityCoordinate[GraphValueT] | PublicationAvailabilityCoordinate[GraphValueT],
            ...,
        ] = (graph_input_availability_coordinate(graph, scope_run),)
    elif type(cause) is RoutedActivationCause:
        try:
            coordinates = tuple(
                publication_availability_coordinate(graph, scope_run, reference.activation)
                for reference in cause.references
            )
        except (AttributeError, KeyError, TypeError) as error:
            raise SnapshotMismatchError("pending activation cause references an unknown publication") from error
    else:
        raise SnapshotMismatchError("pending activation has an unsupported cause")

    candidates: list[Config | None] = []
    for coordinate in coordinates:
        try:
            candidates.append(frames.lookup(coordinate).frame.activation_config)
        except SnapshotMismatchError:
            # Config is optional execution metadata.  A recovered or manually
            # assembled frame index may omit it while the node still has all
            # business inputs required for execution.
            continue
    return _select_activation_config(
        tuple(candidates),
        conflict_message="node inputs combine different activation Config snapshots",
    )


def node_inputs_available(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    activation_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
    node_id: GraphNodeId,
    state: GraphRunState | None = None,
) -> bool:
    plan = _require_node_materialization(graph, node_id)
    has_predecessor = any(isinstance(binding.source, CompiledPredecessorInput) for binding in plan.bindings.entries)
    cause: GraphActivationCause | None = None
    node: GraphFrontierNode | None = None
    if state is not None:
        node = frontier_node(state.frontier, node_id)
        if state.run_id != scope_run.graph_run_id:
            raise SnapshotMismatchError("predecessor input availability scope does not match authoritative state")
        if has_predecessor and activation_superstep != state.superstep:
            raise SnapshotMismatchError("predecessor input availability coordinate does not match authoritative state")
        if node is None:
            if has_predecessor:
                raise SnapshotMismatchError("predecessor-bound activation is not present in the current frontier")
        else:
            cause = node.cause
    elif has_predecessor:
        raise SnapshotMismatchError("predecessor input availability requires authoritative graph state")
    for binding in plan.bindings.entries:
        try:
            _source, coordinate = binding_source_coordinate(
                graph,
                state,
                scope_run,
                activation_superstep,
                binding,
                cause=cause,
            )
        except InvalidRoutingCommandError as error:
            raise SnapshotMismatchError(str(error)) from error
        if not frame_coordinate_available(frames, coordinate):
            return False
    return True


def pending_node_input_available(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
    node_id: GraphNodeId,
) -> bool:
    node = frontier_node(state.frontier, node_id)
    if node is None or not isinstance(node.settlement, PendingGraphNode):
        raise SnapshotMismatchError("input availability requires a current pending node")
    plan = _require_node_materialization(graph, node_id)
    has_predecessor = any(isinstance(binding.source, CompiledPredecessorInput) for binding in plan.bindings.entries)
    if isinstance(node.settlement.input, OverrideGraphNodeInput):
        if has_predecessor:
            raise SnapshotMismatchError("predecessor-bound activation cannot use an input override")
        return True
    coordinate = _resume_input_coordinate(
        stable_activation(scope_run, GraphActivationIdentity(state.run_id, state.superstep, node_id)),
        plan,
    )
    if not has_predecessor and frames.has_resume_input(coordinate):
        return True
    return node_inputs_available(
        graph,
        scope_run,
        state.superstep,
        frames,
        node_id,
        state,
    )


def materialize_node_input(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameIndex[GraphValueT],
    node_id: GraphNodeId,
) -> NodeInputFrame[GraphValueT]:
    require_resume_input_binding(graph, state)
    if state.run_id != scope_run.graph_run_id:
        raise SnapshotMismatchError("node materialization scope does not match authoritative state")
    node = frontier_node(state.frontier, node_id)
    match node:
        case GraphFrontierNode(settlement=PendingGraphNode(input=effective_input)):
            pass
        case _:
            raise SnapshotMismatchError("effective input requires a current pending node")
    activation = stable_activation(scope_run, GraphActivationIdentity(state.run_id, state.superstep, node_id))
    plan = _require_node_materialization(graph, node_id)
    # A predecessor binding must always be resolved from the state-owned cause;
    # neither an override nor a cached frame may replace that selection.
    has_predecessor = any(isinstance(binding.source, CompiledPredecessorInput) for binding in plan.bindings.entries)
    if isinstance(effective_input, OverrideGraphNodeInput):
        if has_predecessor:
            raise SnapshotMismatchError("predecessor-bound activation cannot use an input override")
        inherited_config = activation_config_for_cause(graph, scope_run, node, frames)
        return decode_resume_input(
            graph,
            node_id,
            bytes(effective_input.payload),
            activation_config=inherited_config,
        )
    resume_coordinate = _resume_input_coordinate(activation, plan)
    if not has_predecessor:
        try:
            cached = frames.lookup(resume_coordinate).frame
        except SnapshotMismatchError:
            cached = None
        if cached is not None:
            inherited_config = activation_config_for_cause(graph, scope_run, node, frames)
            return _make_node_input_frame(
                cached.entries,
                plan.descriptor.declarations,
                activation_config=_select_activation_config(
                    (inherited_config, cached.activation_config),
                    conflict_message="node inputs combine different activation Config snapshots",
                ),
            )
    resolved_bindings: list[
        tuple[
            ResolvedValueSource,
            GraphInputAvailabilityCoordinate[GraphValueT] | PublicationAvailabilityCoordinate[GraphValueT],
            ResolvedInputBinding[GraphValueT],
        ]
    ] = []
    for binding in plan.bindings.entries:
        try:
            source, coordinate = binding_source_coordinate(
                graph,
                state,
                scope_run,
                state.superstep,
                binding,
                cause=node.cause,
            )
        except InvalidRoutingCommandError as error:
            raise SnapshotMismatchError(str(error)) from error
        resolved_bindings.append((source, coordinate, binding))
    entries: list[NamedValue[GraphValueT]] = []
    source_configs: list[Config | None] = []
    for source, coordinate, binding in resolved_bindings:
        if isinstance(source, GraphInputPort):
            value_name = source.name
            unavailable = f"graph input {source.name!r}"
        else:
            value_name = source.output_name
            unavailable = f"node output {source.node_id!r}.{source.output_name!r}"
        try:
            frame = frames.lookup(coordinate).frame
        except SnapshotMismatchError as error:
            raise GraphValueUnavailableError(f"{unavailable} is unavailable at {scope_run!r}") from error
        source_configs.append(frame.activation_config)
        value = _frame_value(frame, value_name)
        entries.append(NamedValue(binding.destination.local_name, value))
    inherited_config = activation_config_for_cause(graph, scope_run, node, frames)
    return _make_node_input_frame(
        tuple(entries),
        plan.descriptor.declarations,
        activation_config=_select_activation_config(
            (inherited_config, *source_configs),
            conflict_message="node inputs combine different activation Config snapshots",
        ),
    )


__all__ = ["_require_node_materialization", "_resume_input_coordinate"]
