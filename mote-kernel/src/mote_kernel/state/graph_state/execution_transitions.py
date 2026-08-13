"""Pure execution lease and settlement transitions for one graph run."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    FailedGraphNodeOutcome,
    FenceGraphExecution,
    GraphFrontierResolution,
    InterruptedGraphNodeOutcome,
    SettleGraphExecution,
    StartGraphRun,
    SucceededGraphNodeOutcome,
)
from mote_kernel.state.graph_state.frontier_model import (
    FailedGraphNode,
    GraphFrontierNode,
    GraphFrontierState,
    GraphFrontierStatus,
    GraphNodeInterrupt,
    InterruptedGraphNode,
    PendingGraphNode,
    SucceededGraphNode,
    UseStepRequestInput,
    frontier_status,
    pending_node_ids,
)
from mote_kernel.state.graph_state.model import GraphExecutionLease, GraphExecutionToken, GraphRunState, GraphRunStatus
from mote_kernel.state.graph_state.transition_guard import require_execution_lease
from mote_kernel.state.graph_state.validation import (
    GraphStateTransitionError,
    validate_graph_frontier,
    validated_graph_run_state,
)


def start_graph_run(command: StartGraphRun) -> GraphRunState:
    frontier = GraphFrontierState(
        tuple(GraphFrontierNode(node_id, PendingGraphNode(UseStepRequestInput())) for node_id in command.node_ids)
    )
    return validated_graph_run_state(
        GraphRunState(
            run_id=command.run_id,
            definition_id=command.definition_id,
            definition_version=command.definition_version,
            status=GraphRunStatus.RUNNING,
            superstep=0,
            frontier=frontier,
            parent=command.parent,
            resume_input_codec=command.resume_input_codec,
        )
    )


def claim_graph_execution(state: GraphRunState, command: ClaimGraphExecution) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING or state.execution is not None:
        raise GraphStateTransitionError("only a quiescent running graph can claim execution")
    if frontier_status(state.frontier) is not GraphFrontierStatus.EXECUTABLE:
        raise GraphStateTransitionError("only an executable frontier can claim execution")
    if command.node_ids != pending_node_ids(state.frontier):
        raise GraphStateTransitionError("claim must exactly cover all pending nodes")
    token = GraphExecutionToken(state.execution_sequence + 1, command.attempt_id)
    return validated_graph_run_state(
        replace(state, execution_sequence=token.generation, execution=GraphExecutionLease(token, command.node_ids))
    )


def fence_graph_execution(state: GraphRunState, command: FenceGraphExecution) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can fence execution")
    require_execution_lease(state, command.execution)
    return validated_graph_run_state(replace(state, execution=None, resources=None))


def apply_frontier_resolution(
    state: GraphRunState,
    resolution: GraphFrontierResolution,
) -> GraphRunState:
    if isinstance(resolution, CompleteGraphFrontier):
        if state.join_progress:
            raise GraphStateTransitionError("a completed graph cannot discard unresolved join progress")
        return replace(
            state,
            status=GraphRunStatus.COMPLETED,
            frontier=GraphFrontierState(()),
            join_progress=(),
            resources=None,
            execution=None,
        )
    match resolution:
        case AdvanceGraphFrontier(node_ids=node_ids, join_progress=join_progress):
            return replace(
                state,
                superstep=state.superstep + 1,
                frontier=GraphFrontierState(
                    tuple(GraphFrontierNode(node_id, PendingGraphNode(UseStepRequestInput())) for node_id in node_ids)
                ),
                join_progress=join_progress,
                resources=None,
                execution=None,
            )
        case _:
            raise GraphStateTransitionError("frontier resolution has an unsupported variant")


def settle_graph_execution(state: GraphRunState, command: SettleGraphExecution) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph execution can settle")
    lease = require_execution_lease(state, command.execution)
    if any(
        not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            outcome, SucceededGraphNodeOutcome | FailedGraphNodeOutcome | InterruptedGraphNodeOutcome
        )
        for outcome in command.outcomes
    ):
        raise GraphStateTransitionError("settlement outcome has an unsupported variant")
    outcome_ids = tuple(outcome.node_id for outcome in command.outcomes)
    if not outcome_ids or outcome_ids != lease.node_ids:
        raise GraphStateTransitionError("settlement outcomes must exactly cover the active lease")
    by_id = {outcome.node_id: outcome for outcome in command.outcomes}
    settled_nodes: list[GraphFrontierNode] = []
    for node in state.frontier.nodes:
        outcome = by_id.get(node.node_id)
        if outcome is None:
            settled_nodes.append(node)
            continue
        if isinstance(outcome, SucceededGraphNodeOutcome):
            settlement = SucceededGraphNode(outcome.routing)
        elif isinstance(outcome, FailedGraphNodeOutcome):
            settlement = FailedGraphNode(outcome.failure)
        else:
            expected = (
                state.run_id,
                state.superstep,
                node.node_id,
                command.execution.generation,
            )
            identity = outcome.identity
            if (identity.run_id, identity.superstep, identity.node_id, identity.execution_generation) != expected:
                raise GraphStateTransitionError("interrupt outcome identity does not match the active execution")
            if state.resume_input_codec is None:
                raise GraphStateTransitionError("an interrupted node requires a resume input codec")
            settlement = InterruptedGraphNode(GraphNodeInterrupt(identity, outcome.request_payload))
        settled_nodes.append(GraphFrontierNode(node.node_id, settlement))
    frontier = GraphFrontierState(tuple(settled_nodes))
    validate_graph_frontier(state, frontier)
    status = frontier_status(frontier)
    quiescent = replace(state, frontier=frontier, resources=None, execution=None)
    if status is GraphFrontierStatus.SETTLED:
        if command.resolution is None:
            raise GraphStateTransitionError("a settled frontier requires its atomic resolution")
        return validated_graph_run_state(apply_frontier_resolution(quiescent, command.resolution))
    if command.resolution is not None:
        raise GraphStateTransitionError("an unsettled frontier cannot apply routing resolution")
    return validated_graph_run_state(quiescent)


__all__ = ["apply_frontier_resolution"]
