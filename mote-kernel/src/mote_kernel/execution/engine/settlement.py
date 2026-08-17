"""Projection of one typed node completion into one state command."""

from typing import TypeVar

from mote_kernel.execution.engine.routing import validate_routing_contribution
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.task import task_identity
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskResult, TaskSuccess
from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    FailedGraphNodeOutcome,
    GraphExecutionToken,
    GraphFailure,
    GraphInterruptPayload,
    GraphNodeId,
    GraphRouteId,
    GraphRunState,
    InterruptedGraphNodeOutcome,
    PendingGraphNode,
    SelectGraphRoute,
    SettleGraphNode,
    SucceededGraphNodeOutcome,
    derive_graph_node_interrupt_identity,
    frontier_node,
)

GraphValueT = TypeVar("GraphValueT")


def require_settlement_execution_token(state: GraphRunState) -> GraphExecutionToken:
    execution = state.execution
    if execution is None:
        raise ResultCollectionError("settlement requires a committed execution lease")
    return execution.token


def project_success_settlement(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    node_id: GraphNodeId,
    route: str | None,
) -> SettleGraphNode:
    routing = ContinueGraphRouting() if route is None else SelectGraphRoute(GraphRouteId(route))
    validate_routing_contribution(graph, node_id, routing)
    return SettleGraphNode(
        state.revision,
        require_settlement_execution_token(state),
        SucceededGraphNodeOutcome(node_id, routing),
    )


def project_failure_settlement(
    state: GraphRunState,
    node_id: GraphNodeId,
    failure: str,
) -> SettleGraphNode:
    return SettleGraphNode(
        state.revision,
        require_settlement_execution_token(state),
        FailedGraphNodeOutcome(node_id, GraphFailure(failure)),
    )


def project_interrupt_settlement(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    node_id: GraphNodeId,
    request_payload: bytes,
) -> SettleGraphNode:
    if graph.resume_input is None:
        raise ResultCollectionError("node interrupt requires a resume input codec")
    token = require_settlement_execution_token(state)
    return SettleGraphNode(
        state.revision,
        token,
        InterruptedGraphNodeOutcome(
            node_id,
            derive_graph_node_interrupt_identity(
                state.run_id,
                state.superstep,
                node_id,
                token.generation,
            ),
            GraphInterruptPayload(request_payload),
        ),
    )


def settle_result(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    result: TaskResult[GraphValueT],
) -> SettleGraphNode:
    """Validate one result against the acknowledged state and project its command."""

    if type(result) not in (TaskSuccess, TaskFailure, TaskInterrupt):
        raise ResultCollectionError("task result has an unsupported variant")
    require_snapshot_matches_graph(graph, state)
    require_settlement_execution_token(state)
    task = result.task
    if (
        task.run_id != state.run_id
        or task.superstep != state.superstep
        or task.task_id != task_identity(task.run_id, task.superstep, task.node_id)
    ):
        raise ResultCollectionError("task result has invalid coordinates")
    node = frontier_node(state.frontier, task.node_id)
    if node is None or not isinstance(node.settlement, PendingGraphNode):
        raise ResultCollectionError("task result does not reference a pending node")
    if isinstance(result, TaskSuccess):
        return project_success_settlement(graph, state, task.node_id, result.route)
    if isinstance(result, TaskFailure):
        return project_failure_settlement(state, task.node_id, result.failure)
    return project_interrupt_settlement(graph, state, task.node_id, result.request_payload)


__all__ = [
    "project_failure_settlement",
    "project_interrupt_settlement",
    "project_success_settlement",
    "require_settlement_execution_token",
    "settle_result",
]
