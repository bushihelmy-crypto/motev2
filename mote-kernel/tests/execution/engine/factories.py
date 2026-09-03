from dataclasses import replace
from typing import TypeVar

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import NodeOutputFrame, _frame_value, _make_node_output_frame
from mote_kernel.execution.result import TaskSuccess
from mote_kernel.state.graph_state import (
    ActivationReference,
    FailedGraphNode,
    GraphAbort,
    GraphAbortReason,
    GraphActivationCause,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphJoinProgress,
    GraphNodeId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    PendingGraphNode,
    RoutedActivationCause,
    StartActivationCause,
    UseStepRequestInput,
)

ValueT = TypeVar("ValueT")


async def identity(values: Graph.Values[str]) -> Graph.Values[str]:
    return values


def node_output(value: ValueT) -> NodeOutputFrame[ValueT]:
    return _make_node_output_frame(
        Graph.values(value=value),
        normalize_output_declarations({"value": type(value)}),
    )


def output_value(frame: NodeOutputFrame[ValueT]) -> ValueT:
    return _frame_value(frame, "value")


def task_success(task: GraphTask, value: ValueT, route: str | None = None) -> TaskSuccess[ValueT]:
    return TaskSuccess(task, node_output(value), route)


def callable_node(node_id: str) -> CallableNodeDefinition[str]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        identity,
        normalize_input_bindings({"value": Graph.graph_input("value", str)}),
        normalize_output_declarations({"value": str}),
    )


def _compile(
    node_ids: tuple[str, ...],
    edges: tuple[Edge, ...],
    entries: tuple[str, ...],
) -> CompiledGraph[str]:
    incoming = {edge.target for edge in edges if edge.target != Graph.END}
    explicit_entries = tuple(GraphNodeId(node_id) for node_id in entries if GraphNodeId(node_id) in incoming)
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("test.graph"),
            version=GraphDefinitionVersion(1),
            nodes=tuple(callable_node(node_id) for node_id in node_ids),
            edges=edges,
            entries=explicit_entries,
            outputs=normalize_graph_output_declarations({}),
        )
    )


def compiled_graph(
    *node_ids: str,
    entries: tuple[str, ...] = ("a",),
    edges: tuple[Edge, ...] = (),
) -> CompiledGraph[str]:
    return _compile(node_ids, edges, entries)


def topology(
    *node_ids: str,
    edges: tuple[Edge, ...] = (),
    entries: tuple[str, ...] = ("a",),
) -> CompiledGraph[str]:
    return _compile(node_ids, edges, entries)


def direct(source: str, target: str) -> DirectEdge:
    return DirectEdge(GraphNodeId(source), GraphNodeId(target))


def conditional(source: str, route: str, target: str) -> ConditionalEdge:
    return ConditionalEdge(GraphNodeId(source), GraphRouteId(route), GraphNodeId(target))


def join(sources: tuple[str, ...], target: str) -> JoinEdge:
    return JoinEdge(tuple(GraphNodeId(source) for source in sources), GraphNodeId(target))


def running_state(
    *,
    superstep: int = 0,
    revision: int = 0,
    frontier: tuple[str, ...] = ("a",),
    run_id: str = "run",
    definition_id: str = "test.graph",
    version: int = 1,
    join_progress: tuple[GraphJoinProgress, ...] = (),
) -> GraphRunState:
    canonical_run_id = GraphRunId(run_id)

    settled_activations = tuple(
        sorted(
            {reference for progress in join_progress for reference in progress.arrived}
            | {
                ActivationReference(GraphActivationIdentity(canonical_run_id, superstep - 1, GraphNodeId(node_id)))
                for node_id in frontier
                if superstep > 0
            },
            key=ActivationReference.canonical_key,
        )
    )

    def cause(node_id: GraphNodeId) -> GraphActivationCause:
        if superstep == 0:
            return StartActivationCause()
        return RoutedActivationCause(
            (ActivationReference(GraphActivationIdentity(canonical_run_id, superstep - 1, node_id)),)
        )

    return GraphRunState(
        run_id=canonical_run_id,
        definition_id=GraphDefinitionId(definition_id),
        definition_version=GraphDefinitionVersion(version),
        status=GraphRunStatus.RUNNING,
        superstep=superstep,
        frontier=GraphFrontierState(
            tuple(
                GraphFrontierNode(
                    GraphNodeId(node_id),
                    PendingGraphNode(UseStepRequestInput()),
                    cause(GraphNodeId(node_id)),
                )
                for node_id in sorted(frontier)
            )
        ),
        join_progress=join_progress,
        settled_activations=settled_activations,
        revision=revision,
    )


def leased_state(state: GraphRunState) -> GraphRunState:
    token = GraphExecutionToken(state.execution_sequence + 1, GraphExecutionAttemptId("test-attempt"))
    return replace(
        state,
        execution_sequence=token.generation,
        execution=GraphExecutionLease(token),
    )


def terminal_state(status: GraphRunStatus) -> GraphRunState:
    state = running_state()
    if status is GraphRunStatus.COMPLETED:
        return replace(state, status=status, frontier=GraphFrontierState(()))
    if status is GraphRunStatus.FAILED:
        return replace(
            state,
            status=status,
            frontier=GraphFrontierState(
                (GraphFrontierNode(GraphNodeId("a"), FailedGraphNode(GraphFailure("failed")), StartActivationCause()),)
            ),
        )
    if status is GraphRunStatus.ABORTED:
        return replace(state, status=status, abort=GraphAbort(GraphAbortReason("aborted")))
    return state
