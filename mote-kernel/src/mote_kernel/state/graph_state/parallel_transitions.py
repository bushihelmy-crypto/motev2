"""Pure resource-admission transitions for one graph run."""

from dataclasses import replace

from mote_kernel.parallel import (
    AcquireResources,
    ParallelSnapshot,
    ParallelTransitionError,
    ResourceLock,
    reduce_parallel,
)
from mote_kernel.state.graph_state.command import UpdateGraphParallel
from mote_kernel.state.graph_state.model import GraphRunState, GraphRunStatus
from mote_kernel.state.graph_state.transition_guard import require_interrupt_generation
from mote_kernel.state.graph_state.validation import GraphStateTransitionError, validated_graph_run_state


def _validate_admission_transition(
    previous: ParallelSnapshot | None,
    proposed: ParallelSnapshot,
) -> None:
    if previous is None:
        replayed = ParallelSnapshot(tuple(ResourceLock(resource.resource_id) for resource in proposed.resources))
        prior_acquisitions = 0
    else:
        replayed = previous
        prior_acquisitions = len(previous.acquisitions)
        if proposed.acquisitions[:prior_acquisitions] != previous.acquisitions:
            raise GraphStateTransitionError("resource admission cannot rewrite committed acquisitions")
    try:
        for acquisition in proposed.acquisitions[prior_acquisitions:]:
            replayed = reduce_parallel(
                replayed,
                AcquireResources(acquisition.participant_id, acquisition.required),
            )
    except ParallelTransitionError as error:
        raise GraphStateTransitionError("resource admission is not a legal acquisition sequence") from error
    if replayed != proposed:
        raise GraphStateTransitionError("resource admission does not match its replayed acquisition sequence")


def update_graph_parallel(state: GraphRunState, command: UpdateGraphParallel) -> GraphRunState:
    """Commit one replayable extension of resource admission."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can update resource admission")
    if state.execution is not None:
        raise GraphStateTransitionError("resource admission cannot change during execution")
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("resource admission was based on a stale superstep")
    if command.expected_parallel != state.parallel:
        raise GraphStateTransitionError("resource admission was based on a stale snapshot")
    require_interrupt_generation(state, command.expected_interrupt_generation)
    _validate_admission_transition(state.parallel, command.parallel)
    return validated_graph_run_state(replace(state, parallel=command.parallel))


__all__: list[str] = []
