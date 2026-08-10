from mote_kernel.execution import project_execution_snapshot, project_graph_command
from mote_kernel.execution.graph import GraphDefinitionId, GraphDefinitionVersion, NodeId
from mote_kernel.execution.snapshot import (
    ExecutionSnapshot,
    ExecutionStatus,
    JoinProgress,
    ParentTaskId,
    ParentTaskRef,
)
from mote_kernel.execution.snapshot import GraphRunId as ExecutionRunId
from mote_kernel.execution.transition import AdvanceTransition, CompleteTransition, FailTransition
from mote_kernel.state.graph_state import (
    AdvanceGraphRun,
    CompleteGraphRun,
    FailGraphRun,
    GraphFailure,
    GraphJoinProgress,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphTaskId,
    ParentGraphTask,
    reduce_graph_run,
)
from mote_kernel.state.graph_state import GraphDefinitionId as StateDefinitionId
from mote_kernel.state.graph_state import GraphDefinitionVersion as StateDefinitionVersion


def test_projection_maps_every_authoritative_graph_run_fact() -> None:
    state = GraphRunState(
        run_id=GraphRunId("child"),
        definition_id=StateDefinitionId("flow.graph"),
        definition_version=StateDefinitionVersion(3),
        status=GraphRunStatus.SUSPENDED,
        superstep=7,
        frontier=(GraphNodeId("b"), GraphNodeId("a")),
        parent=ParentGraphTask(GraphRunId("parent"), GraphTaskId("parent-task")),
        join_progress=(
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("c")),
                GraphNodeId("d"),
                frozenset({GraphNodeId("a")}),
            ),
        ),
    )

    assert project_execution_snapshot(state) == ExecutionSnapshot(
        run_id=ExecutionRunId("child"),
        definition_id=GraphDefinitionId("flow.graph"),
        definition_version=GraphDefinitionVersion(3),
        status=ExecutionStatus.SUSPENDED,
        superstep=7,
        frontier=(NodeId("b"), NodeId("a")),
        parent=ParentTaskRef(ExecutionRunId("parent"), ParentTaskId("parent-task")),
        join_progress=(
            JoinProgress(
                (NodeId("a"), NodeId("c")),
                NodeId("d"),
                frozenset({NodeId("a")}),
            ),
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
        )

        assert project_execution_snapshot(state).status is execution_status


def test_advance_transition_projects_every_state_command_fact() -> None:
    progress = JoinProgress(
        (NodeId("a"), NodeId("b")),
        NodeId("c"),
        frozenset({NodeId("a")}),
    )

    assert project_graph_command(AdvanceTransition(4, (NodeId("d"),), (progress,))) == AdvanceGraphRun(
        4,
        (GraphNodeId("d"),),
        (
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("b")),
                GraphNodeId("c"),
                frozenset({GraphNodeId("a")}),
            ),
        ),
    )


def test_terminal_transitions_project_expected_superstep_and_failure() -> None:
    assert project_graph_command(CompleteTransition(5)) == CompleteGraphRun(5)
    assert project_graph_command(FailTransition(6, "node failed")) == FailGraphRun(6, GraphFailure("node failed"))


def running_state(*, superstep: int = 0) -> GraphRunState:
    return GraphRunState(
        GraphRunId("run"),
        StateDefinitionId("graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        superstep,
        (GraphNodeId("a"),),
    )


def test_advance_transition_projects_through_reducer_into_durable_state() -> None:
    progress = JoinProgress(
        (NodeId("a"), NodeId("b")),
        NodeId("c"),
        frozenset({NodeId("a")}),
    )

    state = reduce_graph_run(
        running_state(superstep=3),
        project_graph_command(AdvanceTransition(3, (NodeId("d"),), (progress,))),
    )

    assert state.superstep == 4
    assert state.frontier == (GraphNodeId("d"),)
    assert state.join_progress == (
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("c"),
            frozenset({GraphNodeId("a")}),
        ),
    )


def test_complete_transition_projects_through_reducer_into_durable_state() -> None:
    state = reduce_graph_run(running_state(superstep=3), project_graph_command(CompleteTransition(3)))

    assert state.status is GraphRunStatus.COMPLETED
    assert state.superstep == 3
    assert state.frontier == ()


def test_fail_transition_projects_through_reducer_into_durable_state() -> None:
    state = reduce_graph_run(running_state(superstep=3), project_graph_command(FailTransition(3, "node failed")))

    assert state.status is GraphRunStatus.FAILED
    assert state.superstep == 3
    assert state.frontier == ()
    assert state.failure == GraphFailure("node failed")
