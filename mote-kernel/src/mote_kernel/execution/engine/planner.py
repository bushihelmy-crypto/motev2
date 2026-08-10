"""Deterministic ready-task planning."""

from typing import TypeVar

from mote_kernel.execution.engine.task import GraphTask, task_identity
from mote_kernel.execution.errors import ExecutionLimitError, InvalidExecutionSnapshotError, SnapshotMismatchError
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


def _require_identity(value: str, field: str) -> None:
    if not value or value != value.strip():
        raise InvalidExecutionSnapshotError(f"{field} must be non-empty and trimmed")


def _validate_snapshot(graph: CompiledGraph[InputT, OutputT], snapshot: ExecutionSnapshot) -> None:
    _require_identity(snapshot.run_id, "graph run identity")
    _require_identity(snapshot.definition_id, "graph definition identity")
    if snapshot.definition_version < 1:
        raise InvalidExecutionSnapshotError("graph definition version must be positive")
    if snapshot.superstep < 0:
        raise InvalidExecutionSnapshotError("snapshot superstep cannot be negative")
    if snapshot.parent is not None:
        _require_identity(snapshot.parent.run_id, "parent graph run identity")
        _require_identity(snapshot.parent.task_id, "parent graph task identity")
        if snapshot.parent.run_id == snapshot.run_id:
            raise InvalidExecutionSnapshotError("a graph run cannot be its own parent")
    if len(snapshot.frontier) != len(set(snapshot.frontier)):
        raise InvalidExecutionSnapshotError("snapshot frontier contains duplicate nodes")
    for node_id in snapshot.frontier:
        _require_identity(node_id, "frontier node identity")
    unknown_nodes = tuple(sorted(node_id for node_id in snapshot.frontier if node_id not in graph.nodes))
    if unknown_nodes:
        raise InvalidExecutionSnapshotError(f"snapshot frontier contains unknown nodes: {unknown_nodes!r}")
    if snapshot.status in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED} and snapshot.frontier:
        raise InvalidExecutionSnapshotError("a terminal snapshot cannot retain a frontier")
    if snapshot.status is ExecutionStatus.SUSPENDED and not snapshot.frontier:
        raise InvalidExecutionSnapshotError("a suspended snapshot requires a recoverable frontier")


def plan_tasks(
    graph: CompiledGraph[InputT, OutputT], snapshot: ExecutionSnapshot, limits: ExecutionLimits
) -> tuple[GraphTask, ...]:
    """Materialize the committed frontier as a stable, side-effect-free task batch."""

    _validate_limits(limits)
    _validate_snapshot(graph, snapshot)
    if snapshot.definition_id != graph.definition_id or snapshot.definition_version != graph.version:
        raise SnapshotMismatchError("execution snapshot does not match the compiled graph identity and version")
    if snapshot.status is not ExecutionStatus.RUNNING:
        return ()
    if snapshot.superstep >= limits.max_supersteps:
        raise ExecutionLimitError("graph run reached its superstep limit")
    if not snapshot.frontier:
        raise InvalidExecutionSnapshotError("a running snapshot requires a non-empty frontier")
    if len(snapshot.frontier) > limits.max_parallel_tasks:
        raise ExecutionLimitError("planned frontier exceeds the parallel task limit")
    tasks = tuple(
        GraphTask(
            task_id=task_identity(snapshot.run_id, snapshot.superstep, node_id),
            run_id=snapshot.run_id,
            superstep=snapshot.superstep,
            node_id=node_id,
        )
        for node_id in sorted(snapshot.frontier)
    )
    return tuple(sorted(tasks, key=lambda task: task.sort_key))


__all__ = ["plan_tasks"]
