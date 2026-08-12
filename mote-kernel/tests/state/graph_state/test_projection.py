from mote_kernel.execution import project_execution_snapshot, project_graph_command
from mote_kernel.execution.graph import (
    END,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    NodeDefinition,
    NodeId,
    NodeSuccess,
    ResolutionBinding,
    ResolutionCodecId,
    compile_graph,
)
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.snapshot import (
    ExecutionAttemptId,
    ExecutionLeaseSnapshot,
    ExecutionSnapshot,
    ExecutionStatus,
    ExecutionTaskId,
    ExecutionToken,
    InterruptId,
    InterruptLifecycle,
    InterruptPayload,
    InterruptRecord,
    JoinProgress,
    ParentTaskId,
    ParentTaskRef,
)
from mote_kernel.execution.snapshot import GraphRunId as ExecutionRunId
from mote_kernel.execution.snapshot import ResolutionCodecId as SnapshotCodecId
from mote_kernel.execution.transition import AdvanceTransition, CompleteTransition, FailTransition
from mote_kernel.state.graph_state import (
    AdvanceGraphRun,
    CompleteGraphRun,
    FailGraphExecution,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphInterruptId,
    GraphInterruptIdentity,
    GraphInterruptLifecycle,
    GraphInterruptPayload,
    GraphInterruptRecord,
    GraphJoinProgress,
    GraphNodeId,
    GraphResolutionCodec,
    GraphResolutionCodecId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphTaskId,
    ParentGraphTask,
    reduce_graph_run,
)
from mote_kernel.state.graph_state import GraphDefinitionId as StateDefinitionId
from mote_kernel.state.graph_state import GraphDefinitionVersion as StateDefinitionVersion

EXECUTION_TOKEN = ExecutionToken(3, ExecutionAttemptId("attempt"))
STATE_TOKEN = GraphExecutionToken(3, GraphExecutionAttemptId("attempt"))
CODEC = GraphResolutionCodec(GraphResolutionCodecId("input.v1"), 2)


class StringDecoder:
    def decode(self, payload: bytes) -> str:
        return payload.decode("utf-8")


async def identity(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def test_projection_maps_every_authoritative_graph_run_fact() -> None:
    interrupt = GraphInterruptRecord(
        GraphInterruptIdentity(GraphRunId("root"), GraphInterruptId("pause"), 4),
        GraphInterruptPayload(b"request"),
        CODEC,
        GraphInterruptLifecycle.RESOLVED,
        GraphInterruptPayload(b"resolution"),
    )
    state = GraphRunState(
        run_id=GraphRunId("child"),
        definition_id=StateDefinitionId("flow.graph"),
        definition_version=StateDefinitionVersion(3),
        status=GraphRunStatus.RUNNING,
        superstep=7,
        frontier=(GraphNodeId("b"), GraphNodeId("a")),
        revision=11,
        parent=ParentGraphTask(GraphRunId("root"), GraphTaskId("parent-task")),
        join_progress=(
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("c")),
                GraphNodeId("d"),
                frozenset({GraphNodeId("a")}),
            ),
        ),
        execution_sequence=3,
        execution=GraphExecutionLease(STATE_TOKEN, (GraphTaskId("task"),)),
        interrupt=interrupt,
        resolution_codec=CODEC,
    )

    assert project_execution_snapshot(state) == ExecutionSnapshot(
        run_id=ExecutionRunId("child"),
        definition_id=GraphDefinitionId("flow.graph"),
        definition_version=GraphDefinitionVersion(3),
        status=ExecutionStatus.RUNNING,
        superstep=7,
        frontier=(NodeId("b"), NodeId("a")),
        revision=11,
        parent=ParentTaskRef(ExecutionRunId("root"), ParentTaskId("parent-task")),
        join_progress=(
            JoinProgress(
                (NodeId("a"), NodeId("c")),
                NodeId("d"),
                frozenset({NodeId("a")}),
            ),
        ),
        execution_sequence=3,
        execution=ExecutionLeaseSnapshot(EXECUTION_TOKEN, (ExecutionTaskId("task"),)),
        interrupt=InterruptRecord(
            ExecutionRunId("root"),
            InterruptId("pause"),
            4,
            InterruptPayload(b"request"),
            SnapshotCodecId("input.v1"),
            2,
            InterruptLifecycle.RESOLVED,
            InterruptPayload(b"resolution"),
        ),
    )


