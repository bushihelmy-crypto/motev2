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
from mote_kernel.state.graph_state.transition_guard import (
    require_execution_lease,
    require_interrupt_generation,
)
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
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("execution claim was based on a stale superstep")
    if command.expected_execution_sequence != state.execution_sequence:
        raise GraphStateTransitionError("execution claim was based on a stale execution sequence")
    if command.expected_parallel != state.parallel:
        raise GraphStateTransitionError("execution claim was based on a stale parallel snapshot")
    require_interrupt_generation(state, command.expected_interrupt_generation)
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
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("execution fence was based on a stale superstep")
    require_execution_lease(state, command.execution)
    return validated_graph_run_state(replace(state, execution=None))


def advance_graph_run(state: GraphRunState, command: AdvanceGraphRun) -> GraphRunState:
    """Commit a claimed superstep's next recoverable position."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can advance")
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("advance command was based on a stale superstep")
    require_execution_lease(state, command.execution)
    require_interrupt_generation(state, command.expected_interrupt_generation)
    return validated_graph_run_state(
        replace(
            state,
            superstep=state.superstep + 1,
            frontier=tuple(sorted(command.frontier)),
            join_progress=tuple(sorted(command.join_progress, key=_join_sort_key)),
            parallel=None,
            execution=None,
            interrupt=consume_graph_resolution(state),
        )
    )


def complete_graph_run(state: GraphRunState, command: CompleteGraphRun) -> GraphRunState:
    """Commit successful completion of one claimed graph execution."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can complete")
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("complete command was based on a stale superstep")
    require_execution_lease(state, command.execution)
    require_interrupt_generation(state, command.expected_interrupt_generation)
    if state.join_progress:
        raise GraphStateTransitionError("a graph cannot complete with unresolved join progress")
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.COMPLETED,
            frontier=(),
            join_progress=(),
            parallel=None,
            execution=None,
            interrupt=consume_graph_resolution(state),
        )
    )


def fail_graph_execution(state: GraphRunState, command: FailGraphExecution) -> GraphRunState:
    """Commit failure produced by one claimed graph execution."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph execution can fail")
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("execution failure was based on a stale superstep")
    require_execution_lease(state, command.execution)
    require_interrupt_generation(state, command.expected_interrupt_generation)
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.FAILED,
            frontier=(),
            failure=command.failure,
            join_progress=(),
            parallel=None,
            execution=None,
            interrupt=consume_graph_resolution(state),
        )
    )


__all__: list[str] = []
