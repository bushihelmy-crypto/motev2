"""Validation and execution of committed resource waves."""

from typing import TypeVar

from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph, NodeDefinition
from mote_kernel.execution.result import TaskResult
from mote_kernel.state.graph_state import (
    GraphNodeId,
    ReleaseResources,
    ResourceLock,
    ResourceSnapshot,
    reduce_resources,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def initial_resource_snapshot(graph: CompiledGraph[InputT, OutputT]) -> ResourceSnapshot:
    return ResourceSnapshot(tuple(ResourceLock(resource_id) for resource_id in graph.resource_order))


def validated_resource_nodes(
    graph: CompiledGraph[InputT, OutputT],
    tasks: tuple[GraphTask, ...],
    resources: ResourceSnapshot | None,
) -> frozenset[GraphNodeId]:
    if resources is not None and tuple(lock.resource_id for lock in resources.resources) != graph.resource_order:
        raise ResultCollectionError("committed resources do not match graph resource order")
    resource_nodes = frozenset(
        task.node_id
        for task in tasks
        if isinstance(definition := graph.nodes[task.node_id], NodeDefinition) and definition.resources
    )
    if resources is not None and {item.node_id for item in resources.acquisitions} != set(resource_nodes):
        raise ResultCollectionError("committed acquisitions do not exactly cover resource-requiring nodes")
    if resources is not None:
        acquisitions = {item.node_id: item for item in resources.acquisitions}
        for node_id in resource_nodes:
            definition = graph.nodes[node_id]
            if not isinstance(definition, NodeDefinition) or acquisitions[node_id].required != definition.resources:
                raise ResultCollectionError("committed acquisition does not match compiled requirements")
    return resource_nodes


async def execute_resource_waves(
    graph: CompiledGraph[InputT, OutputT],
    executables: tuple[ExecutableTask[InputT], ...],
    resources: ResourceSnapshot,
    resource_nodes: frozenset[GraphNodeId],
) -> tuple[TaskResult[OutputT], ...]:
    current = resources
    remaining = set(resource_nodes)
    nonresource = tuple(item for item in executables if item.task.node_id not in resource_nodes)
    by_node = {item.task.node_id: item for item in executables}
    collected: list[TaskResult[OutputT]] = []
    first_wave = True
    while remaining:
        admitted = tuple(
            acquisition.node_id
            for acquisition in current.acquisitions
            if acquisition.node_id in remaining and acquisition.admitted
        )
        if not admitted:
            raise ResultCollectionError("resource scheduler cannot advance committed acquisition")
        wave = (
            (*nonresource, *(by_node[node_id] for node_id in admitted))
            if first_wave
            else tuple(by_node[node_id] for node_id in admitted)
        )
        collected.extend(await execute_tasks(graph, wave))
        for node_id in reversed(admitted):
            current = reduce_resources(current, ReleaseResources(node_id))
            remaining.remove(node_id)
        first_wave = False
    return tuple(collected)


__all__ = ["execute_resource_waves", "initial_resource_snapshot", "validated_resource_nodes"]
