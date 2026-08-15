"""Pure resource admission planning by authoritative node identity."""

from dataclasses import dataclass
from typing import TypeVar

from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.graph import CompiledGraph, NodeDefinition
from mote_kernel.state.graph_state import (
    AcquireResources,
    GraphNodeId,
    ResourceLock,
    ResourceSnapshot,
    ResourceTransitionError,
    reduce_resources,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def initial_resource_snapshot(graph: CompiledGraph[InputT, OutputT]) -> ResourceSnapshot:
    """Create the replay base used only while projecting one atomic claim."""

    return ResourceSnapshot(tuple(ResourceLock(resource_id) for resource_id in graph.resource_order))


@dataclass(frozen=True, slots=True)
class TaskAdmission:
    snapshot: ResourceSnapshot
    admitted_node_ids: tuple[GraphNodeId, ...]
    waiting_node_ids: tuple[GraphNodeId, ...]


def admit_tasks(
    graph: CompiledGraph[InputT, OutputT],
    tasks: tuple[GraphTask, ...],
    snapshot: ResourceSnapshot,
) -> TaskAdmission:
    if tuple(lock.resource_id for lock in snapshot.resources) != graph.resource_order:
        raise ResourceTransitionError("resource snapshot does not match compiled resource order")
    task_by_node = {task.node_id: task for task in tasks}
    if len(task_by_node) != len(tasks):
        raise ResourceTransitionError("admission tasks must have unique node identities")
    if not set(task_by_node) <= set(graph.nodes):
        raise ResourceTransitionError("admission task references an unknown graph node")
    known = {acquisition.node_id for acquisition in snapshot.acquisitions}
    if not known <= set(task_by_node):
        raise ResourceTransitionError("resource snapshot contains an acquisition outside planned nodes")
    proposed = snapshot
    acquisitions = {acquisition.node_id: acquisition for acquisition in snapshot.acquisitions}
    admitted: list[GraphNodeId] = []
    waiting: list[GraphNodeId] = []
    for task in sorted(tasks, key=lambda item: item.sort_key):
        definition = graph.nodes[task.node_id]
        if not isinstance(definition, NodeDefinition):
            raise ResourceTransitionError("admission only accepts executable node tasks")
        requirements = definition.resources
        acquisition = acquisitions.get(task.node_id)
        if not requirements:
            if acquisition is not None:
                raise ResourceTransitionError("resource-free node unexpectedly has an acquisition")
            continue
        if acquisition is None:
            proposed = reduce_resources(proposed, AcquireResources(task.node_id, requirements))
            acquisition = proposed.acquisitions[-1]
            acquisitions[task.node_id] = acquisition
        elif acquisition.required != requirements:
            raise ResourceTransitionError("node acquisition does not match compiled requirements")
        (admitted if acquisition.admitted else waiting).append(task.node_id)
    return TaskAdmission(proposed, tuple(admitted), tuple(waiting))


__all__ = ["TaskAdmission", "admit_tasks", "initial_resource_snapshot"]
