"""Single dispatch entry point for pure graph-run state transitions."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    AdvanceGraphRun,
    ClaimGraphExecution,
    CompleteGraphRun,
    FailGraphExecution,
    FenceGraphExecution,
    GraphRunCommand,
    RequestGraphRunInterrupt,
    ResolveGraphRunInterrupt,
    StartGraphRun,
    UpdateGraphResources,
)
from mote_kernel.state.graph_state.execution_transitions import (
    advance_graph_run,
    claim_graph_execution,
    complete_graph_run,
    fail_graph_execution,
    fence_graph_execution,
    start_graph_run,
)
from mote_kernel.state.graph_state.interrupt_transitions import (
    abort_graph_run,
    request_graph_interrupt,
    resolve_graph_interrupt,
)
from mote_kernel.state.graph_state.model import GraphRunState
from mote_kernel.state.graph_state.resource_transitions import update_graph_resources
from mote_kernel.state.graph_state.validation import GraphStateTransitionError, validate_graph_run_state


def reduce_graph_run(state: GraphRunState | None, command: GraphRunCommand) -> GraphRunState:
    """Return a new graph-run state without mutating the prior state."""

    if isinstance(command, StartGraphRun):
        if state is not None:
            raise GraphStateTransitionError("an existing graph run cannot be started again")
        return start_graph_run(command)
    if state is None:
        raise GraphStateTransitionError("a graph run must be started before it can transition")
    validate_graph_run_state(state)
    if command.expected_revision != state.revision:
        raise GraphStateTransitionError("graph command was based on a stale revision")
    if isinstance(command, ClaimGraphExecution):
        updated = claim_graph_execution(state, command)
    elif isinstance(command, FenceGraphExecution):
        updated = fence_graph_execution(state, command)
    elif isinstance(command, RequestGraphRunInterrupt):
        updated = request_graph_interrupt(state, command)
    elif isinstance(command, ResolveGraphRunInterrupt):
        updated = resolve_graph_interrupt(state, command)
    elif isinstance(command, UpdateGraphResources):
        updated = update_graph_resources(state, command)
    elif isinstance(command, AdvanceGraphRun):
        updated = advance_graph_run(state, command)
    elif isinstance(command, CompleteGraphRun):
        updated = complete_graph_run(state, command)
    elif isinstance(command, FailGraphExecution):
        updated = fail_graph_execution(state, command)
    else:
        updated = abort_graph_run(state, command)
    return replace(updated, revision=state.revision + 1)


__all__ = ["reduce_graph_run"]
