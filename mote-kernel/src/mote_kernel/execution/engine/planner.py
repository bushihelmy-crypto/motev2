"""Deterministic ready-task planning."""

from typing import TypeVar

from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.task import GraphTask, task_identity
from mote_kernel.execution.errors import ExecutionLimitError, InvalidExecutionSnapshotError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.snapshot import ExecutionSnapshot, ExecutionStatus

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def _validate_limits(limits: ExecutionLimits) -> None:
    if limits.max_supersteps < 1:
        raise ExecutionLimitError("max_supersteps must be positive")
    if limits.max_parallel_tasks < 1:
        raise ExecutionLimitError("max_parallel_tasks must be positive")


def plan_tasks(
    graph: CompiledGraph[InputT, OutputT], snapshot: ExecutionSnapshot, limits: ExecutionLimits
) -> tuple[GraphTask, ...]:
    """Materialize the committed frontier as a stable, side-effect-free task batch."""

    _validate_limits(limits)
    require_snapshot_matches_graph(graph, snapshot)
    declared_joins = {(edge.sources, edge.target) for edges in graph.joins_by_source.values() for edge in edges}
    if any((progress.sources, progress.target) not in declared_joins for progress in snapshot.join_progress):
        raise InvalidExecutionSnapshotError("snapshot references unknown join progress")
    unknown_nodes = tuple(sorted(node_id for node_id in snapshot.frontier if node_id not in graph.nodes))
    if unknown_nodes:
        raise InvalidExecutionSnapshotError(f"snapshot frontier contains unknown nodes: {unknown_nodes!r}")
    if snapshot.status is not ExecutionStatus.RUNNING:
        return ()
    if snapshot.superstep >= limits.max_supersteps:
        raise ExecutionLimitError("graph run reached its superstep limit")
    if len(snapshot.frontier) > limits.max_parallel_tasks:
        raise ExecutionLimitError("planned frontier exceeds the parallel task limit")
    return tuple(
        GraphTask(
            task_id=task_identity(snapshot.run_id, snapshot.superstep, node_id),
            run_id=snapshot.run_id,
            superstep=snapshot.superstep,
            node_id=node_id,
        )
        for node_id in sorted(snapshot.frontier)
    )


__all__ = ["plan_tasks"]
