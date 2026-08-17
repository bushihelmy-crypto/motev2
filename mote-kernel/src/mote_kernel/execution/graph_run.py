"""Pure projections between authoritative graph state and execution."""

from typing import TypeVar

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.state.graph_state import (
    GraphResumeInputCodec,
    GraphRunId,
    ParentGraphActivation,
    StartGraphRun,
    child_graph_run_id,
)

GraphValueT = TypeVar("GraphValueT")


def project_start_graph_command(
    graph: CompiledGraph[GraphValueT],
    run_id: GraphRunId,
    parent: ParentGraphActivation | None = None,
) -> StartGraphRun:
    if parent is not None and run_id != child_graph_run_id(parent.run_id, parent.superstep, parent.node_id):
        raise SnapshotMismatchError("child graph run identity does not match its parent activation")
    binding = graph.resume_input
    return StartGraphRun(
        run_id=run_id,
        definition_id=graph.definition_id,
        definition_version=graph.version,
        node_ids=graph.entries,
        parent=parent,
        resume_input_codec=(GraphResumeInputCodec(binding.codec_id, binding.version) if binding is not None else None),
    )


__all__ = ["project_start_graph_command"]
