"""Execution-local task projections."""

from dataclasses import dataclass
from typing import Generic, NewType, TypeVar

from mote_kernel.execution.graph.values import NodeInputFrame
from mote_kernel.state.graph_state.identity import GraphNodeId, GraphRunId

TaskId = NewType("TaskId", str)
GraphValueT = TypeVar("GraphValueT")


def task_identity(run_id: GraphRunId, superstep: int, node_id: GraphNodeId) -> TaskId:
    return TaskId(f"{len(run_id)}:{run_id}:{superstep}:{len(node_id)}:{node_id}")


@dataclass(frozen=True, slots=True)
class GraphTask:
    task_id: TaskId
    run_id: GraphRunId
    superstep: int
    node_id: GraphNodeId

    @property
    def sort_key(self) -> tuple[int, GraphNodeId, TaskId]:
        return (self.superstep, self.node_id, self.task_id)


@dataclass(frozen=True, slots=True)
class ExecutableTask(Generic[GraphValueT]):
    task: GraphTask
    effective_input: NodeInputFrame[GraphValueT]


__all__ = ["ExecutableTask", "GraphTask", "TaskId", "task_identity"]
