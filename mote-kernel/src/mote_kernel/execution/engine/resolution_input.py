"""Graph-owned decoding of one durable interrupt resolution."""

from typing import TypeVar

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.state.graph_state import GraphInterruptLifecycle, GraphRunState

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def require_resolution_binding(
    graph: CompiledGraph[InputT, OutputT],
    state: GraphRunState,
) -> None:
    """Require the compiled graph to own the durable codec recorded by state."""

    binding = graph.resolution
    codec = state.resolution_codec
    if binding is None and codec is None:
        return
    if binding is None or codec is None or binding.codec_id != codec.codec_id or binding.version != codec.version:
        raise SnapshotMismatchError("compiled graph resolution codec does not match durable graph state")


def effective_node_input(graph: CompiledGraph[InputT, OutputT], state: GraphRunState, ordinary_input: InputT) -> InputT:
    """Decode the current generation through the executor-owned graph binding."""

    require_resolution_binding(graph, state)
    interrupt = state.interrupt
    if interrupt is None:
        return ordinary_input
    if interrupt.lifecycle is GraphInterruptLifecycle.CONSUMED:
        return ordinary_input
    if interrupt.lifecycle is not GraphInterruptLifecycle.RESOLVED:
        raise SnapshotMismatchError("only a resolved interrupt can provide node input")
    binding = graph.resolution
    if binding is None:
        raise SnapshotMismatchError("resolved interrupt is missing its compiled graph decoder")
    payload = interrupt.resolution_payload
    if payload is None:
        raise SnapshotMismatchError("resolved interrupt is missing its durable payload")
    return binding.decoder.decode(bytes(payload))


__all__ = ["effective_node_input", "require_resolution_binding"]
