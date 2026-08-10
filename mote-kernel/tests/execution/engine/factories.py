from mote_kernel.execution.graph import (
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    NodeDefinition,
    NodeId,
    compile_graph,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.snapshot import (
    ExecutionSnapshot,
    ExecutionStatus,
    GraphRunId,
    ParentTaskId,
    ParentTaskRef,
)


def identity(node_input: str) -> str:
    return node_input


def compiled_graph(*node_ids: str, entries: tuple[str, ...] = ("a",)) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("test.graph"),
            version=GraphDefinitionVersion(1),
            nodes=tuple(NodeDefinition(NodeId(node_id), identity) for node_id in node_ids),
            edges=(),
            entries=tuple(NodeId(node_id) for node_id in entries),
            exits=(),
        )
    )


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
    )
