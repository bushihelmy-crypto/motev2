from dataclasses import FrozenInstanceError

import pytest
from tests.execution.engine.factories import conditional, direct, join, snapshot, topology

from mote_kernel.execution.engine import plan_tasks
from mote_kernel.execution.engine.collector import CollectedResults, collect_results
from mote_kernel.execution.engine.routing import route_results
from mote_kernel.execution.errors import (
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    UnknownRouteError,
)
from mote_kernel.execution.graph import CompiledGraph, NodeId, RouteId
from mote_kernel.execution.graph.command import Continue, SelectRoute
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskSuccess
from mote_kernel.execution.snapshot import ExecutionSnapshot, JoinProgress


def collected_for(
    graph: CompiledGraph[str, str],
    execution_snapshot: ExecutionSnapshot,
    routes: dict[str, str] | None = None,
) -> CollectedResults[str]:
    tasks = plan_tasks(graph, execution_snapshot, ExecutionLimits())
    results = tuple(
        TaskSuccess(
            task,
            f"{task.node_id}-output",
            SelectRoute(RouteId(routes[str(task.node_id)])) if routes and str(task.node_id) in routes else Continue(),
        )
        for task in reversed(tasks)
    )
    return collect_results(execution_snapshot, tasks, results)


def test_direct_fanout_is_sorted_and_deduplicated() -> None:
    graph = topology("a", "b", "c", edges=(direct("a", "c"), direct("a", "b")))
    execution_snapshot = snapshot()

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))

    assert decision.frontier == (NodeId("b"), NodeId("c"))
    assert decision.join_progress == ()


def test_conditional_route_selects_exact_target() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(conditional("a", "left", "b"), conditional("a", "right", "c")),
    )
    execution_snapshot = snapshot()

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot, {"a": "right"}))

    assert decision.frontier == (NodeId("c"),)


def test_unknown_conditional_route_fails_closed() -> None:
    graph = topology("a", "b", edges=(conditional("a", "known", "b"),))
    execution_snapshot = snapshot()

    with pytest.raises(UnknownRouteError):
        route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot, {"a": "unknown"}))


def test_conditional_node_must_select_route() -> None:
    graph = topology("a", "b", edges=(conditional("a", "next", "b"),))
    execution_snapshot = snapshot()

    with pytest.raises(InvalidRoutingCommandError, match="select"):
        route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))


def test_direct_and_conditional_edges_may_advance_together() -> None:
    graph = topology("a", "b", "c", edges=(direct("a", "b"), conditional("a", "optional", "c")))
    execution_snapshot = snapshot()

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot, {"a": "optional"}))

    assert decision.frontier == (NodeId("b"), NodeId("c"))


def test_direct_path_cannot_skip_declared_conditional_route() -> None:
    graph = topology("a", "b", "c", edges=(direct("a", "b"), conditional("a", "optional", "c")))
    execution_snapshot = snapshot()

    with pytest.raises(InvalidRoutingCommandError, match="select"):
        route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))


def test_same_step_join_fires_once() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(join(("a", "b"), "c"),),
        entries=("a", "b"),
    )
    execution_snapshot = snapshot(frontier=("b", "a"))

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))

    assert decision.frontier == (NodeId("c"),)
    assert decision.join_progress == ()


def test_one_source_can_complete_multiple_joins_in_the_same_step() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        "e",
        edges=(join(("a", "b"), "d"), join(("a", "c"), "e")),
        entries=("a", "b", "c"),
    )
    execution_snapshot = snapshot(frontier=("c", "a", "b"))

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))

    assert decision.frontier == (NodeId("d"), NodeId("e"))
    assert decision.join_progress == ()


def test_distinct_completed_joins_with_same_target_schedule_it_once() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        edges=(join(("a", "b"), "d"), join(("a", "c"), "d")),
        entries=("a", "b", "c"),
    )
    execution_snapshot = snapshot(frontier=("a", "b", "c"))

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))

    assert decision.frontier == (NodeId("d"),)
    assert decision.join_progress == ()


def test_direct_and_join_arrivals_schedule_the_same_target_once() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "c"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )
    execution_snapshot = snapshot(frontier=("a", "b"))

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))

    assert decision.frontier == (NodeId("c"),)
    assert decision.join_progress == ()


def test_direct_target_can_run_before_join_and_again_when_join_completes() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "c"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )
    first_snapshot = snapshot(frontier=("a",))

    first = route_results(graph, first_snapshot, collected_for(graph, first_snapshot))
    second_snapshot = snapshot(superstep=1, frontier=("b",), join_progress=first.join_progress)
    second = route_results(graph, second_snapshot, collected_for(graph, second_snapshot))

    assert first.frontier == (NodeId("c"),)
    assert first.join_progress == (JoinProgress((NodeId("a"), NodeId("b")), NodeId("c"), frozenset({NodeId("a")})),)
    assert second.frontier == (NodeId("c"),)
    assert second.join_progress == ()


