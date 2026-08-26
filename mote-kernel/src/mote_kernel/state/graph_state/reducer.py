"""Single dispatch entry point for pure graph-run state transitions."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    FenceGraphExecution,
    GraphRunCommand,
    ResumeGraphNodes,
    SettleGraphNode,
    StartGraphRun,
)
from mote_kernel.state.graph_state.execution_transitions import (
    advance_graph_frontier,
    claim_graph_execution,
    complete_graph_frontier,
    fence_graph_execution,
    settle_graph_node,
    start_graph_run,
)
from mote_kernel.state.graph_state.lifecycle_transitions import abort_graph_run
from mote_kernel.state.graph_state.model import GraphRunState
from mote_kernel.state.graph_state.recovery_transitions import resume_graph_nodes
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
    match command:
        case (
            ClaimGraphExecution()
            | FenceGraphExecution()
            | SettleGraphNode()
            | ResumeGraphNodes()
            | AdvanceGraphFrontier()
            | CompleteGraphFrontier()
            | AbortGraphRun()
        ):
            pass
        case _:
            raise GraphStateTransitionError("graph command has an unsupported variant")
    if command.expected_revision != state.revision:
        raise GraphStateTransitionError("graph command was based on a stale revision")
    if isinstance(command, ClaimGraphExecution):
        updated = claim_graph_execution(state, command)
    elif isinstance(command, FenceGraphExecution):
        updated = fence_graph_execution(state, command)
    elif isinstance(command, SettleGraphNode):
        updated = settle_graph_node(state, command)
    elif isinstance(command, ResumeGraphNodes):
        updated = resume_graph_nodes(state, command)
    elif isinstance(command, AdvanceGraphFrontier):
        updated = advance_graph_frontier(state, command)
    elif isinstance(command, CompleteGraphFrontier):
        updated = complete_graph_frontier(state, command)
    else:
        updated = abort_graph_run(state, command)
    return replace(updated, revision=state.revision + 1)


__all__ = ["reduce_graph_run"]
