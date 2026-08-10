"""Deterministic task invocation without node-level retry."""

from typing import TypeVar

from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import NodeExecutionContractError, ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph, NestedGraphNodeDefinition, NodeFailure, NodeSuccess
from mote_kernel.execution.result import NestedTaskFailure, NestedTaskResult, TaskFailure, TaskResult, TaskSuccess

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def execute_tasks(
    graph: CompiledGraph[InputT, OutputT],
    tasks: tuple[GraphTask, ...],
    node_input: InputT,
    nested_results: tuple[NestedTaskResult[OutputT], ...] = (),
) -> tuple[TaskResult[OutputT], ...]:
    """Invoke each planned task once in canonical order."""

    nested_by_task = {result.task_id: result for result in nested_results}
    nested_task_ids = {
        task.task_id for task in tasks if isinstance(graph.nodes[task.node_id], NestedGraphNodeDefinition)
    }
    if set(nested_by_task) != nested_task_ids:
        raise ResultCollectionError("nested task results must exactly cover planned nested graph tasks")
    results: list[TaskResult[OutputT]] = []
    for task in tasks:
        definition = graph.nodes[task.node_id]
        if isinstance(definition, NestedGraphNodeDefinition):
            nested = nested_by_task[task.task_id]
            if isinstance(nested, NestedTaskFailure):
                results.append(TaskFailure(task, nested.failure))
            else:
                results.append(TaskSuccess(task, nested.output, nested.routing))
            continue
        match definition.node(node_input):
            case NodeSuccess(output=output, routing=routing):
                results.append(TaskSuccess(task, output, routing))
            case NodeFailure(failure=failure):
                results.append(TaskFailure(task, failure))
            case invalid:
                raise NodeExecutionContractError(f"node returned an invalid outcome: {type(invalid).__name__}")
    return tuple(results)


__all__: list[str] = []
