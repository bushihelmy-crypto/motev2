"""Validated settlement of one complete graph superstep."""

from typing import TypeVar

from mote_kernel.execution.engine.collector import collect_results
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.engine.transition import select_transition
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.result import TaskResult
from mote_kernel.execution.snapshot import ExecutionSnapshot
from mote_kernel.execution.transition import ExecutionTransition

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def settle_tasks(
    graph: CompiledGraph[InputT, OutputT],
    snapshot: ExecutionSnapshot,
    planned_tasks: tuple[GraphTask, ...],
    results: tuple[TaskResult[OutputT], ...],
) -> ExecutionTransition:
    """Validate a full task batch and select its authoritative transition."""

    require_snapshot_matches_graph(graph, snapshot)
    collected = collect_results(snapshot, planned_tasks, results)
    return select_transition(graph, snapshot, collected)


__all__ = ["settle_tasks"]