def test_conditional_branch_can_supply_a_join_source() -> None:
    graph = topology(
        "start",
        "a",
        "b",
        "end",
        edges=(
            direct("start", "a"),
            conditional("start", "right", "b"),
            join(("a", "b"), "end"),
        ),
        entries=("start",),
    )
    first_snapshot = snapshot(frontier=("start",))
    first = route_results(graph, first_snapshot, collected_for(graph, first_snapshot, {"start": "right"}))
    second_snapshot = snapshot(superstep=1, frontier=("a", "b"), join_progress=first.join_progress)

    second = route_results(graph, second_snapshot, collected_for(graph, second_snapshot))

    assert first.frontier == (NodeId("a"), NodeId("b"))
    assert second.frontier == (NodeId("end"),)
    assert second.join_progress == ()


def test_chained_joins_advance_to_a_fixed_point_across_supersteps() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        "e",
        edges=(join(("a", "b"), "c"), direct("c", "d"), join(("c", "d"), "e")),
        entries=("a", "b"),
    )
    first_snapshot = snapshot(frontier=("a", "b"))
    first = route_results(graph, first_snapshot, collected_for(graph, first_snapshot))
    second_snapshot = snapshot(superstep=1, frontier=first.frontier, join_progress=first.join_progress)
    second = route_results(graph, second_snapshot, collected_for(graph, second_snapshot))
    third_snapshot = snapshot(superstep=2, frontier=second.frontier, join_progress=second.join_progress)

    third = route_results(graph, third_snapshot, collected_for(graph, third_snapshot))

    assert first.frontier == (NodeId("c"),)
    assert second.frontier == (NodeId("d"),)
    assert second.join_progress == (JoinProgress((NodeId("c"), NodeId("d")), NodeId("e"), frozenset({NodeId("c")})),)
    assert third.frontier == (NodeId("e"),)
    assert third.join_progress == ()


def test_self_loop_schedules_same_node_in_next_superstep() -> None:
    graph = topology("a", edges=(direct("a", "a"),))
    execution_snapshot = snapshot(superstep=5)

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))

    assert decision.frontier == (NodeId("a"),)


def test_join_can_fire_again_after_its_prior_progress_was_consumed() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    execution_snapshot = snapshot(superstep=4, frontier=("a", "b"))

    first = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))
    second_snapshot = snapshot(superstep=7, frontier=("a", "b"), join_progress=first.join_progress)
    second = route_results(graph, second_snapshot, collected_for(graph, second_snapshot))

    assert first.frontier == second.frontier == (NodeId("c"),)
    assert first.join_progress == second.join_progress == ()


def test_join_progress_survives_across_supersteps() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        edges=(direct("a", "b"), direct("b", "c"), join(("a", "c"), "d")),
    )
    first_snapshot = snapshot(frontier=("a",))
    first = route_results(graph, first_snapshot, collected_for(graph, first_snapshot))
    assert first.frontier == (NodeId("b"),)
    assert first.join_progress == (JoinProgress((NodeId("a"), NodeId("c")), NodeId("d"), frozenset({NodeId("a")})),)

    second_snapshot = snapshot(superstep=1, frontier=("b",), join_progress=first.join_progress)
    second = route_results(graph, second_snapshot, collected_for(graph, second_snapshot))
    assert second.frontier == (NodeId("c"),)
    assert second.join_progress == first.join_progress

    third_snapshot = snapshot(superstep=2, frontier=("c",), join_progress=second.join_progress)
    third = route_results(graph, third_snapshot, collected_for(graph, third_snapshot))
    assert third.frontier == (NodeId("d"),)
    assert third.join_progress == ()


def test_repeated_arrival_for_an_incomplete_join_is_idempotent() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "a"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )
    progress = JoinProgress(
        (NodeId("a"), NodeId("b")),
        NodeId("c"),
        frozenset({NodeId("a")}),
    )
    execution_snapshot = snapshot(superstep=3, frontier=("a",), join_progress=(progress,))

    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))

    assert decision.frontier == (NodeId("a"),)
    assert decision.join_progress == (progress,)


def test_persisted_join_progress_order_does_not_change_routing_decision() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        "e",
        edges=(
            direct("a", "a"),
            join(("a", "b"), "d"),
            join(("a", "c"), "e"),
        ),
        entries=("a", "b", "c"),
    )
    first = JoinProgress(
        (NodeId("a"), NodeId("b")),
        NodeId("d"),
        frozenset({NodeId("a")}),
    )
    second = JoinProgress(
        (NodeId("a"), NodeId("c")),
        NodeId("e"),
        frozenset({NodeId("a")}),
    )
    ordered_snapshot = snapshot(frontier=("a",), join_progress=(first, second))
    reversed_snapshot = snapshot(frontier=("a",), join_progress=(second, first))

    ordered = route_results(graph, ordered_snapshot, collected_for(graph, ordered_snapshot))
    reversed_decision = route_results(graph, reversed_snapshot, collected_for(graph, reversed_snapshot))

    assert ordered == reversed_decision
    assert ordered.join_progress == (first, second)


