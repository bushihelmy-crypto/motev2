"""Projection of complete typed outcomes into one settlement command."""

from typing import TypeVar

from mote_kernel.execution.engine.collector import collect_results
from mote_kernel.execution.engine.routing import resolve_routing, validate_routing_contribution
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.result import TaskResult
from mote_kernel.state.graph_state import (
    FailedGraphNodeOutcome,
    GraphNodeOutcome,
    GraphRunState,
    InterruptedGraphNodeOutcome,
    SettleGraphExecution,
    SucceededGraphNodeOutcome,
    derive_graph_node_interrupt_identity,
    failed_node_ids,
    interrupted_node_ids,
    routing_contributions,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def settle_tasks(
    graph: CompiledGraph[InputT, OutputT],
    state: GraphRunState,
    planned_tasks: tuple[GraphTask, ...],
    results: tuple[TaskResult[OutputT], ...],
) -> SettleGraphExecution:
    require_snapshot_matches_graph(graph, state)
    execution = state.execution
    if execution is None:
        raise ResultCollectionError("settlement requires a committed execution lease")
    collected = collect_results(state, planned_tasks, results)
    if collected.interrupts and graph.resume_input is None:
        raise ResultCollectionError("node interrupt requires a resume input codec")
    for success in collected.successes:
        validate_routing_contribution(graph, success.task.node_id, success.routing)
    successes = {result.task.node_id: result for result in collected.successes}
    failures = {result.task.node_id: result for result in collected.failures}
    interrupts = {result.task.node_id: result for result in collected.interrupts}
    outcomes: list[GraphNodeOutcome] = []
    for node_id in execution.node_ids:
        if node_id in successes:
            result = successes[node_id]
            outcomes.append(SucceededGraphNodeOutcome(node_id, result.routing))
        elif node_id in failures:
            outcomes.append(FailedGraphNodeOutcome(node_id, failures[node_id].failure))
        else:
            result = interrupts[node_id]
            outcomes.append(
                InterruptedGraphNodeOutcome(
                    node_id,
                    derive_graph_node_interrupt_identity(
                        state.run_id,
                        state.superstep,
                        node_id,
                        execution.token.generation,
                    ),
                    result.request_payload,
                )
            )
    if failures or interrupts or failed_node_ids(state.frontier) or interrupted_node_ids(state.frontier):
        resolution = None
    else:
        retained = dict(routing_contributions(state.frontier))
        retained.update({success.task.node_id: success.routing for success in collected.successes})
        resolution = resolve_routing(graph, tuple(sorted(retained.items())), state.join_progress)
    return SettleGraphExecution(state.revision, execution.token, tuple(outcomes), resolution)


__all__ = ["settle_tasks"]