def test_projection_maps_every_lifecycle_status() -> None:
    for state_status, execution_status in (
        (GraphRunStatus.RUNNING, ExecutionStatus.RUNNING),
        (GraphRunStatus.SUSPENDED, ExecutionStatus.SUSPENDED),
        (GraphRunStatus.COMPLETED, ExecutionStatus.COMPLETED),
        (GraphRunStatus.FAILED, ExecutionStatus.FAILED),
    ):
        state = GraphRunState(
            GraphRunId("run"),
            StateDefinitionId("graph"),
            StateDefinitionVersion(1),
            state_status,
            0,
            (GraphNodeId("a"),) if state_status in {GraphRunStatus.RUNNING, GraphRunStatus.SUSPENDED} else (),
            failure=GraphFailure("failed") if state_status is GraphRunStatus.FAILED else None,
            interrupt=(
                GraphInterruptRecord(
                    GraphInterruptIdentity(GraphRunId("run"), GraphInterruptId("pause"), 1),
                    GraphInterruptPayload(b"request"),
                    CODEC,
                    GraphInterruptLifecycle.REQUESTED,
                )
                if state_status is GraphRunStatus.SUSPENDED
                else None
            ),
            resolution_codec=CODEC if state_status is GraphRunStatus.SUSPENDED else None,
        )

        assert project_execution_snapshot(state).status is execution_status


def test_compiled_graph_projects_start_with_fixed_resolution_codec() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(5),
            (NodeDefinition(NodeId("a"), identity),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
            resolution=ResolutionBinding(ResolutionCodecId("input.v1"), 2, StringDecoder()),
        )
    )

    command = project_start_graph_command(
        graph,
        GraphRunId("child"),
        ParentGraphTask(GraphRunId("root"), GraphTaskId("task")),
    )

    assert command.run_id == GraphRunId("child")
    assert command.definition_id == StateDefinitionId("graph")
    assert command.definition_version == StateDefinitionVersion(5)
    assert command.frontier == (GraphNodeId("a"),)
    assert command.resolution_codec == CODEC


def test_compiled_graph_without_resolution_codec_projects_none() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), identity),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    assert project_start_graph_command(graph, GraphRunId("root")).resolution_codec is None


def test_advance_transition_projects_every_state_command_fact() -> None:
    progress = JoinProgress(
        (NodeId("a"), NodeId("b")),
        NodeId("c"),
        frozenset({NodeId("a")}),
    )

    assert project_graph_command(AdvanceTransition(8, EXECUTION_TOKEN, (NodeId("d"),), (progress,))) == AdvanceGraphRun(
        8,
        STATE_TOKEN,
        (GraphNodeId("d"),),
        (
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("b")),
                GraphNodeId("c"),
                frozenset({GraphNodeId("a")}),
            ),
        ),
    )


def test_terminal_transitions_project_token_generation_and_failure() -> None:
    assert project_graph_command(CompleteTransition(5, EXECUTION_TOKEN)) == CompleteGraphRun(5, STATE_TOKEN)
    assert project_graph_command(FailTransition(6, EXECUTION_TOKEN, "node failed")) == FailGraphExecution(
        6, STATE_TOKEN, GraphFailure("node failed")
    )


def leased_state(*, superstep: int = 0) -> GraphRunState:
    return GraphRunState(
        GraphRunId("run"),
        StateDefinitionId("graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        superstep,
        (GraphNodeId("a"),),
        execution_sequence=3,
        execution=GraphExecutionLease(STATE_TOKEN, (GraphTaskId("task"),)),
    )


def test_advance_transition_projects_through_reducer_into_durable_state() -> None:
    advanced = reduce_graph_run(
        leased_state(superstep=3),
        project_graph_command(AdvanceTransition(0, EXECUTION_TOKEN, (NodeId("d"),))),
    )

    assert advanced.superstep == 4
    assert advanced.frontier == (GraphNodeId("d"),)


def test_complete_transition_projects_through_reducer_into_durable_state() -> None:
    completed = reduce_graph_run(
        leased_state(superstep=3),
        project_graph_command(CompleteTransition(0, EXECUTION_TOKEN)),
    )

    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.frontier == ()


def test_fail_transition_projects_through_reducer_into_durable_state() -> None:
    failed = reduce_graph_run(
        leased_state(superstep=3),
        project_graph_command(FailTransition(0, EXECUTION_TOKEN, "node failed")),
    )

    assert failed.status is GraphRunStatus.FAILED
    assert failed.frontier == ()
    assert failed.failure == GraphFailure("node failed")
