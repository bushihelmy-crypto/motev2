"""Deterministic pending-node task planning."""

from typing import TypeVar

from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.task import GraphTask, task_identity
from mote_kernel.execution.errors import ExecutionLimitError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.state.graph_state import GraphRunState, GraphRunStatus, pending_node_ids

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def plan_tasks(
    graph: CompiledGraph[InputT, OutputT], state: GraphRunState, limits: ExecutionLimits
) -> tuple[GraphTask, ...]:
    require_snapshot_matches_graph(graph, state)
    if state.status is not GraphRunStatus.RUNNING:
        return ()
    node_ids = pending_node_ids(state.frontier)
    if state.superstep >= limits.max_supersteps:
        raise ExecutionLimitError("graph run reached its superstep limit")
    return tuple(
        GraphTask(task_identity(state.run_id, state.superstep, node_id), state.run_id, state.superstep, node_id)
        for node_id in node_ids
    )


__all__ = ["plan_tasks"]
