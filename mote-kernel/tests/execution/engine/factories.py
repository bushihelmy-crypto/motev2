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
    ExecutionAttemptId,
    ExecutionLeaseSnapshot,
    ExecutionSnapshot,
    ExecutionStatus,
    ExecutionTaskId,
    ExecutionToken,
    GraphRunId,
    InterruptRecord,
    JoinProgress,
    ParentTaskId,
    ParentTaskRef,
)


async def identity(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def lease_snapshot(execution_snapshot: ExecutionSnapshot, *task_ids: str) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        run_id=execution_snapshot.run_id,
        definition_id=execution_snapshot.definition_id,
        definition_version=execution_snapshot.definition_version,
        status=execution_snapshot.status,
        superstep=execution_snapshot.superstep,
        frontier=execution_snapshot.frontier,
        revision=execution_snapshot.revision,
        parent=execution_snapshot.parent,
        join_progress=execution_snapshot.join_progress,
        resources=execution_snapshot.resources,
        execution_sequence=1,
        execution=ExecutionLeaseSnapshot(
            ExecutionToken(1, ExecutionAttemptId("test-attempt")),
            tuple(ExecutionTaskId(task_id) for task_id in sorted(task_ids)),
        ),
        interrupt=execution_snapshot.interrupt,
    )


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
    revision: int = 0,
    frontier: tuple[str, ...] = ("a",),
    run_id: str = "run",
    definition_id: str = "test.graph",
    version: int = 1,
    parent_run_id: str | None = None,
    parent_task_id: str = "parent-task",
    join_progress: tuple[JoinProgress, ...] = (),
    leased_task_ids: tuple[str, ...] = (),
    interrupt: InterruptRecord | None = None,
) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        run_id=GraphRunId(run_id),
        definition_id=GraphDefinitionId(definition_id),
        definition_version=GraphDefinitionVersion(version),
        status=status,
        superstep=superstep,
        frontier=tuple(NodeId(node_id) for node_id in frontier),
        revision=revision,
        parent=(
            ParentTaskRef(GraphRunId(parent_run_id), ParentTaskId(parent_task_id))
            if parent_run_id is not None
            else None
        ),
        join_progress=join_progress,
        execution_sequence=1 if leased_task_ids else 0,
        execution=(
            ExecutionLeaseSnapshot(
                ExecutionToken(1, ExecutionAttemptId("test-attempt")),
                tuple(ExecutionTaskId(task_id) for task_id in leased_task_ids),
            )
            if leased_task_ids
            else None
        ),
        interrupt=interrupt,
    )
