"""Graph-run state projections owned by the execution subsystem."""

from typing import TypeVar

from mote_kernel.execution.graph.definition import GraphDefinitionId, GraphDefinitionVersion
from mote_kernel.execution.graph.identity import NodeId
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.snapshot import (
    ExecutionAttemptId,
    ExecutionLeaseSnapshot,
    ExecutionSnapshot,
    ExecutionStatus,
    ExecutionTaskId,
    ExecutionToken,
    GraphRunId,
    InterruptId,
    InterruptLifecycle,
    InterruptPayload,
    InterruptReceipt,
    InterruptRecord,
    JoinProgress,
    ParentTaskId,
    ParentTaskRef,
    ResolutionCodecId,
)
from mote_kernel.execution.transition import AdvanceTransition, CompleteTransition, ExecutionTransition
from mote_kernel.state.graph_state.command import (
    AdvanceGraphRun,
    CompleteGraphRun,
    FailGraphExecution,
    GraphRunCommand,
    StartGraphRun,
)
from mote_kernel.state.graph_state.model import (
    GraphDefinitionId as StateGraphDefinitionId,
)
from mote_kernel.state.graph_state.model import (
    GraphDefinitionVersion as StateGraphDefinitionVersion,
)
from mote_kernel.state.graph_state.model import (
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFailure,
    GraphInterruptLifecycle,
    GraphJoinProgress,
    GraphNodeId,
    GraphResolutionCodec,
    GraphResolutionCodecId,
    GraphRunState,
    GraphRunStatus,
    ParentGraphTask,
)
from mote_kernel.state.graph_state.model import (
    GraphRunId as StateGraphRunId,
)
from mote_kernel.state.graph_state.reducer import validate_graph_run_state

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")

_EXECUTION_STATUS = {
    GraphRunStatus.RUNNING: ExecutionStatus.RUNNING,
    GraphRunStatus.SUSPENDED: ExecutionStatus.SUSPENDED,
    GraphRunStatus.COMPLETED: ExecutionStatus.COMPLETED,
    GraphRunStatus.FAILED: ExecutionStatus.FAILED,
}

_INTERRUPT_LIFECYCLE = {
    GraphInterruptLifecycle.REQUESTED: InterruptLifecycle.REQUESTED,
    GraphInterruptLifecycle.RESOLVED: InterruptLifecycle.RESOLVED,
    GraphInterruptLifecycle.CONSUMED: InterruptLifecycle.CONSUMED,
    GraphInterruptLifecycle.CANCELLED: InterruptLifecycle.CANCELLED,
}


def project_execution_snapshot(state: GraphRunState) -> ExecutionSnapshot:
    """Project committed graph-run facts into execution-owned DTOs."""

    validate_graph_run_state(state)
    parent = state.parent
    execution = state.execution
    interrupt = state.interrupt
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
        parallel=state.parallel,
        execution_sequence=state.execution_sequence,
        execution=(
            ExecutionLeaseSnapshot(
                ExecutionToken(
                    execution.token.generation,
                    ExecutionAttemptId(execution.token.attempt_id),
                ),
                tuple(ExecutionTaskId(task_id) for task_id in execution.task_ids),
            )
            if execution is not None
            else None
        ),
        interrupt=(
            InterruptRecord(
                GraphRunId(interrupt.identity.root_run_id),
                InterruptId(interrupt.identity.interrupt_id),
                interrupt.identity.generation,
                InterruptPayload(interrupt.request_payload),
                ResolutionCodecId(interrupt.resolution_codec.codec_id),
                interrupt.resolution_codec.version,
                _INTERRUPT_LIFECYCLE[interrupt.lifecycle],
                (InterruptPayload(interrupt.resolution_payload) if interrupt.resolution_payload is not None else None),
                InterruptReceipt(interrupt.receipt.superstep) if interrupt.receipt is not None else None,
            )
            if interrupt is not None
            else None
        ),
    )


def _project_execution_token(token: ExecutionToken) -> GraphExecutionToken:
    return GraphExecutionToken(token.generation, GraphExecutionAttemptId(token.attempt_id))


def project_start_graph_command(
    graph: CompiledGraph[InputT, OutputT],
    run_id: StateGraphRunId,
    parent: ParentGraphTask | None = None,
) -> StartGraphRun:
    """Derive initial durable graph state only from one compiled graph definition."""

    resolution = graph.resolution
    return StartGraphRun(
        run_id,
        StateGraphDefinitionId(graph.definition_id),
        StateGraphDefinitionVersion(graph.version),
        tuple(GraphNodeId(node_id) for node_id in graph.entries),
        parent,
        (
            GraphResolutionCodec(GraphResolutionCodecId(resolution.codec_id), resolution.version)
            if resolution is not None
            else None
        ),
    )


def project_graph_command(transition: ExecutionTransition) -> GraphRunCommand:
    """Project an execution outcome into the authoritative state command."""

    if isinstance(transition, AdvanceTransition):
        return AdvanceGraphRun(
            expected_superstep=transition.expected_superstep,
            execution=_project_execution_token(transition.execution),
            expected_interrupt_generation=transition.expected_interrupt_generation,
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
        return CompleteGraphRun(
            transition.expected_superstep,
            _project_execution_token(transition.execution),
            transition.expected_interrupt_generation,
        )
    return FailGraphExecution(
        transition.expected_superstep,
        _project_execution_token(transition.execution),
        transition.expected_interrupt_generation,
        GraphFailure(transition.failure),
    )


__all__ = ["project_execution_snapshot", "project_graph_command", "project_start_graph_command"]
