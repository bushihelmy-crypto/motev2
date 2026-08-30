"""Pure resource admission planning by authoritative node identity."""

from dataclasses import dataclass
from typing import TypeVar

from mote_kernel.execution.engine.routing import _graph_input_coordinate, _node_output_coordinate
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import GraphValueAdmissionError, ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import GraphInputPort, require_publication_selection
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    GraphInputFrame,
    GraphOutputView,
    NamedValue,
    NodeInputFrame,
    _frame_value,
    _graph_input_from_node_input,
    _GraphValues,
    _make_graph_input_frame,
    _make_graph_output_view,
)
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.run_context import ScopedFrameIndex
from mote_kernel.state.graph_state import (
    AcquireResources,
    GraphNodeId,
    ResourceLock,
    ResourceSnapshot,
    ResourceTransitionError,
    reduce_resources,
)

GraphValueT = TypeVar("GraphValueT")


def admit_graph_input(
    graph: CompiledGraph[GraphValueT],
    values: _GraphValues[GraphValueT],
) -> GraphInputFrame[GraphValueT]:
    return _make_graph_input_frame(values, graph.graph_input_descriptor.declarations)


def admit_child_graph_input(
    graph: CompiledGraph[GraphValueT],
    frame: NodeInputFrame[GraphValueT],
) -> GraphInputFrame[GraphValueT]:
    return _graph_input_from_node_input(frame, graph.graph_input_descriptor.declarations)


def project_graph_outputs(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    completion_superstep: int,
    frames: ScopedFrameIndex[GraphValueT],
) -> GraphOutputView[GraphValueT]:
    entries: list[NamedValue[GraphValueT]] = []
    for binding in graph.transition.graph_outputs.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            graph_input_coordinate = _graph_input_coordinate(graph, scope_run)
            frame = frames.lookup(graph_input_coordinate).frame
            value = _frame_value(frame, source.name)
        else:
            selection = require_publication_selection(
                binding.publication,
                GraphValueAdmissionError("compiled graph output binding lacks its activation selection"),
            )
            publication_coordinate = _node_output_coordinate(
                graph, scope_run, source, selection.resolve(completion_superstep)
            )
            try:
                frame = frames.lookup(publication_coordinate).frame
            except SnapshotMismatchError as error:
                raise GraphValueAdmissionError(
                    f"graph output source {source.node_id!r}.{source.output_name!r} is unavailable"
                ) from error
            value = _frame_value(frame, source.output_name)
        entries.append(NamedValue(binding.destination.boundary_name, value))
    return _make_graph_output_view(tuple(entries), graph.graph_output_descriptor.declarations)


def initial_resource_snapshot(graph: CompiledGraph[GraphValueT]) -> ResourceSnapshot:
    """Create the replay base used only while projecting one atomic claim."""

    return ResourceSnapshot(tuple(ResourceLock(resource_id) for resource_id in graph.transition.resource_order))


@dataclass(frozen=True, slots=True)
class TaskAdmission:
    snapshot: ResourceSnapshot
    admitted_node_ids: tuple[GraphNodeId, ...]
    waiting_node_ids: tuple[GraphNodeId, ...]


def admit_tasks(
    graph: CompiledGraph[GraphValueT],
    tasks: tuple[GraphTask, ...],
    snapshot: ResourceSnapshot,
) -> TaskAdmission:
    if tuple(lock.resource_id for lock in snapshot.resources) != graph.transition.resource_order:
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
        if not isinstance(definition, CallableNodeDefinition):
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


def claim_resource_snapshot(
    graph: CompiledGraph[GraphValueT],
    tasks: tuple[GraphTask, ...],
) -> ResourceSnapshot | None:
    resource_tasks = tuple(
        task
        for task in tasks
        if isinstance(definition := graph.nodes[task.node_id], CallableNodeDefinition) and definition.resources
    )
    if not resource_tasks:
        return None
    admission = admit_tasks(graph, resource_tasks, initial_resource_snapshot(graph))
    if not admission.snapshot.acquisitions:
        raise ResultCollectionError("resource admission did not create acquisition participants")
    return admission.snapshot


def select_executable_tasks(
    graph: CompiledGraph[GraphValueT],
    tasks: tuple[GraphTask, ...],
    snapshot: ResourceSnapshot | None,
    limits: ExecutionLimits,
    *,
    active_count: int,
    started_node_ids: set[GraphNodeId] | frozenset[GraphNodeId],
) -> tuple[GraphTask, ...]:
    """Select the next ordinary tasks using the runtime/recovery shared policy."""

    available_slots = limits.max_parallel_tasks - active_count
    if available_slots <= 0:
        return ()
    acquisitions = {
        acquisition.node_id: acquisition for acquisition in (snapshot.acquisitions if snapshot is not None else ())
    }
    selected: list[GraphTask] = []
    for task in sorted(tasks, key=lambda item: item.sort_key):
        if task.node_id in started_node_ids:
            continue
        definition = graph.nodes[task.node_id]
        if not isinstance(definition, CallableNodeDefinition):
            continue
        if definition.resources:
            acquisition = acquisitions.get(task.node_id)
            if acquisition is None or not acquisition.admitted or acquisition.required != definition.resources:
                continue
        selected.append(task)
        if len(selected) == available_slots:
            break
    return tuple(selected)


__all__ = [
    "TaskAdmission",
    "admit_tasks",
    "claim_resource_snapshot",
    "initial_resource_snapshot",
    "select_executable_tasks",
]
