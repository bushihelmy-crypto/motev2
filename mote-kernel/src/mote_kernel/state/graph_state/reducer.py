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
    SucceededGraphNodeOutcome,
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
from mote_kernel.state.graph_state.model import GraphEvidenceCommitment, GraphRunState
from mote_kernel.state.graph_state.recovery_transitions import resume_graph_nodes
from mote_kernel.state.graph_state.validation import (
    GraphStateTransitionError,
    validate_graph_run_state,
    validated_graph_run_state,
)


def reduce_graph_run(state: GraphRunState | None, command: GraphRunCommand) -> GraphRunState:
    """Return a new graph-run state without mutating the prior state."""

    if isinstance(command, StartGraphRun):
        if state is not None:
            raise GraphStateTransitionError("an existing graph run cannot be started again")
        return start_graph_run(command)
    if state is None:
        raise GraphStateTransitionError("a graph run must be started before it can transition")
    validate_graph_run_state(state)
    if type(command) not in (
        ClaimGraphExecution,
        FenceGraphExecution,
        SettleGraphNode,
        ResumeGraphNodes,
        AdvanceGraphFrontier,
        CompleteGraphFrontier,
        AbortGraphRun,
    ):
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
    return validated_graph_run_state(replace(updated, revision=state.revision + 1))


def admit_graph_run_confirmation(
    state: GraphRunState | None,
    command: GraphRunCommand,
    confirmed: GraphRunState,
) -> GraphRunState:
    """Admit the exact reducer successor, including one durable evidence refinement."""

    validate_graph_run_state(confirmed)
    expected = reduce_graph_run(state, command)
    if confirmed == expected:
        return confirmed
    if isinstance(command, StartGraphRun):
        commitment = confirmed.graph_input_evidence
        if commitment is None or command.graph_input_evidence is not None:
            raise GraphStateTransitionError("confirmed graph state is not the exact reducer successor")
        bound = replace(command, graph_input_evidence=GraphEvidenceCommitment.admit(commitment))
    elif isinstance(command, SettleGraphNode) and isinstance(command.outcome, SucceededGraphNodeOutcome):
        matches = tuple(
            item
            for item in confirmed.settled_publications
            if item.reference.activation.run_id == confirmed.run_id
            and item.reference.activation.superstep == confirmed.superstep
            and item.reference.activation.node_id == command.outcome.node_id
        )
        if len(matches) != 1 or matches[0].evidence is None or command.publication_evidence is not None:
            raise GraphStateTransitionError("confirmed graph state is not the exact reducer successor")
        bound = replace(command, publication_evidence=GraphEvidenceCommitment.admit(matches[0].evidence))
    else:
        raise GraphStateTransitionError("confirmed graph state is not the exact reducer successor")
    if confirmed != reduce_graph_run(state, bound):
        raise GraphStateTransitionError("confirmed graph state is not the exact reducer successor")
    return confirmed


__all__ = ["admit_graph_run_confirmation", "reduce_graph_run"]
