"""Pure admission planning for graph tasks with resource requirements."""

from dataclasses import dataclass
from typing import TypeVar

from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.graph import CompiledGraph, NodeDefinition
from mote_kernel.state.graph_state import (
    AcquireResources,
    ParticipantId,
    ResourceSnapshot,
    ResourceTransitionError,
    reduce_resources,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class TaskAdmission:
    """A deterministic proposal that must be committed before execution."""

    snapshot: ResourceSnapshot
    admitted: tuple[GraphTask, ...]
    waiting: tuple[GraphTask, ...]


def admit_tasks(
    graph: CompiledGraph[InputT, OutputT],
    tasks: tuple[GraphTask, ...],
    snapshot: ResourceSnapshot,
) -> TaskAdmission:
    """Propose ordered resource acquisitions for tasks not already represented."""

    task_by_participant = {ParticipantId(task.task_id): task for task in tasks}
    if len(task_by_participant) != len(tasks):
        raise ResourceTransitionError("admission tasks must have unique identities")
    known_participants = {acquisition.participant_id for acquisition in snapshot.acquisitions}
    if not known_participants <= set(task_by_participant):
        raise ResourceTransitionError("resource snapshot contains an acquisition outside the planned task batch")

    proposed = snapshot
    acquisition_by_participant = {acquisition.participant_id: acquisition for acquisition in snapshot.acquisitions}
    admitted: list[GraphTask] = []
    waiting: list[GraphTask] = []
    for task in sorted(tasks, key=lambda item: item.sort_key):
        node = graph.nodes.get(task.node_id)
        if node is None:
            raise ResourceTransitionError("admission task references an unknown graph node")
        if not isinstance(node, NodeDefinition):
            raise ResourceTransitionError("admission only accepts executable node tasks")
        requirements = node.resources
        participant_id = ParticipantId(task.task_id)
        acquisition = acquisition_by_participant.get(participant_id)
        if not requirements:
            if acquisition is not None:
                raise ResourceTransitionError("resource-free task unexpectedly has an acquisition")
            admitted.append(task)
            continue
        if acquisition is None:
            proposed = reduce_resources(proposed, AcquireResources(participant_id, requirements))
            acquisition = proposed.acquisitions[-1]
            acquisition_by_participant[participant_id] = acquisition
        elif acquisition.required != requirements:
            raise ResourceTransitionError("task acquisition does not match its compiled resource requirements")
        if acquisition.admitted:
            admitted.append(task)
        else:
            waiting.append(task)

    return TaskAdmission(
        proposed,
        tuple(admitted),
        tuple(waiting),
    )


__all__ = ["TaskAdmission", "admit_tasks"]
