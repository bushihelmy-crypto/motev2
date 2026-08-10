import pytest
from tests.execution.engine.factories import conditional, direct, join, snapshot, topology

from mote_kernel.execution.engine import plan_tasks, settle_tasks
from mote_kernel.execution.errors import RoutingDeadlockError, SnapshotMismatchError
from mote_kernel.execution.graph import END, NodeId
from mote_kernel.execution.graph.command import SelectRoute
from mote_kernel.execution.graph.edge import RouteId
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskSuccess
from mote_kernel.execution.snapshot import JoinProgress
from mote_kernel.execution.transition import AdvanceTransition, CompleteTransition, FailTransition


def test_success_with_next_frontier_advances() -> None:
    graph = topology("a", "b", edges=(direct("a", "b"),))
    execution_snapshot = snapshot(superstep=4)
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]
    assert settle_tasks(graph, execution_snapshot, (task,), (TaskSuccess(task, "output"),)) == AdvanceTransition(
        4, (NodeId("b"),)
    )


def test_success_without_next_frontier_completes() -> None:
    graph = topology("a", edges=(direct("a", END),))
    execution_snapshot = snapshot(superstep=2)
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]
    assert settle_tasks(graph, execution_snapshot, (task,), (TaskSuccess(task, "output"),)) == CompleteTransition(2)


def test_conditional_route_to_end_completes() -> None:
    graph = topology("a", edges=(conditional("a", "finish", END),))
    execution_snapshot = snapshot(superstep=2)
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]
    assert settle_tasks(
        graph,
        execution_snapshot,
        (task,),
        (TaskSuccess(task, "output", SelectRoute(RouteId("finish"))),),
    ) == CompleteTransition(2)


def test_join_to_end_completes_after_all_sources() -> None:
    graph = topology("a", "b", edges=(join(("a", "b"), END),), entries=("a", "b"))
    execution_snapshot = snapshot(superstep=2, frontier=("a", "b"))
    tasks = plan_tasks(graph, execution_snapshot, ExecutionLimits())
    assert settle_tasks(
        graph,
        execution_snapshot,
        tasks,
        tuple(TaskSuccess(task, "output") for task in tasks),
    ) == CompleteTransition(2)


def test_join_to_end_cannot_complete_before_all_sources_arrive() -> None:
    graph = topology("a", "b", edges=(join(("a", "b"), END),), entries=("a", "b"))
    execution_snapshot = snapshot(superstep=2, frontier=("a",))
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]

    with pytest.raises(RoutingDeadlockError):
        settle_tasks(graph, execution_snapshot, (task,), (TaskSuccess(task, "output"),))


def test_failure_selects_superstep_bound_fail_transition_without_routing() -> None:
    graph = topology("a", "b", edges=(direct("a", "b"),))
    execution_snapshot = snapshot(superstep=3)
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]
    assert settle_tasks(graph, execution_snapshot, (task,), (TaskFailure(task, "node failed"),)) == FailTransition(
        3, "node failed"
    )


@pytest.mark.parametrize(
    ("definition_id", "version"),
    [("other.graph", 1), ("test.graph", 2)],
)
def test_settlement_rejects_snapshot_owned_by_another_graph_before_failure_selection(
    definition_id: str, version: int
) -> None:
    graph = topology("a")
    execution_snapshot = snapshot(definition_id=definition_id, version=version)
    task = plan_tasks(topology("a"), snapshot(), ExecutionLimits())[0]

    with pytest.raises(SnapshotMismatchError):
        settle_tasks(graph, execution_snapshot, (task,), (TaskFailure(task, "node failed"),))


def test_advance_transition_carries_partial_join_progress() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        edges=(direct("a", "b"), join(("a", "c"), "d")),
        entries=("a", "c"),
    )
    execution_snapshot = snapshot(superstep=6)
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]
    progress = JoinProgress(
        (NodeId("a"), NodeId("c")),
        NodeId("d"),
        frozenset({NodeId("a")}),
    )

    assert settle_tasks(graph, execution_snapshot, (task,), (TaskSuccess(task, "output"),)) == AdvanceTransition(
        6,
        (NodeId("b"),),
        (progress,),
    )
