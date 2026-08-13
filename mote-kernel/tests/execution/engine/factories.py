from dataclasses import replace

from mote_kernel.execution.graph import (
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
    JoinEdge,
    NodeDefinition,
    NodeSuccess,
    compile_graph,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.state.graph_state import (
    GraphAbort,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFrontierNode,
    GraphFrontierState,
    GraphJoinProgress,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    PendingGraphNode,
    UseStepRequestInput,
    pending_node_ids,
)


async def identity(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def compiled_graph(*node_ids: str, entries: tuple[str, ...] = ("a",)) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("test.graph"),
            version=GraphDefinitionVersion(1),
            nodes=tuple(NodeDefinition(GraphNodeId(node_id), identity) for node_id in node_ids),
            edges=(),
            entries=tuple(GraphNodeId(node_id) for node_id in entries),
        )
    )


def topology(
    *node_ids: str,
    edges: tuple[DirectEdge | ConditionalEdge | JoinEdge, ...] = (),
    entries: tuple[str, ...] = ("a",),
) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("test.graph"),
            version=GraphDefinitionVersion(1),
            nodes=tuple(NodeDefinition(GraphNodeId(node_id), identity) for node_id in node_ids),
            edges=edges,
            entries=tuple(GraphNodeId(node_id) for node_id in entries),
        )
    )


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
    return GraphRunState(
        run_id=GraphRunId(run_id),
        definition_id=GraphDefinitionId(definition_id),
        definition_version=GraphDefinitionVersion(version),
        status=GraphRunStatus.RUNNING,
        superstep=superstep,
        frontier=GraphFrontierState(
            tuple(
                GraphFrontierNode(GraphNodeId(node_id), PendingGraphNode(UseStepRequestInput()))
                for node_id in sorted(frontier)
            )
        ),
        join_progress=join_progress,
        revision=revision,
    )


def leased_state(state: GraphRunState) -> GraphRunState:
    node_ids = pending_node_ids(state.frontier)
    token = GraphExecutionToken(state.execution_sequence + 1, GraphExecutionAttemptId("test-attempt"))
    return replace(
        state,
        execution_sequence=token.generation,
        execution=GraphExecutionLease(token, node_ids),
    )


def terminal_state(status: GraphRunStatus) -> GraphRunState:
    state = running_state()
    if status is GraphRunStatus.COMPLETED:
        return replace(state, status=status, frontier=GraphFrontierState(()))
    if status is GraphRunStatus.ABORTED:
        return replace(state, status=status, abort=GraphAbort(GraphAbortReason("aborted")))
    return state
