from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest
from tests.execution.engine.factories import conditional, direct, join, running_state, topology

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.routing import resolve_routing, validate_routing_contribution
from mote_kernel.execution.errors import (
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    UnknownRouteError,
)
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.identity import root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    GraphInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    GraphAbortReason,
    GraphFrontierNode,
    GraphFrontierState,
    GraphJoinProgress,
    GraphNodeId,
    GraphRouteId,
    GraphRoutingContribution,
    GraphRunId,
    SelectGraphRoute,
    SucceededGraphNode,
)


def test_selected_control_target_with_missing_input_aborts_before_advance() -> None:
    graph = topology("source", "target", edges=(direct("source", "target"),))
    state = running_state(frontier=("source",))
    state = replace(
        state,
        frontier=GraphFrontierState(
            (GraphFrontierNode(GraphNodeId("source"), SucceededGraphNode(ContinueGraphRouting())),)
        ),
    )

    command = resolve_routing(
        graph,
        state,
        root_scope_run(state.run_id),
        ScopedFrameIndex(),
    )

    assert command == AbortGraphRun(
        state.revision,
        GraphAbortReason("required values unavailable for controlled nodes ('target',)"),
    )


def expected_advance(
    node_ids: tuple[GraphNodeId, ...],
    join_progress: tuple[GraphJoinProgress, ...] = (),
) -> AdvanceGraphFrontier:
    return AdvanceGraphFrontier(0, node_ids, join_progress)


def expected_complete() -> CompleteGraphFrontier:
    return CompleteGraphFrontier(0)


def continue_for(*node_ids: str) -> tuple[tuple[GraphNodeId, GraphRoutingContribution], ...]:
    return tuple((GraphNodeId(node_id), ContinueGraphRouting()) for node_id in node_ids)


def resolve_contributions(
    graph: CompiledGraph[str],
    contributions: tuple[tuple[GraphNodeId, GraphRoutingContribution], ...],
    join_progress: tuple[GraphJoinProgress, ...],
):
    state = running_state(
        run_id="run",
        frontier=tuple(node_id for node_id, _contribution in contributions),
        join_progress=join_progress,
    )
    state = replace(
        state,
        frontier=GraphFrontierState(
            tuple(
                GraphFrontierNode(node_id, SucceededGraphNode(contribution)) for node_id, contribution in contributions
            )
        ),
    )
    scope_run = root_scope_run(GraphRunId("run"))
    frame = admit_graph_input(graph, Graph.values(value="input"))
    frames: ScopedFrameIndex[str] = ScopedFrameIndex()
    frames = frames.add_graph_input(
        AdmittedGraphInput(
            GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
            frame,
        )
    )
    return resolve_routing(graph, state, scope_run, frames)


def test_direct_conditional_and_terminal_routing_use_one_contribution_model() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "b"), conditional("a", "optional", "c")),
    )
    selected = SelectGraphRoute(GraphRouteId("optional"))

    assert resolve_contributions(graph, ((GraphNodeId("a"), selected),), ()) == expected_advance(
        (GraphNodeId("b"), GraphNodeId("c")), ()
    )
    assert resolve_contributions(topology("a"), continue_for("a"), ()) == expected_complete()


def test_direct_fanout_is_sorted_and_deduplicated() -> None:
    graph = topology("a", "b", "c", edges=(direct("a", "c"), direct("a", "b")))
    assert resolve_contributions(graph, continue_for("a"), ()) == expected_advance(
        (GraphNodeId("b"), GraphNodeId("c")), ()
    )


