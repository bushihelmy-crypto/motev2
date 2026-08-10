"""Graph task identities and immutable task descriptions."""

from dataclasses import dataclass
from typing import NewType

from mote_kernel.execution.graph import NodeId
from mote_kernel.execution.snapshot import GraphRunId

TaskId = NewType("TaskId", str)


def task_identity(run_id: GraphRunId, superstep: int, node_id: NodeId) -> TaskId:
    """Derive a collision-resistant stable identity from committed task coordinates."""

    return TaskId(f"{len(run_id)}:{run_id}:{superstep}:{len(node_id)}:{node_id}")


@dataclass(frozen=True, slots=True)
class GraphTask:
    """One deterministic node invocation planned for a superstep."""

    task_id: TaskId
    run_id: GraphRunId
    superstep: int
    node_id: NodeId

    @property
    def sort_key(self) -> tuple[int, NodeId, TaskId]:
        """Return the canonical ordering key used across execution stages."""

        return (self.superstep, self.node_id, self.task_id)


__all__ = ["GraphTask", "TaskId", "task_identity"]
