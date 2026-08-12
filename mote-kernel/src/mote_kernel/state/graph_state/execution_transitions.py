"""Pure execution lease and settlement transitions for one graph run."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    AdvanceGraphRun,
    ClaimGraphExecution,
    CompleteGraphRun,
    FailGraphExecution,
    FenceGraphExecution,
    StartGraphRun,
)
from mote_kernel.state.graph_state.interrupt_transitions import consume_graph_resolution
from mote_kernel.state.graph_state.model import (
    GraphExecutionLease,
    GraphExecutionToken,
    GraphJoinProgress,
    GraphRunState,
    GraphRunStatus,
)
from mote_kernel.state.graph_state.transition_guard import require_execution_lease
from mote_kernel.state.graph_state.validation import GraphStateTransitionError, validated_graph_run_state


def _join_sort_key(progress: GraphJoinProgress) -> tuple[tuple[str, ...], str]:
    return (progress.sources, progress.target)


def start_graph_run(command: StartGraphRun) -> GraphRunState:
    """Create the initial recoverable position for one graph run."""

    return validated_graph_run_state(
        GraphRunState(
            run_id=command.run_id,
            definition_id=command.definition_id,
            definition_version=command.definition_version,
            status=GraphRunStatus.RUNNING,
            superstep=0,
            frontier=tuple(sorted(command.frontier)),
            parent=command.parent,
            resolution_codec=command.resolution_codec,
        )
    )


def claim_graph_execution(state: GraphRunState, command: ClaimGraphExecution) -> GraphRunState:
    """Claim one exact task batch for a new execution generation."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can claim execution")
    if state.execution is not None:
        raise GraphStateTransitionError("graph already has an active execution lease")
    token = GraphExecutionToken(state.execution_sequence + 1, command.attempt_id)
    return validated_graph_run_state(
        replace(
            state,
            execution_sequence=token.generation,
            execution=GraphExecutionLease(token, tuple(sorted(command.task_ids))),
        )
    )


def fence_graph_execution(state: GraphRunState, command: FenceGraphExecution) -> GraphRunState:
    """Clear the exact active execution lease after external fencing."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can fence execution")
    require_execution_lease(state, command.execution)
    return validated_graph_run_state(replace(state, execution=None))


def advance_graph_run(state: GraphRunState, command: AdvanceGraphRun) -> GraphRunState:
    """Commit a claimed superstep's next recoverable position."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can advance")
    require_execution_lease(state, command.execution)
    return validated_graph_run_state(
        replace(
            state,
            superstep=state.superstep + 1,
            frontier=tuple(sorted(command.frontier)),
            join_progress=tuple(sorted(command.join_progress, key=_join_sort_key)),
            resources=None,
            execution=None,
            interrupt=consume_graph_resolution(state),
        )
    )


def complete_graph_run(state: GraphRunState, command: CompleteGraphRun) -> GraphRunState:
    """Commit successful completion of one claimed graph execution."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can complete")
    require_execution_lease(state, command.execution)
    if state.join_progress:
        raise GraphStateTransitionError("a graph cannot complete with unresolved join progress")
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.COMPLETED,
            frontier=(),
            join_progress=(),
            resources=None,
            execution=None,
            interrupt=consume_graph_resolution(state),
        )
    )


def fail_graph_execution(state: GraphRunState, command: FailGraphExecution) -> GraphRunState:
    """Commit failure produced by one claimed graph execution."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph execution can fail")
    require_execution_lease(state, command.execution)
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.FAILED,
            frontier=(),
            failure=command.failure,
            join_progress=(),
            resources=None,
            execution=None,
            interrupt=consume_graph_resolution(state),
        )
    )


__all__: list[str] = []