def test_conditional_route_selects_exact_target() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(conditional("a", "left", "b"), conditional("a", "right", "c")),
    )
    assert resolve_contributions(
        graph,
        ((GraphNodeId("a"), SelectGraphRoute(GraphRouteId("right"))),),
        (),
    ) == expected_advance((GraphNodeId("c"),), ())


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
    with pytest.raises(InvalidRoutingCommandError, match="non-conditional"):
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

    first = resolve_contributions(graph, continue_for("a"), ())
    assert isinstance(first, AdvanceGraphFrontier)
    assert first == expected_advance(
        (GraphNodeId("work"),),
        (
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("b")),
                GraphNodeId("c"),
                frozenset({GraphNodeId("a")}),
            ),
        ),
    )
    second = resolve_contributions(graph, continue_for("b"), first.join_progress)
    assert second == expected_advance((GraphNodeId("c"),), ())


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
    assert resolve_contributions(graph, continue_for("a", "b", "c"), ()) == expected_advance(
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
    assert resolve_contributions(graph, continue_for("a", "b", "c"), ()) == expected_advance((GraphNodeId("d"),), ())


def test_direct_target_can_run_before_join_and_again_when_join_completes() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "c"), join(("a", "b"), "c")),
        entries=("a", "b"),
    )
    first = resolve_contributions(graph, continue_for("a"), ())
    assert isinstance(first, AdvanceGraphFrontier)
    assert first == expected_advance(
        (GraphNodeId("c"),),
        (
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("b")),
                GraphNodeId("c"),
                frozenset({GraphNodeId("a")}),
            ),
        ),
    )
    assert resolve_contributions(graph, continue_for("b"), first.join_progress) == expected_advance(
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
    first = resolve_contributions(
        graph,
        ((GraphNodeId("start"), SelectGraphRoute(GraphRouteId("right"))),),
        (),
    )
    assert first == expected_advance((GraphNodeId("a"), GraphNodeId("b")), ())
    assert resolve_contributions(graph, continue_for("a", "b"), ()) == expected_advance((GraphNodeId("end"),), ())


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
    first = resolve_contributions(graph, continue_for("a", "b"), ())
    assert first == expected_advance((GraphNodeId("c"),), ())
    second = resolve_contributions(graph, continue_for("c"), ())
    assert isinstance(second, AdvanceGraphFrontier)
    assert second == expected_advance(
        (GraphNodeId("d"),),
        (
            GraphJoinProgress(
                (GraphNodeId("c"), GraphNodeId("d")),
                GraphNodeId("e"),
                frozenset({GraphNodeId("c")}),
            ),
        ),
    )
    assert resolve_contributions(graph, continue_for("d"), second.join_progress) == expected_advance(
        (GraphNodeId("e"),), ()
    )


def test_join_can_fire_again_after_prior_progress_was_consumed() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    expected = expected_advance((GraphNodeId("c"),), ())
    assert resolve_contributions(graph, continue_for("a", "b"), ()) == expected
    assert resolve_contributions(graph, continue_for("a", "b"), ()) == expected


def test_join_progress_survives_unrelated_supersteps() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        edges=(direct("a", "b"), direct("b", "c"), join(("a", "c"), "d")),
    )
    first = resolve_contributions(graph, continue_for("a"), ())
    assert isinstance(first, AdvanceGraphFrontier)
    second = resolve_contributions(graph, continue_for("b"), first.join_progress)
    assert isinstance(second, AdvanceGraphFrontier)
    third = resolve_contributions(graph, continue_for("c"), second.join_progress)
    assert second.join_progress == first.join_progress
    assert third == expected_advance((GraphNodeId("d"),), ())


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
    assert resolve_contributions(graph, continue_for("a"), (progress,)) == expected_advance(
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
    assert resolve_contributions(graph, continue_for("a"), (first, second)) == resolve_contributions(
        graph, continue_for("a"), (second, first)
    )


def test_same_step_join_to_end_completes_and_self_loop_reactivates_node() -> None:
    joined = topology("a", "b", edges=(join(("a", "b"), END),), entries=("a", "b"))
    assert resolve_contributions(joined, continue_for("a", "b"), ()) == expected_complete()
    loop = topology("a", edges=(direct("a", "a"),))
    assert resolve_contributions(loop, continue_for("a"), ()) == expected_advance((GraphNodeId("a"),), ())


def test_partial_join_without_continuing_work_is_deadlocked() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    with pytest.raises(RoutingDeadlockError):
        resolve_contributions(graph, continue_for("a"), ())


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
        resolve_contributions(graph, continue_for("b"), (progress,))


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
        resolve_contributions(graph, continue_for("b"), (progress, progress))


def test_routing_command_is_immutable() -> None:
    command = resolve_contributions(
        topology("a", "b", edges=(direct("a", "b"),)),
        continue_for("a"),
        (),
    )
    assert isinstance(command, AdvanceGraphFrontier)

    with pytest.raises(FrozenInstanceError):
        command.node_ids = ()  # type: ignore[misc]