def test_partial_join_without_continuing_work_fails_as_deadlock() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    execution_snapshot = snapshot(frontier=("a",))

    with pytest.raises(RoutingDeadlockError):
        route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))


@pytest.mark.parametrize(
    "progress",
    [
        JoinProgress((NodeId("a"), NodeId("b")), NodeId("c"), frozenset()),
        JoinProgress((NodeId("a"), NodeId("b")), NodeId("c"), frozenset({NodeId("a"), NodeId("b")})),
        JoinProgress((NodeId("a"), NodeId("b")), NodeId("unknown"), frozenset({NodeId("a")})),
    ],
)
def test_invalid_persisted_join_progress_fails_closed(progress: JoinProgress) -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    execution_snapshot = snapshot(frontier=("b",))
    collected = collected_for(graph, execution_snapshot)
    object.__setattr__(execution_snapshot, "join_progress", (progress,))

    with pytest.raises(JoinProgressError):
        route_results(graph, execution_snapshot, collected)


def test_duplicate_persisted_join_progress_fails_closed() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    progress = JoinProgress((NodeId("a"), NodeId("b")), NodeId("c"), frozenset({NodeId("a")}))
    execution_snapshot = snapshot(frontier=("b",))
    collected = collected_for(graph, execution_snapshot)
    object.__setattr__(execution_snapshot, "join_progress", (progress, progress))

    with pytest.raises(JoinProgressError, match="repeats"):
        route_results(graph, execution_snapshot, collected)


def test_failed_collection_cannot_be_routed() -> None:
    graph = topology("a")
    execution_snapshot = snapshot()
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]

    with pytest.raises(InvalidRoutingCommandError):
        route_results(graph, execution_snapshot, CollectedResults((), TaskFailure(task, "failed")))


def test_manually_forged_success_for_unknown_node_fails_as_routing_error() -> None:
    graph = topology("a")
    execution_snapshot = snapshot()
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]
    foreign_task = type(task)(task.task_id, task.run_id, task.superstep, NodeId("unknown"))

    with pytest.raises(InvalidRoutingCommandError):
        route_results(graph, execution_snapshot, CollectedResults((TaskSuccess(foreign_task, "output"),)))


def test_manually_forged_success_outside_frontier_fails_as_routing_error() -> None:
    graph = topology("a", "b", entries=("a", "b"))
    execution_snapshot = snapshot(frontier=("a",))
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]
    foreign_task = type(task)(task.task_id, task.run_id, task.superstep, NodeId("b"))

    with pytest.raises(InvalidRoutingCommandError):
        route_results(graph, execution_snapshot, CollectedResults((TaskSuccess(foreign_task, "output"),)))


def test_stale_success_from_prior_superstep_cannot_be_routed() -> None:
    graph = topology("a")
    current_snapshot = snapshot(superstep=2)
    stale_task = plan_tasks(graph, snapshot(superstep=1), ExecutionLimits())[0]

    with pytest.raises(InvalidRoutingCommandError):
        route_results(graph, current_snapshot, CollectedResults((TaskSuccess(stale_task, "output"),)))


def test_manually_forged_empty_success_collection_fails_as_routing_error() -> None:
    graph = topology("a")

    with pytest.raises(InvalidRoutingCommandError):
        route_results(graph, snapshot(), CollectedResults(()))


def test_manually_forged_partial_success_collection_fails_as_routing_error() -> None:
    graph = topology("a", "b", entries=("a", "b"))
    execution_snapshot = snapshot(frontier=("a", "b"))
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]

    with pytest.raises(InvalidRoutingCommandError):
        route_results(graph, execution_snapshot, CollectedResults((TaskSuccess(task, "output"),)))


def test_manually_forged_duplicate_success_collection_fails_as_routing_error() -> None:
    graph = topology("a")
    execution_snapshot = snapshot()
    task = plan_tasks(graph, execution_snapshot, ExecutionLimits())[0]
    success = TaskSuccess(task, "output")

    with pytest.raises(InvalidRoutingCommandError):
        route_results(graph, execution_snapshot, CollectedResults((success, success)))


def test_routing_decision_is_immutable() -> None:
    graph = topology("a", "b", edges=(direct("a", "b"),))
    execution_snapshot = snapshot()
    decision = route_results(graph, execution_snapshot, collected_for(graph, execution_snapshot))

    with pytest.raises(FrozenInstanceError):
        decision.frontier = ()  # type: ignore[misc]
