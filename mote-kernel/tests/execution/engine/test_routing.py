from dataclasses import FrozenInstanceError
from typing import cast

import pytest
from tests.execution.engine.factories import conditional, direct, join, topology

from mote_kernel.execution.engine.routing import resolve_routing, validate_routing_contribution
from mote_kernel.execution.errors import (
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    UnknownRouteError,
)
from mote_kernel.execution.graph import END, GraphNodeId, GraphRouteId
from mote_kernel.state.graph_state import (
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    GraphJoinProgress,
    GraphRoutingContribution,
    SelectGraphRoute,
)


def continue_for(*node_ids: str):
    return tuple((GraphNodeId(node_id), ContinueGraphRouting()) for node_id in node_ids)


def test_direct_conditional_and_terminal_routing_use_one_contribution_model() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "b"), conditional("a", "optional", "c")),
    )
    selected = SelectGraphRoute(GraphRouteId("optional"))

    assert resolve_routing(graph, ((GraphNodeId("a"), selected),), ()) == AdvanceGraphFrontier(
        (GraphNodeId("b"), GraphNodeId("c")), ()
    )
    assert resolve_routing(topology("a"), continue_for("a"), ()) == CompleteGraphFrontier()


def test_direct_fanout_is_sorted_and_deduplicated() -> None:
    graph = topology("a", "b", "c", edges=(direct("a", "c"), direct("a", "b")))
    assert resolve_routing(graph, continue_for("a"), ()) == AdvanceGraphFrontier(
        (GraphNodeId("b"), GraphNodeId("c")), ()
    )


def test_conditional_route_selects_exact_target() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(conditional("a", "left", "b"), conditional("a", "right", "c")),
    )
    assert resolve_routing(
        graph,
        ((GraphNodeId("a"), SelectGraphRoute(GraphRouteId("right"))),),
        (),
    ) == AdvanceGraphFrontier((GraphNodeId("c"),), ())


def test_routing_validator_rejects_topology_incompatible_contribution() -> None:
    conditional_graph = topology("a", "b", edges=(conditional("a", "known", "b"),))
    with pytest.raises(InvalidRoutingCommandError, match="select"):
        validate_routing_contribution(conditional_graph, GraphNodeId("a"), ContinueGraphRouting())
    with pytest.raises(UnknownRouteError):
        validate_routing_contribution(
            conditional_graph,
            GraphNodeId("a"),
            SelectGraphRoute(GraphRouteId("unknown")),
        )
    with pytest.raises(InvalidRoutingCommandError, match="non-conditional"):
        validate_routing_contribution(
            topology("a"),
            GraphNodeId("a"),
            SelectGraphRoute(GraphRouteId("route")),
        )
    with pytest.raises(InvalidRoutingCommandError, match="unknown node"):
        validate_routing_contribution(topology("a"), GraphNodeId("foreign"), ContinueGraphRouting())
    with pytest.raises(InvalidRoutingCommandError, match="unsupported variant"):
        validate_routing_contribution(
            topology("a"),
            GraphNodeId("a"),
            cast(GraphRoutingContribution, object()),
        )


def test_join_fires_only_after_all_sources_arrive_across_supersteps() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "work",
        edges=(direct("a", "work"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )

    first = resolve_routing(graph, continue_for("a"), ())
    assert isinstance(first, AdvanceGraphFrontier)
    assert isinstance(first, AdvanceGraphFrontier)
    assert first == AdvanceGraphFrontier(
        (GraphNodeId("work"),),
        (
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("b")),
                GraphNodeId("c"),
                frozenset({GraphNodeId("a")}),
            ),
        ),
    )
    second = resolve_routing(graph, continue_for("b"), first.join_progress)
    assert second == AdvanceGraphFrontier((GraphNodeId("c"),), ())


def test_one_source_can_complete_multiple_joins_in_same_step() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        "e",
        edges=(join(("a", "b"), "d"), join(("a", "c"), "e")),
        entries=("a", "b", "c"),
    )
    assert resolve_routing(graph, continue_for("a", "b", "c"), ()) == AdvanceGraphFrontier(
        (GraphNodeId("d"), GraphNodeId("e")), ()
    )


def test_completed_joins_and_direct_arrivals_deduplicate_targets() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        edges=(direct("a", "d"), join(("a", "b"), "d"), join(("a", "c"), "d")),
        entries=("a", "b", "c"),
    )
    assert resolve_routing(graph, continue_for("a", "b", "c"), ()) == AdvanceGraphFrontier((GraphNodeId("d"),), ())


def test_direct_target_can_run_before_join_and_again_when_join_completes() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "c"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )
    first = resolve_routing(graph, continue_for("a"), ())
    assert isinstance(first, AdvanceGraphFrontier)
    assert first == AdvanceGraphFrontier(
        (GraphNodeId("c"),),
        (
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("b")),
                GraphNodeId("c"),
                frozenset({GraphNodeId("a")}),
            ),
        ),
    )
    assert resolve_routing(graph, continue_for("b"), first.join_progress) == AdvanceGraphFrontier(
        (GraphNodeId("c"),), ()
    )


