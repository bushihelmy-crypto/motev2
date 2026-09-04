"""Scoped node-input materialization and graph-local resume codecs."""

from typing import TypeVar, cast

from mote_kernel.execution.engine.routing import (
    _graph_input_coordinate,
    _node_output_coordinate,
    feedback_source_for_cause,
)
from mote_kernel.execution.errors import (
    GraphValueAdmissionError,
    GraphValueUnavailableError,
    InvalidRoutingCommandError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.ports import (
    CompiledActivationRule,
    GraphInputPort,
    MaterializationPlan,
    NodeOutputPort,
    PublicationSelection,
    require_publication_selection,
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
        payload = binding.encoder.encode(values)
    except Exception as error:
        raise GraphValueAdmissionError("resume input encoder rejected the value frame") from error
    if type(payload) is not bytes:
        raise GraphValueAdmissionError("resume input encoder must return bytes")
    return OverrideGraphNodeInput(GraphResumeInputPayload(payload))


def _admit_override(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
    values: _GraphValues[GraphValueT],
) -> NodeInputFrame[GraphValueT]:
    plan = _require_node_materialization(graph, node_id)
    return _make_node_input_frame(
        tuple(NamedValue(name, value) for name, value in values.items()),
        plan.descriptor.declarations,
    )


def decode_resume_input(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
    payload: bytes,
) -> NodeInputFrame[GraphValueT]:
    binding = graph.resume_input
    if binding is None:
        raise SnapshotMismatchError("input override is missing its compiled graph decoder")
    try:
        candidate = cast(_GraphValues[GraphValueT] | bytes, binding.decoder.decode(payload))
    except Exception as error:
        raise GraphValueAdmissionError("resume input decoder rejected its opaque payload") from error
    if not isinstance(candidate, _GraphValues):
        raise GraphValueAdmissionError("resume input decoder must return Graph.Values")
    return _admit_override(graph, node_id, candidate)


def _source_coordinate(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    anchor_superstep: int,
    source: GraphInputPort | NodeOutputPort,
    publication: PublicationSelection | None,
) -> GraphInputAvailabilityCoordinate[GraphValueT] | PublicationAvailabilityCoordinate[GraphValueT]:
    if isinstance(source, GraphInputPort):
        return _graph_input_coordinate(graph, scope_run)
    selection = require_publication_selection(
        publication,
        SnapshotMismatchError("compiled node-output binding lacks its activation selection"),
    )
    return _node_output_coordinate(graph, scope_run, source, selection.resolve(anchor_superstep))


def _feedback_source_for_state(
    state: GraphRunState,
    rule: CompiledActivationRule[GraphValueT],
) -> tuple[GraphInputPort | NodeOutputPort, PublicationSelection | None, int]:
    try:
        node = frontier_node(state.frontier, rule.target)
        if node is None:
            raise InvalidRoutingCommandError("feedback activation is not present in the current frontier")
        selected = feedback_source_for_cause(state, rule.target, state.superstep, node.cause, rule)
    except InvalidRoutingCommandError as error:
        raise SnapshotMismatchError(str(error)) from error
    return selected.source, selected.publication, state.superstep


def node_inputs_available(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    activation_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
    node_id: GraphNodeId,
    state: GraphRunState | None = None,
) -> bool:
    plan = _require_node_materialization(graph, node_id)
    if state is not None:
        if state.run_id != scope_run.graph_run_id:
            raise SnapshotMismatchError("feedback input availability scope does not match authoritative state")
        if (
            any(isinstance(binding.source, CompiledActivationRule) for binding in plan.bindings.entries)
            and activation_superstep != state.superstep
        ):
            raise SnapshotMismatchError("feedback input availability coordinate does not match authoritative state")
    for binding in plan.bindings.entries:
        effective = binding.source
        publication = binding.publication
        anchor = activation_superstep
        if isinstance(effective, CompiledActivationRule):
            if state is None:
                raise SnapshotMismatchError("feedback input availability requires authoritative graph state")
            effective, publication, anchor = _feedback_source_for_state(state, effective)
        coordinate = _source_coordinate(graph, scope_run, anchor, effective, publication)
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
    has_feedback = any(isinstance(binding.source, CompiledActivationRule) for binding in plan.bindings.entries)
    if isinstance(node.settlement.input, OverrideGraphNodeInput):
        if has_feedback:
            raise SnapshotMismatchError("feedback activation cannot use an input override")
        return True
    coordinate = _resume_input_coordinate(
        stable_activation(scope_run, GraphActivationIdentity(state.run_id, state.superstep, node_id)),
        plan,
    )
    if not has_feedback and frames.has_resume_input(coordinate):
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
    # A feedback binding must always be resolved from the state-owned cause;
    # neither an override nor a cached frame may replace that selection.
    has_feedback = any(isinstance(binding.source, CompiledActivationRule) for binding in plan.bindings.entries)
    if isinstance(effective_input, OverrideGraphNodeInput):
        if has_feedback:
            raise SnapshotMismatchError("feedback activation cannot use an input override")
        return decode_resume_input(graph, node_id, bytes(effective_input.payload))
    resume_coordinate = _resume_input_coordinate(activation, plan)
    if not any(isinstance(binding.source, CompiledActivationRule) for binding in plan.bindings.entries):
        try:
            return frames.lookup(resume_coordinate).frame
        except SnapshotMismatchError:
            pass
    entries: list[NamedValue[GraphValueT]] = []
    for binding in plan.bindings.entries:
        source = binding.source
        publication = binding.publication
        anchor = state.superstep
        if isinstance(source, CompiledActivationRule):
            source, publication, anchor = _feedback_source_for_state(state, source)
        coordinate = _source_coordinate(graph, scope_run, anchor, source, publication)
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
        value = _frame_value(frame, value_name)
        entries.append(NamedValue(binding.destination.local_name, value))
    return _make_node_input_frame(tuple(entries), plan.descriptor.declarations)


__all__ = ["_require_node_materialization", "_resume_input_coordinate"]
