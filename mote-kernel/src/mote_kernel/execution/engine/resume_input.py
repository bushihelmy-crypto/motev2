"""Scoped node-input materialization and graph-local resume codecs."""

from typing import TypeVar

from mote_kernel.execution.engine.routing import _graph_input_coordinate, _node_output_coordinate
from mote_kernel.execution.errors import (
    GraphValueAdmissionError,
    GraphValueUnavailableError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.ports import (
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
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.run_context import (
    ResumeInputAvailabilityCoordinate,
    ScopedFrameAvailability,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    FailedGraphNode,
    GraphFrontierNode,
    GraphNodeId,
    GraphResumeInputPayload,
    GraphRunState,
    OverrideGraphNodeInput,
    PendingGraphNode,
    UseStepRequestInput,
    frontier_node,
)

GraphValueT = TypeVar("GraphValueT")


def _resume_input_coordinate(
    activation: StableActivation,
    plan: MaterializationPlan[GraphValueT],
) -> ResumeInputAvailabilityCoordinate[GraphValueT]:
    return ResumeInputAvailabilityCoordinate(
        activation,
        plan.descriptor.identity,
    )


def _require_decoded_values(
    candidate: _GraphValues[GraphValueT] | bytes,
) -> _GraphValues[GraphValueT]:
    if not isinstance(candidate, _GraphValues):
        raise GraphValueAdmissionError("resume input decoder must return Graph.Values")
    return candidate


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
    plan = graph.transition.materializations[node_id]
    declarations = tuple((entry.name, entry.descriptor) for entry in plan.descriptor.declarations.entries)
    return _make_node_input_frame(
        tuple(NamedValue(name, value) for name, value in values.items()),
        declarations,
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
        candidate = binding.decoder.decode(payload)
    except Exception as error:
        raise GraphValueAdmissionError("resume input decoder rejected its opaque payload") from error
    return _admit_override(graph, node_id, _require_decoded_values(candidate))


def _publication_value(
    graph: CompiledGraph[GraphValueT],
    frames: ScopedFrameIndex[GraphValueT],
    scope_run: ScopeRunCoordinate,
    source: NodeOutputPort,
    output_name: str,
    anchor_superstep: int,
    selection: PublicationSelection | None,
) -> GraphValueT:
    selection = require_publication_selection(
        selection,
        SnapshotMismatchError("compiled node-output binding lacks its activation selection"),
    )
    coordinate = _node_output_coordinate(graph, scope_run, source, selection.resolve(anchor_superstep))
    try:
        frame = frames.lookup(coordinate).frame
    except SnapshotMismatchError as error:
        raise GraphValueUnavailableError(
            f"node output {source.node_id!r}.{output_name!r} is unavailable at {scope_run!r}"
        ) from error
    return _frame_value(frame, output_name)


def node_inputs_available(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    activation_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
    node_id: GraphNodeId,
) -> bool:
    for binding in graph.transition.materializations[node_id].bindings.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            graph_input_coordinate = _graph_input_coordinate(graph, scope_run)
            if not frames.has_graph_input(graph_input_coordinate):
                return False
        else:
            selection = require_publication_selection(
                binding.publication,
                SnapshotMismatchError("compiled node-output binding lacks its activation selection"),
            )
            publication_coordinate = _node_output_coordinate(
                graph, scope_run, source, selection.resolve(activation_superstep)
            )
            if not frames.has_publication(publication_coordinate):
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
    if isinstance(node.settlement.input, OverrideGraphNodeInput):
        return True
    plan = graph.transition.materializations[node_id]
    coordinate = _resume_input_coordinate(StableActivation(scope_run, state.superstep, node_id), plan)
    return frames.has_resume_input(coordinate) or node_inputs_available(
        graph,
        scope_run,
        state.superstep,
        frames,
        node_id,
    )


def materialize_node_input(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameIndex[GraphValueT],
    node_id: GraphNodeId,
    *,
    failed_retry_input: UseStepRequestInput | None = None,
) -> NodeInputFrame[GraphValueT]:
    require_resume_input_binding(graph, state)
    if state.run_id != scope_run.graph_run_id:
        raise SnapshotMismatchError("node materialization scope does not match authoritative state")
    node = frontier_node(state.frontier, node_id)
    match node, failed_retry_input:
        case GraphFrontierNode(settlement=PendingGraphNode(input=effective_input)), None:
            pass
        case GraphFrontierNode(settlement=FailedGraphNode()), UseStepRequestInput() as effective_input:
            pass
        case _:
            raise SnapshotMismatchError(
                "effective input requires a current pending node or a current failed node with failed retry input"
            )
    activation = StableActivation(scope_run, state.superstep, node_id)
    plan = graph.transition.materializations[node_id]
    if isinstance(effective_input, OverrideGraphNodeInput):
        return decode_resume_input(graph, node_id, bytes(effective_input.payload))
    resume_coordinate = _resume_input_coordinate(activation, plan)
    try:
        return frames.lookup(resume_coordinate).frame
    except SnapshotMismatchError:
        pass
    entries: list[NamedValue[GraphValueT]] = []
    for binding in plan.bindings.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            coordinate = _graph_input_coordinate(graph, scope_run)
            try:
                value = _frame_value(frames.lookup(coordinate).frame, source.name)
            except SnapshotMismatchError as error:
                raise GraphValueUnavailableError(
                    f"graph input {source.name!r} is unavailable at {scope_run!r}"
                ) from error
        else:
            value = _publication_value(
                graph,
                frames,
                scope_run,
                source,
                source.output_name,
                state.superstep,
                binding.publication,
            )
        entries.append(NamedValue(binding.destination.local_name, value))
    declarations = tuple((entry.name, entry.descriptor) for entry in plan.descriptor.declarations.entries)
    return _make_node_input_frame(tuple(entries), declarations)


__all__: list[str] = []
