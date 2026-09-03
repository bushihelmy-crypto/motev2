"""Pure selective recovery transitions for graph frontier nodes."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    ResumeGraphNodes,
    ResumeInterruptedNode,
)
from mote_kernel.state.graph_state.frontier_model import (
    GraphFrontierNode,
    GraphFrontierState,
    InterruptedGraphNode,
    PendingGraphNode,
)
from mote_kernel.state.graph_state.identity import graph_interrupt_id
from mote_kernel.state.graph_state.model import GraphRunState, GraphRunStatus
from mote_kernel.state.graph_state.validation import (
    GraphStateTransitionError,
    validate_graph_frontier,
    validated_graph_run_state,
)


def resume_graph_nodes(state: GraphRunState, command: ResumeGraphNodes) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING or state.execution is not None or state.resources is not None:
        raise GraphStateTransitionError("node resume requires one quiescent running graph")
    for action in command.actions:
        if type(action) is not ResumeInterruptedNode:
            raise GraphStateTransitionError("resume action has an unsupported variant")
    action_ids = tuple(action.node_id for action in command.actions)
    if not action_ids or action_ids != tuple(sorted(set(action_ids))):
        raise GraphStateTransitionError("resume actions must be non-empty, distinct, and canonical")
    actions = {action.node_id: action for action in command.actions}
    updated: list[GraphFrontierNode] = []
    for node in state.frontier.nodes:
        action = actions.pop(node.node_id, None)
        if action is None:
            updated.append(node)
            continue
        settlement = node.settlement
        if isinstance(settlement, InterruptedGraphNode):
            identity = settlement.interrupt.identity
            if action.interrupt_id != graph_interrupt_id(
                identity.run_id,
                identity.superstep,
                identity.node_id,
                identity.execution_generation,
            ):
                raise GraphStateTransitionError("interrupt resume ID does not match the current node interrupt")
            next_settlement = PendingGraphNode(action.input)
        else:
            raise GraphStateTransitionError("resume action does not match its current node settlement")
        updated.append(GraphFrontierNode(node.node_id, next_settlement, node.cause))
    if actions:
        raise GraphStateTransitionError("resume action references an unknown frontier node")
    frontier = GraphFrontierState(tuple(updated))
    validate_graph_frontier(state, frontier)
    return validated_graph_run_state(replace(state, frontier=frontier))


__all__: list[str] = []
