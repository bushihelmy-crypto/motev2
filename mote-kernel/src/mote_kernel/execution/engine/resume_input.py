"""Node-scoped resume input encoding, guarding, and materialization."""

from typing import TypeVar

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.state.graph_state import (
    GraphNodeId,
    GraphResumeInputPayload,
    GraphRunState,
    OverrideGraphNodeInput,
    PendingGraphNode,
    UseStepRequestInput,
    frontier_node,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def require_resume_input_binding(graph: CompiledGraph[InputT, OutputT], state: GraphRunState) -> None:
    binding = graph.resume_input
    codec = state.resume_input_codec
    if binding is None and codec is None:
        return
    if binding is None or codec is None or binding.codec_id != codec.codec_id or binding.version != codec.version:
        raise SnapshotMismatchError("compiled graph resume input codec does not match durable graph state")


def encode_resume_input(graph: CompiledGraph[InputT, OutputT], value: InputT) -> OverrideGraphNodeInput:
    binding = graph.resume_input
    if binding is None:
        raise SnapshotMismatchError("graph does not define a resume input codec")
    return OverrideGraphNodeInput(GraphResumeInputPayload(binding.encoder.encode(value)))


def effective_node_input(
    graph: CompiledGraph[InputT, OutputT],
    state: GraphRunState,
    node_id: GraphNodeId,
    ordinary_input: InputT,
) -> InputT:
    require_resume_input_binding(graph, state)
    node = frontier_node(state.frontier, node_id)
    if node is None or not isinstance(node.settlement, PendingGraphNode):
        raise SnapshotMismatchError("effective input requires a current pending node")
    binding = node.settlement.input
    if isinstance(binding, UseStepRequestInput):
        return ordinary_input
    decoder = graph.resume_input
    if decoder is None:
        raise SnapshotMismatchError("input override is missing its compiled graph decoder")
    return decoder.decoder.decode(bytes(binding.payload))


__all__ = ["effective_node_input", "encode_resume_input", "require_resume_input_binding"]
