"""Scoped node-input materialization and graph-local resume codecs."""

from typing import TypeVar, cast

from mote_kernel.config import Config, require_config
from mote_kernel.execution.engine.routing import (
    _graph_input_coordinate,
    _node_output_coordinate,
    causal_input_source_for_cause,
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
    require_publication_selection,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    GraphInputFrame,
    NamedValue,
    NodeInputFrame,
    NodeOutputFrame,
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
    GraphActivationIdentity,
    GraphFrontierNode,
    GraphNodeId,
    GraphResumeInputPayload,
    GraphRunState,
    OverrideGraphNodeInput,
    PendingGraphNode,
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
    try:
        payload = binding.encoder(values)
    except Exception as error:
        raise GraphValueAdmissionError("resume input encoder rejected the value frame") from error
    if type(payload) is not bytes:
        raise GraphValueAdmissionError("resume input encoder must return bytes")
    return OverrideGraphNodeInput(GraphResumeInputPayload(payload))


def decode_resume_input(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
    payload: bytes,
) -> NodeInputFrame[GraphValueT]:
    binding = graph.resume_input
    if binding is None:
        raise SnapshotMismatchError("input override is missing its compiled graph decoder")
    try:
        candidate = cast(_GraphValues[GraphValueT] | bytes, binding.decoder(payload))
    except Exception as error:
        raise GraphValueAdmissionError("resume input decoder rejected its opaque payload") from error
    if not isinstance(candidate, _GraphValues):
        raise GraphValueAdmissionError("resume input decoder must return Graph.Values")
    plan = _require_node_materialization(graph, node_id)
    return _make_node_input_frame(
        tuple(NamedValue(name, value) for name, value in candidate.items()),
        plan.descriptor.declarations,
        activation_config=candidate.activation_config,
    )


def _activation_config_from_frames(
    frames: tuple[GraphInputFrame[GraphValueT] | NodeOutputFrame[GraphValueT], ...],
) -> Config | None:
    """Require one activation Config across all source frames."""

    selected: Config | None = None
    for frame in frames:
        candidate = frame.activation_config
        if candidate is None:
            continue
        require_config(candidate)
        if selected is None:
            selected = candidate
        elif selected != candidate:
            raise SnapshotMismatchError("node inputs combine different activation Config snapshots")
    return selected


def _binding_source_coordinate(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState | None,
    scope_run: ScopeRunCoordinate,
    activation_superstep: int,
    binding: ResolvedInputBinding[GraphValueT],
) -> tuple[
    ResolvedValueSource,
    GraphInputAvailabilityCoordinate[GraphValueT] | PublicationAvailabilityCoordinate[GraphValueT],
]:
    """Resolve one compiled binding to its actual source and frame coordinate.

    Predecessor bindings derive their source from the state-owned activation
    cause; ordinary bindings use the compiler-provided publication selection.
    Keeping this coordinate fact in one place prevents availability checks and
    materialization from drifting while leaving their frame/error policies
    independent.
    """

    source = binding.source
    if isinstance(source, CompiledPredecessorInput):
        if state is None:
            raise SnapshotMismatchError("predecessor input availability requires authoritative graph state")
        try:
            node = frontier_node(state.frontier, source.target)
            if node is None:
                raise InvalidRoutingCommandError("predecessor-bound activation is not present in the current frontier")
            selected = causal_input_source_for_cause(
                graph,
                state,
                state.superstep,
                node.cause,
                binding,
            )
        except InvalidRoutingCommandError as error:
            raise SnapshotMismatchError(str(error)) from error
        if isinstance(selected, GraphInputPort):
            return selected, _graph_input_coordinate(graph, scope_run)
        resolved = selected.source
        return resolved, _node_output_coordinate(graph, scope_run, resolved, selected.predecessor.superstep)
    if isinstance(source, GraphInputPort):
        return source, _graph_input_coordinate(graph, scope_run)
    selection = require_publication_selection(
        binding.publication,
        SnapshotMismatchError("compiled node-output binding lacks its activation selection"),
    )
    return source, _node_output_coordinate(graph, scope_run, source, selection.resolve(activation_superstep))


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
    if state is not None:
        if state.run_id != scope_run.graph_run_id:
            raise SnapshotMismatchError("predecessor input availability scope does not match authoritative state")
        if has_predecessor and activation_superstep != state.superstep:
            raise SnapshotMismatchError("predecessor input availability coordinate does not match authoritative state")
    for binding in plan.bindings.entries:
        _source, coordinate = _binding_source_coordinate(
            graph,
            state,
            scope_run,
            activation_superstep,
            binding,
        )
        if isinstance(coordinate, GraphInputAvailabilityCoordinate):
            if not frames.has_graph_input(coordinate):
                return False
        elif not frames.has_publication(coordinate):
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
        return decode_resume_input(graph, node_id, bytes(effective_input.payload))
    resume_coordinate = _resume_input_coordinate(activation, plan)
    if not has_predecessor:
        try:
            return frames.lookup(resume_coordinate).frame
        except SnapshotMismatchError:
            pass
    entries: list[NamedValue[GraphValueT]] = []
    source_frames: list[GraphInputFrame[GraphValueT] | NodeOutputFrame[GraphValueT]] = []
    for binding in plan.bindings.entries:
        source, coordinate = _binding_source_coordinate(
            graph,
            state,
            scope_run,
            state.superstep,
            binding,
        )
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
        source_frames.append(frame)
        value = _frame_value(frame, value_name)
        entries.append(NamedValue(binding.destination.local_name, value))
    return _make_node_input_frame(
        tuple(entries),
        plan.descriptor.declarations,
        activation_config=_activation_config_from_frames(tuple(source_frames)),
    )


__all__ = ["_require_node_materialization", "_resume_input_coordinate"]
