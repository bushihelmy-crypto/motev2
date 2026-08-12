"""Pure resource-admission transitions for one graph run."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import UpdateGraphResources
from mote_kernel.state.graph_state.model import GraphRunState, GraphRunStatus
from mote_kernel.state.graph_state.resource_command import AcquireResources
from mote_kernel.state.graph_state.resource_model import ResourceLock, ResourceSnapshot
from mote_kernel.state.graph_state.resource_reducer import ResourceTransitionError, reduce_resources
from mote_kernel.state.graph_state.validation import GraphStateTransitionError, validated_graph_run_state


def _validate_admission_transition(
    previous: ResourceSnapshot | None,
    proposed: ResourceSnapshot,
) -> None:
    if previous is None:
        replayed = ResourceSnapshot(tuple(ResourceLock(resource.resource_id) for resource in proposed.resources))
        prior_acquisitions = 0
    else:
        replayed = previous
        prior_acquisitions = len(previous.acquisitions)
        if proposed.acquisitions[:prior_acquisitions] != previous.acquisitions:
            raise GraphStateTransitionError("resource admission cannot rewrite committed acquisitions")
    try:
        for acquisition in proposed.acquisitions[prior_acquisitions:]:
            replayed = reduce_resources(
                replayed,
                AcquireResources(acquisition.participant_id, acquisition.required),
            )
    except ResourceTransitionError as error:
        raise GraphStateTransitionError("resource admission is not a legal acquisition sequence") from error
    if replayed != proposed:
        raise GraphStateTransitionError("resource admission does not match its replayed acquisition sequence")


def update_graph_resources(state: GraphRunState, command: UpdateGraphResources) -> GraphRunState:
    """Commit one replayable extension of resource admission."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can update resource admission")
    if state.execution is not None:
        raise GraphStateTransitionError("resource admission cannot change during execution")
    _validate_admission_transition(state.resources, command.resources)
    return validated_graph_run_state(replace(state, resources=command.resources))


__all__: list[str] = []
