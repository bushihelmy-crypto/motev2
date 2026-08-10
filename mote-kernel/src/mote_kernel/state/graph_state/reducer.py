"""Pure graph-run state transitions."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    AdvanceGraphRun,
    CompleteGraphRun,
    GraphRunCommand,
    ResumeGraphRun,
    StartGraphRun,
    SuspendGraphRun,
)
from mote_kernel.state.graph_state.model import GraphJoinProgress, GraphRunState, GraphRunStatus


class GraphStateTransitionError(ValueError):
    """A graph command is invalid for the current committed state."""


def _require_identity(value: str, field: str) -> None:
    if not value or value != value.strip():
        raise GraphStateTransitionError(f"{field} must be non-empty and trimmed")


def _validate_frontier(frontier: tuple[str, ...], *, required: bool = True) -> None:
    if required and not frontier:
        raise GraphStateTransitionError("a running graph requires a non-empty frontier")
    if len(frontier) != len(set(frontier)):
        raise GraphStateTransitionError("a graph frontier cannot contain duplicate nodes")
    for node_id in frontier:
        _require_identity(node_id, "frontier node identity")


def _validate_join_progress(progress: tuple[GraphJoinProgress, ...]) -> None:
    seen: set[tuple[tuple[str, ...], str]] = set()
    for join in progress:
        if not join.sources or len(join.sources) != len(set(join.sources)):
            raise GraphStateTransitionError("join progress requires distinct sources")
        for source in join.sources:
            _require_identity(source, "join source identity")
        _require_identity(join.target, "join target identity")
        if join.target in join.sources:
            raise GraphStateTransitionError("join target cannot be a source")
        if not join.arrived or not join.arrived < frozenset(join.sources):
            raise GraphStateTransitionError("join progress must contain partial arrivals")
        key = (join.sources, join.target)
        if key in seen:
            raise GraphStateTransitionError("graph state repeats join progress")
        seen.add(key)


def _join_sort_key(progress: GraphJoinProgress) -> tuple[tuple[str, ...], str]:
    return (progress.sources, progress.target)


def _validate_state(state: GraphRunState) -> None:
    _require_identity(state.run_id, "graph run identity")
    _require_identity(state.definition_id, "graph definition identity")
    if state.definition_version < 1:
        raise GraphStateTransitionError("graph definition version must be positive")
    if state.superstep < 0:
        raise GraphStateTransitionError("graph superstep cannot be negative")
    if state.parent is not None:
        _require_identity(state.parent.run_id, "parent graph run identity")
        _require_identity(state.parent.task_id, "parent graph task identity")
        if state.parent.run_id == state.run_id:
            raise GraphStateTransitionError("a graph run cannot be its own parent")
    _validate_frontier(state.frontier, required=state.status in {GraphRunStatus.RUNNING, GraphRunStatus.SUSPENDED})
    _validate_join_progress(state.join_progress)
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED} and state.frontier:
        raise GraphStateTransitionError("a terminal graph cannot retain a frontier")
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED} and state.join_progress:
        raise GraphStateTransitionError("a terminal graph cannot retain join progress")
    if state.status is GraphRunStatus.FAILED:
        if state.failure is None:
            raise GraphStateTransitionError("a failed graph requires a failure")
        _require_identity(state.failure, "graph failure")
    elif state.failure is not None:
        raise GraphStateTransitionError("only a failed graph may retain a failure")


def _start(command: StartGraphRun) -> GraphRunState:
    _require_identity(command.run_id, "graph run identity")
    _require_identity(command.definition_id, "graph definition identity")
    if command.definition_version < 1:
        raise GraphStateTransitionError("graph definition version must be positive")
    _validate_frontier(command.frontier)
    if command.parent is not None:
        _require_identity(command.parent.run_id, "parent graph run identity")
        _require_identity(command.parent.task_id, "parent graph task identity")
        if command.parent.run_id == command.run_id:
            raise GraphStateTransitionError("a graph run cannot be its own parent")
    return GraphRunState(
        run_id=command.run_id,
        definition_id=command.definition_id,
        definition_version=command.definition_version,
        status=GraphRunStatus.RUNNING,
        superstep=0,
        frontier=tuple(sorted(command.frontier)),
        parent=command.parent,
    )


def reduce_graph_run(state: GraphRunState | None, command: GraphRunCommand) -> GraphRunState:
    """Return a new graph-run state without mutating the prior state."""

    if isinstance(command, StartGraphRun):
        if state is not None:
            raise GraphStateTransitionError("an existing graph run cannot be started again")
        return _start(command)
    if state is None:
        raise GraphStateTransitionError("a graph run must be started before it can transition")
    _validate_state(state)
    if isinstance(command, AdvanceGraphRun):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can advance")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("advance command was based on a stale superstep")
        _validate_frontier(command.frontier)
        _validate_join_progress(command.join_progress)
        return replace(
            state,
            superstep=state.superstep + 1,
            frontier=tuple(sorted(command.frontier)),
            join_progress=tuple(sorted(command.join_progress, key=_join_sort_key)),
        )
    if isinstance(command, SuspendGraphRun):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can suspend")
        return replace(state, status=GraphRunStatus.SUSPENDED)
    if isinstance(command, ResumeGraphRun):
        if state.status is not GraphRunStatus.SUSPENDED:
            raise GraphStateTransitionError("only a suspended graph can resume")
        return replace(state, status=GraphRunStatus.RUNNING)
    if isinstance(command, CompleteGraphRun):
        if state.status is not GraphRunStatus.RUNNING:
            raise GraphStateTransitionError("only a running graph can complete")
        if command.expected_superstep != state.superstep:
            raise GraphStateTransitionError("complete command was based on a stale superstep")
        return replace(state, status=GraphRunStatus.COMPLETED, frontier=(), join_progress=())
    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED}:
        raise GraphStateTransitionError("a terminal graph cannot fail again")
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("fail command was based on a stale superstep")
    _require_identity(command.failure, "graph failure")
    return replace(state, status=GraphRunStatus.FAILED, frontier=(), failure=command.failure, join_progress=())
