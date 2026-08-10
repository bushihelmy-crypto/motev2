"""Graph-run state adapters owned by the execution subsystem."""

from mote_kernel.execution.graph.definition import GraphDefinitionId, GraphDefinitionVersion
from mote_kernel.execution.graph.identity import NodeId
from mote_kernel.execution.snapshot import (
    ExecutionSnapshot,
    ExecutionStatus,
    GraphRunId,
    JoinProgress,
    ParentTaskId,
    ParentTaskRef,
)
from mote_kernel.execution.transition import AdvanceTransition, CompleteTransition, ExecutionTransition
from mote_kernel.state.graph_state.command import AdvanceGraphRun, CompleteGraphRun, FailGraphRun, GraphRunCommand
from mote_kernel.state.graph_state.model import (
    GraphFailure,
    GraphJoinProgress,
    GraphNodeId,
    GraphRunState,
    GraphRunStatus,
)

_EXECUTION_STATUS = {
    GraphRunStatus.RUNNING: ExecutionStatus.RUNNING,
    GraphRunStatus.SUSPENDED: ExecutionStatus.SUSPENDED,
    GraphRunStatus.COMPLETED: ExecutionStatus.COMPLETED,
    GraphRunStatus.FAILED: ExecutionStatus.FAILED,
}


def project_execution_snapshot(state: GraphRunState) -> ExecutionSnapshot:
    """Project committed graph-run facts into execution-owned DTOs."""

    parent = state.parent
    return ExecutionSnapshot(
        run_id=GraphRunId(state.run_id),
        definition_id=GraphDefinitionId(state.definition_id),
        definition_version=GraphDefinitionVersion(state.definition_version),
        status=_EXECUTION_STATUS[state.status],
        superstep=state.superstep,
        frontier=tuple(NodeId(node_id) for node_id in state.frontier),
        parent=(ParentTaskRef(GraphRunId(parent.run_id), ParentTaskId(parent.task_id)) if parent is not None else None),
        join_progress=tuple(
            JoinProgress(
                tuple(NodeId(source) for source in progress.sources),
                NodeId(progress.target),
                frozenset(NodeId(source) for source in progress.arrived),
            )
            for progress in state.join_progress
        ),
    )


def project_graph_command(transition: ExecutionTransition) -> GraphRunCommand:
    """Project an execution outcome into the authoritative state command."""

    if isinstance(transition, AdvanceTransition):
        return AdvanceGraphRun(
            expected_superstep=transition.expected_superstep,
            frontier=tuple(GraphNodeId(node_id) for node_id in transition.frontier),
            join_progress=tuple(
                GraphJoinProgress(
                    tuple(GraphNodeId(source) for source in progress.sources),
                    GraphNodeId(progress.target),
                    frozenset(GraphNodeId(source) for source in progress.arrived),
                )
                for progress in transition.join_progress
            ),
        )
    if isinstance(transition, CompleteTransition):
        return CompleteGraphRun(transition.expected_superstep)
    return FailGraphRun(transition.expected_superstep, GraphFailure(transition.failure))


__all__ = ["project_execution_snapshot", "project_graph_command"]