def test_conditional_branch_can_supply_join_source() -> None:
    graph = topology(
        "start",
        "a",
        "b",
        "end",
        edges=(direct("start", "a"), conditional("start", "right", "b"), join(("a", "b"), "end")),
        entries=("start",),
    )
    first = resolve_routing(
        graph,
        ((GraphNodeId("start"), SelectGraphRoute(GraphRouteId("right"))),),
        (),
    )
    assert first == AdvanceGraphFrontier((GraphNodeId("a"), GraphNodeId("b")), ())
    assert resolve_routing(graph, continue_for("a", "b"), ()) == AdvanceGraphFrontier((GraphNodeId("end"),), ())


def test_chained_joins_advance_across_supersteps() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        "e",
        edges=(join(("a", "b"), "c"), direct("c", "d"), join(("c", "d"), "e")),
        entries=("a", "b"),
    )
    first = resolve_routing(graph, continue_for("a", "b"), ())
    assert first == AdvanceGraphFrontier((GraphNodeId("c"),), ())
    second = resolve_routing(graph, continue_for("c"), ())
    assert isinstance(second, AdvanceGraphFrontier)
    assert second == AdvanceGraphFrontier(
        (GraphNodeId("d"),),
        (
            GraphJoinProgress(
                (GraphNodeId("c"), GraphNodeId("d")),
                GraphNodeId("e"),
                frozenset({GraphNodeId("c")}),
            ),
        ),
    )
    assert resolve_routing(graph, continue_for("d"), second.join_progress) == AdvanceGraphFrontier(
        (GraphNodeId("e"),), ()
    )


def test_join_can_fire_again_after_prior_progress_was_consumed() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    expected = AdvanceGraphFrontier((GraphNodeId("c"),), ())
    assert resolve_routing(graph, continue_for("a", "b"), ()) == expected
    assert resolve_routing(graph, continue_for("a", "b"), ()) == expected


def test_join_progress_survives_unrelated_supersteps() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        edges=(direct("a", "b"), direct("b", "c"), join(("a", "c"), "d")),
    )
    first = resolve_routing(graph, continue_for("a"), ())
    assert isinstance(first, AdvanceGraphFrontier)
    second = resolve_routing(graph, continue_for("b"), first.join_progress)
    assert isinstance(second, AdvanceGraphFrontier)
    third = resolve_routing(graph, continue_for("c"), second.join_progress)
    assert second.join_progress == first.join_progress
    assert third == AdvanceGraphFrontier((GraphNodeId("d"),), ())


def test_repeated_incomplete_join_arrival_is_idempotent() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "a"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    )
    assert resolve_routing(graph, continue_for("a"), (progress,)) == AdvanceGraphFrontier(
        (GraphNodeId("a"),), (progress,)
    )


def test_persisted_join_progress_order_does_not_change_decision() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        "e",
        edges=(direct("a", "a"), join(("a", "b"), "d"), join(("a", "c"), "e")),
        entries=("a", "b", "c"),
    )
    first = GraphJoinProgress((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("d"), frozenset({GraphNodeId("a")}))
    second = GraphJoinProgress((GraphNodeId("a"), GraphNodeId("c")), GraphNodeId("e"), frozenset({GraphNodeId("a")}))
    assert resolve_routing(graph, continue_for("a"), (first, second)) == resolve_routing(
        graph, continue_for("a"), (second, first)
    )


def test_same_step_join_to_end_completes_and_self_loop_reactivates_node() -> None:
    joined = topology("a", "b", edges=(join(("a", "b"), END),), entries=("a", "b"))
    assert resolve_routing(joined, continue_for("a", "b"), ()) == CompleteGraphFrontier()
    loop = topology("a", edges=(direct("a", "a"),))
    assert resolve_routing(loop, continue_for("a"), ()) == AdvanceGraphFrontier((GraphNodeId("a"),), ())


def test_partial_join_without_continuing_work_is_deadlocked() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    with pytest.raises(RoutingDeadlockError):
        resolve_routing(graph, continue_for("a"), ())


@pytest.mark.parametrize(
    "progress",
    [
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("c"),
            frozenset(),
        ),
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("c"),
            frozenset({GraphNodeId("a"), GraphNodeId("b")}),
        ),
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("unknown"),
            frozenset({GraphNodeId("a")}),
        ),
    ],
)
def test_invalid_recovered_join_progress_fails_closed(progress: GraphJoinProgress) -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("b", "b"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )
    with pytest.raises(JoinProgressError):
        resolve_routing(graph, continue_for("b"), (progress,))


def test_duplicate_recovered_join_progress_fails_closed() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("b", "b"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        frozenset({GraphNodeId("a")}),
    )
    with pytest.raises(JoinProgressError):
        resolve_routing(graph, continue_for("b"), (progress, progress))


def test_routing_resolution_is_immutable() -> None:
    resolution = resolve_routing(
        topology("a", "b", edges=(direct("a", "b"),)),
        continue_for("a"),
        (),
    )
    assert isinstance(resolution, AdvanceGraphFrontier)

    with pytest.raises(FrozenInstanceError):
        resolution.node_ids = ()  # type: ignore[misc]
