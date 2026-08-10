from mote_kernel.execution.graph import (
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    JoinEdge,
    NodeDefinition,
    NodeId,
    NodeSuccess,
    compile_graph,
)
from mote_kernel.execution.graph.edge import RouteId
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.snapshot import (
    ExecutionSnapshot,
    ExecutionStatus,
    GraphRunId,
    JoinProgress,
    ParentTaskId,
    ParentTaskRef,
)


def identity(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def compiled_graph(*node_ids: str, entries: tuple[str, ...] = ("a",)) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("test.graph"),
            version=GraphDefinitionVersion(1),
            nodes=tuple(NodeDefinition(NodeId(node_id), identity) for node_id in node_ids),
            edges=(),
            entries=tuple(NodeId(node_id) for node_id in entries),
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
            nodes=tuple(NodeDefinition(NodeId(node_id), identity) for node_id in node_ids),
            edges=edges,
            entries=tuple(NodeId(node_id) for node_id in entries),
        )
    )


def direct(source: str, target: str) -> DirectEdge:
    return DirectEdge(NodeId(source), NodeId(target))


def conditional(source: str, route: str, target: str) -> ConditionalEdge:
    return ConditionalEdge(NodeId(source), RouteId(route), NodeId(target))


def join(sources: tuple[str, ...], target: str) -> JoinEdge:
    return JoinEdge(tuple(NodeId(source) for source in sources), NodeId(target))


def snapshot(
    *,
    status: ExecutionStatus = ExecutionStatus.RUNNING,
    superstep: int = 0,
    frontier: tuple[str, ...] = ("a",),
    run_id: str = "run",
    definition_id: str = "test.graph",
    version: int = 1,
    parent_run_id: str | None = None,
    parent_task_id: str = "parent-task",
    join_progress: tuple[JoinProgress, ...] = (),
) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        run_id=GraphRunId(run_id),
        definition_id=GraphDefinitionId(definition_id),
        definition_version=GraphDefinitionVersion(version),
        status=status,
        superstep=superstep,
        frontier=tuple(NodeId(node_id) for node_id in frontier),
        parent=(
            ParentTaskRef(GraphRunId(parent_run_id), ParentTaskId(parent_task_id))
            if parent_run_id is not None
            else None
        ),
        join_progress=join_progress,
    )
