"""Pure claim, node-settlement, fence, and frontier-resolution transitions."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    FailedGraphNodeOutcome,
    FenceGraphExecution,
    InterruptedGraphNodeOutcome,
    SettleGraphNode,
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
    frontier_node,
    frontier_status,
    pending_node_ids,
)
from mote_kernel.state.graph_state.model import GraphExecutionLease, GraphExecutionToken, GraphRunState, GraphRunStatus
from mote_kernel.state.graph_state.resource_command import ReleaseResources
from mote_kernel.state.graph_state.resource_model import ResourceSnapshot
from mote_kernel.state.graph_state.resource_reducer import (
    ResourceTransitionError,
    reduce_resources,
    validate_resource_snapshot,
)
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


def _validate_claim_resources(state: GraphRunState, resources: ResourceSnapshot | None) -> None:
    if resources is None:
        return
    try:
        validate_resource_snapshot(resources)
    except ResourceTransitionError as error:
        raise GraphStateTransitionError("claim resource snapshot is invalid") from error
    if not resources.acquisitions:
        raise GraphStateTransitionError("an active claim cannot persist an empty resource snapshot")
    pending = frozenset(pending_node_ids(state.frontier))
    participants = frozenset(item.node_id for item in resources.acquisitions)
    if not participants <= pending:
        raise GraphStateTransitionError("claim resource participant is outside current pending nodes")


def claim_graph_execution(state: GraphRunState, command: ClaimGraphExecution) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING or state.execution is not None or state.resources is not None:
        raise GraphStateTransitionError("only a quiescent running graph can claim execution")
    if frontier_status(state.frontier) is not GraphFrontierStatus.EXECUTABLE:
        raise GraphStateTransitionError("only an executable frontier can claim execution")
    if not pending_node_ids(state.frontier):
        raise GraphStateTransitionError("an execution claim requires pending nodes")
    _validate_claim_resources(state, command.resources)
    token = GraphExecutionToken(state.execution_sequence + 1, command.attempt_id)
    return validated_graph_run_state(
        replace(
            state,
            execution_sequence=token.generation,
            execution=GraphExecutionLease(token),
            resources=command.resources,
        )
    )


def fence_graph_execution(state: GraphRunState, command: FenceGraphExecution) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can fence execution")
    require_execution_lease(state, command.execution)
    return validated_graph_run_state(replace(state, execution=None, resources=None))


def _resolution_base(state: GraphRunState) -> None:
    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("frontier resolution requires a running graph")
    if frontier_status(state.frontier) is not GraphFrontierStatus.SETTLED:
        raise GraphStateTransitionError("frontier resolution requires a settled frontier")
    if state.execution is not None or state.resources is not None:
        raise GraphStateTransitionError("a settled frontier must be quiescent")


def advance_graph_frontier(state: GraphRunState, command: AdvanceGraphFrontier) -> GraphRunState:
    _resolution_base(state)
    if not command.node_ids or command.node_ids != tuple(sorted(set(command.node_ids))):
        raise GraphStateTransitionError("next frontier nodes must be non-empty and canonical")
    return validated_graph_run_state(
        replace(
            state,
            superstep=state.superstep + 1,
            frontier=GraphFrontierState(
                tuple(
                    GraphFrontierNode(node_id, PendingGraphNode(UseStepRequestInput())) for node_id in command.node_ids
                )
            ),
            join_progress=command.join_progress,
            resources=None,
            execution=None,
        )
    )


def complete_graph_frontier(state: GraphRunState, command: CompleteGraphFrontier) -> GraphRunState:
    _resolution_base(state)
    if state.join_progress:
        raise GraphStateTransitionError("a completed graph cannot discard unresolved join progress")
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.COMPLETED,
            frontier=GraphFrontierState(()),
            join_progress=(),
            resources=None,
            execution=None,
        )
    )


def settle_graph_node(state: GraphRunState, command: SettleGraphNode) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph execution can settle a node")
    require_execution_lease(state, command.execution)
    outcome = command.outcome
    if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        outcome,
        SucceededGraphNodeOutcome | FailedGraphNodeOutcome | InterruptedGraphNodeOutcome,
    ):
        raise GraphStateTransitionError("settlement outcome has an unsupported variant")
    node_id = outcome.node_id
    current = frontier_node(state.frontier, node_id)
    if current is None or not isinstance(current.settlement, PendingGraphNode):
        raise GraphStateTransitionError("node settlement requires a current pending node")

    if isinstance(outcome, SucceededGraphNodeOutcome):
        settlement = SucceededGraphNode(outcome.routing)
    elif isinstance(outcome, FailedGraphNodeOutcome):
        settlement = FailedGraphNode(outcome.failure)
    else:
        expected = (state.run_id, state.superstep, node_id, command.execution.generation)
        identity = outcome.identity
        if (identity.run_id, identity.superstep, identity.node_id, identity.execution_generation) != expected:
            raise GraphStateTransitionError("interrupt outcome identity does not match the active execution")
        if state.resume_input_codec is None:
            raise GraphStateTransitionError("an interrupted node requires a resume input codec")
        settlement = InterruptedGraphNode(GraphNodeInterrupt(identity, outcome.request_payload))

    frontier = GraphFrontierState(
        tuple(
            GraphFrontierNode(node.node_id, settlement if node.node_id == node_id else node.settlement)
            for node in state.frontier.nodes
        )
    )
    validate_graph_frontier(state, frontier)

    resources = state.resources
    if resources is not None and any(item.node_id == node_id for item in resources.acquisitions):
        try:
            resources = reduce_resources(resources, ReleaseResources(node_id))
        except ResourceTransitionError as error:
            raise GraphStateTransitionError("completed node cannot release its resource acquisition") from error
        if not resources.acquisitions:
            resources = None

    if pending_node_ids(frontier):
        execution = state.execution
    else:
        execution = None
        resources = None
    return validated_graph_run_state(replace(state, frontier=frontier, execution=execution, resources=resources))


__all__ = [
    "advance_graph_frontier",
    "claim_graph_execution",
    "complete_graph_frontier",
    "fence_graph_execution",
    "settle_graph_node",
    "start_graph_run",
]
