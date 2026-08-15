"""Projection of one typed node completion into one state command."""

from typing import TypeVar

from mote_kernel.execution.engine.routing import validate_routing_contribution
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.task import task_identity
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskResult, TaskSuccess
from mote_kernel.state.graph_state import (
    FailedGraphNodeOutcome,
    GraphRunState,
    InterruptedGraphNodeOutcome,
    PendingGraphNode,
    SettleGraphNode,
    SucceededGraphNodeOutcome,
    derive_graph_node_interrupt_identity,
    frontier_node,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def settle_result(
    graph: CompiledGraph[InputT, OutputT],
    state: GraphRunState,
    result: TaskResult[OutputT],
) -> SettleGraphNode:
    """Validate one result against the acknowledged state and project its command."""

    require_snapshot_matches_graph(graph, state)
    if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        result,
        TaskSuccess | TaskFailure | TaskInterrupt,
    ):
        raise ResultCollectionError("task result has an unsupported variant")
    execution = state.execution
    if execution is None:
        raise ResultCollectionError("settlement requires a committed execution lease")
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
        validate_routing_contribution(graph, task.node_id, result.routing)
        outcome = SucceededGraphNodeOutcome(task.node_id, result.routing)
    elif isinstance(result, TaskFailure):
        outcome = FailedGraphNodeOutcome(task.node_id, result.failure)
    else:
        if graph.resume_input is None:
            raise ResultCollectionError("node interrupt requires a resume input codec")
        outcome = InterruptedGraphNodeOutcome(
            task.node_id,
            derive_graph_node_interrupt_identity(
                state.run_id,
                state.superstep,
                task.node_id,
                execution.token.generation,
            ),
            result.request_payload,
        )
    return SettleGraphNode(state.revision, execution.token, outcome)


__all__ = ["settle_result"]
