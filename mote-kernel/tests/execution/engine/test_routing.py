# pyright: reportPrivateUsage=false

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest
from tests.execution.engine.factories import conditional, direct, join, running_state, topology

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.routing import (
    _frontier_gate_error,
    _gate_matches_cause,
    _join_arrivals_for_frontier,
    _post_advance_error,
    frontier_admission_error,
    resolve_routing,
    resolve_routing_facts,
    validate_routing_contribution,
)
from mote_kernel.execution.errors import (
    GraphValidationError,
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    UnknownRouteError,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    FeedbackInputBinding,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.topology import CompiledGraph, frozen_map
from mote_kernel.execution.graph.values import _make_node_output_frame
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import StableActivation, root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ActivationReference,
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphAbortReason,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierActivation,
    GraphFrontierNode,
    GraphFrontierState,
    GraphJoinProgress,
    GraphNodeId,
    GraphRouteId,
    GraphRoutingContribution,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    RoutedActivationCause,
    SelectGraphRoute,
    SettleGraphNode,
    StartActivationCause,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    reduce_graph_run,
)


def test_selected_control_target_with_missing_input_aborts_before_advance() -> None:
    graph = topology("source", "target", edges=(direct("source", "target"),))
    state = running_state(frontier=("source",))
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("source"), SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()
                ),
            )
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
    activations: tuple[GraphFrontierActivation, ...],
    join_progress: tuple[GraphJoinProgress, ...] = (),
) -> AdvanceGraphFrontier:
    return AdvanceGraphFrontier(0, activations, join_progress)


def reference(
    node_id: str,
    *,
    superstep: int = 0,
    route: str | None = None,
    run_id: str = "run",
) -> ActivationReference:
    return ActivationReference(
        GraphActivationIdentity(GraphRunId(run_id), superstep, GraphNodeId(node_id)),
        GraphRouteId(route) if route is not None else None,
    )


def _allow_frontier_admission(_graph: CompiledGraph[str], _state: GraphRunState) -> str | None:
    return None


def routed(
    node_id: str,
    *references: ActivationReference,
) -> GraphFrontierActivation:
    return GraphFrontierActivation(GraphNodeId(node_id), RoutedActivationCause(references))


def expected_complete() -> CompleteGraphFrontier:
    return CompleteGraphFrontier(0)


def continue_for(*node_ids: str) -> tuple[tuple[GraphNodeId, GraphRoutingContribution], ...]:
    return tuple((GraphNodeId(node_id), ContinueGraphRouting()) for node_id in node_ids)


def resolve_contributions(
    graph: CompiledGraph[str],
    contributions: tuple[tuple[GraphNodeId, GraphRoutingContribution], ...],
    join_progress: tuple[GraphJoinProgress, ...],
    *,
    superstep: int = 0,
    activations: tuple[GraphFrontierActivation, ...] | None = None,
    settled_activations: tuple[ActivationReference, ...] | None = None,
):
    if activations is None:
        activations = tuple(
            GraphFrontierActivation(
                node_id,
                StartActivationCause()
                if superstep == 0
                else RoutedActivationCause((reference(node_id, superstep=superstep - 1),)),
            )
            for node_id, _contribution in contributions
        )
    current_references = tuple(
        ActivationReference(
            GraphActivationIdentity(GraphRunId("run"), superstep, node_id),
            contribution.route if isinstance(contribution, SelectGraphRoute) else None,
        )
        for node_id, contribution in contributions
    )
    cause_references = tuple(
        reference
        for activation in activations
        if isinstance(activation.cause, RoutedActivationCause)
        for reference in activation.cause.references
    )
    evidence = tuple(
        sorted(
            set(current_references)
            | set(cause_references)
            | {reference for progress in join_progress for reference in progress.arrived}
            | set(settled_activations or ()),
            key=ActivationReference.canonical_key,
        )
    )
    state = running_state(
        run_id="run",
        superstep=superstep,
        frontier=tuple(node_id for node_id, _contribution in contributions),
        join_progress=join_progress,
    )
    state = replace(
        state,
        frontier=GraphFrontierState(
            tuple(
                GraphFrontierNode(node_id, SucceededGraphNode(contribution), activation.cause)
                for (node_id, contribution), activation in zip(contributions, activations, strict=True)
            )
        ),
        settled_activations=evidence,
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


def feedback_graph() -> CompiledGraph[int]:
    async def loop(values: Graph.Values[int]) -> Graph.Values[int]:
        return values

    seed = Graph.graph_input("seed", int)
    node = CallableNodeDefinition(
        GraphNodeId("loop"),
        loop,
        normalize_input_bindings({"value": FeedbackInputBinding(seed, Graph.node_output("loop", "value"))}),
        normalize_output_declarations({"value": int}),
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.routing"),
            GraphDefinitionVersion(1),
            (node,),
            (
                ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
            ),
            (GraphNodeId("loop"),),
            normalize_graph_output_declarations({"value": Graph.node_output("loop", "value")}),
        )
    )


def settled_feedback_graph(
    graph: CompiledGraph[int],
    route: str,
) -> tuple[GraphRunState, ScopedFrameIndex[int]]:
    run_id = GraphRunId("run")
    scope_run = root_scope_run(run_id)
    state = reduce_graph_run(None, project_start_graph_command(graph, run_id))
    state = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("attempt"), None),
    )
    assert state.execution is not None
    state = reduce_graph_run(
        state,
        SettleGraphNode(
            state.revision,
            state.execution.token,
            SucceededGraphNodeOutcome(
                GraphNodeId("loop"),
                SelectGraphRoute(GraphRouteId(route)),
            ),
        ),
    )
    frames: ScopedFrameIndex[int] = ScopedFrameIndex()
    frames = frames.add_graph_input(
        AdmittedGraphInput(
            GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
            admit_graph_input(graph, Graph.values(seed=3)),
        )
    )
    output_descriptor = graph.transition.publications[GraphNodeId("loop")]
    output = _make_node_output_frame(Graph.values(value=4), output_descriptor.declarations)
    frames = frames.add_publication(
        ConfirmedPublication(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, 0, GraphNodeId("loop")),
                output_descriptor.identity,
            ),
            output,
            state.revision,
            ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))),
        )
    )
    return state, frames


def test_direct_conditional_and_terminal_routing_use_one_contribution_model() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "b"), conditional("a", "optional", "c")),
    )
    selected = SelectGraphRoute(GraphRouteId("optional"))

    assert resolve_contributions(graph, ((GraphNodeId("a"), selected),), ()) == expected_advance(
        (
            routed("b", reference("a", route="optional")),
            routed("c", reference("a", route="optional")),
        )
    )
    assert resolve_contributions(topology("a"), continue_for("a"), ()) == expected_complete()


def test_self_feedback_route_emits_one_exact_predecessor_activation() -> None:
    graph = feedback_graph()
    state, frames = settled_feedback_graph(graph, "continue")

    command = resolve_routing(graph, state, root_scope_run(state.run_id), frames)

    assert isinstance(command, AdvanceGraphFrontier)
    assert command.activations == (
        GraphFrontierActivation(
            GraphNodeId("loop"),
            RoutedActivationCause(
                (
                    ActivationReference(
                        GraphActivationIdentity(GraphRunId("run"), 0, GraphNodeId("loop")),
                        GraphRouteId("continue"),
                    ),
                )
            ),
        ),
    )


def test_self_feedback_terminal_route_completes_without_a_new_activation() -> None:
    graph = feedback_graph()
    state, frames = settled_feedback_graph(graph, "done")

    command = resolve_routing(graph, state, root_scope_run(state.run_id), frames)

    assert command == CompleteGraphFrontier(state.revision)


def test_self_feedback_rejects_a_source_with_a_forged_start_cause_after_round_zero() -> None:
    graph = feedback_graph()
    state, frames = settled_feedback_graph(graph, "continue")
    node = state.frontier.nodes[0]
    forged = replace(
        state,
        superstep=1,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    node.node_id,
                    node.settlement,
                    StartActivationCause(),
                ),
            )
        ),
    )

    with pytest.raises(InvalidRoutingCommandError, match="predecessor activation cause"):
        resolve_routing(graph, forged, root_scope_run(forged.run_id), frames)


@pytest.mark.parametrize(
    ("run_id", "superstep", "route", "match"),
    [
        ("other", 1, "continue", "immediate predecessor"),
        ("run", 1, "other", "immediate predecessor"),
        ("run", 2, "continue", "immediate predecessor"),
    ],
)
def test_self_feedback_rejects_a_forged_routed_predecessor(
    run_id: str,
    superstep: int,
    route: str,
    match: str,
) -> None:
    graph = feedback_graph()
    state, frames = settled_feedback_graph(graph, "continue")
    forged = replace(
        state,
        superstep=superstep,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("loop"),
                    state.frontier.nodes[0].settlement,
                    RoutedActivationCause(
                        (
                            ActivationReference(
                                GraphActivationIdentity(GraphRunId(run_id), 0, GraphNodeId("loop")),
                                GraphRouteId(route),
                            ),
                        )
                    ),
                ),
            )
        ),
    )

    with pytest.raises(InvalidRoutingCommandError, match=match):
        resolve_routing(graph, forged, root_scope_run(forged.run_id), frames)


def test_routing_never_resolves_a_failed_feedback_frontier() -> None:
    graph = feedback_graph()
    state, frames = settled_feedback_graph(graph, "continue")
    failed = replace(
        state,
        status=GraphRunStatus.FAILED,
        execution=None,
        frontier=GraphFrontierState(
            (GraphFrontierNode(GraphNodeId("loop"), FailedGraphNode(GraphFailure("declined")), StartActivationCause()),)
        ),
    )

    with pytest.raises(InvalidRoutingCommandError, match="settled frontier"):
        resolve_routing(graph, failed, root_scope_run(failed.run_id), frames)


def test_direct_fanout_activations_are_sorted_and_keep_the_source_cause() -> None:
    graph = topology("a", "b", "c", edges=(direct("a", "c"), direct("a", "b")))
    assert resolve_contributions(graph, continue_for("a"), ()) == expected_advance(
        (routed("b", reference("a")), routed("c", reference("a")))
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
    ) == expected_advance((routed("c", reference("a", route="right")),))


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
        edges=(direct("a", "work"), direct("a", "b"), join(("a", "b"), "c")),
        entries=("a",),
    )

    first = resolve_contributions(graph, continue_for("a"), ())
    assert isinstance(first, AdvanceGraphFrontier)
    assert first == expected_advance(
        (routed("b", reference("a")), routed("work", reference("a"))),
        (
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("b")),
                GraphNodeId("c"),
                (reference("a"),),
            ),
        ),
    )
    second = resolve_contributions(
        graph,
        continue_for("b", "work"),
        first.join_progress,
        superstep=1,
        activations=(routed("b", reference("a")), routed("work", reference("a"))),
        settled_activations=(
            reference("a"),
            reference("b", superstep=1),
            reference("work", superstep=1),
        ),
    )
    assert second == expected_advance((routed("c", reference("a"), reference("b", superstep=1)),))


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
        (
            routed("d", reference("a"), reference("b")),
            routed("e", reference("a"), reference("c")),
        )
    )


def test_independent_direct_and_join_causes_for_one_target_are_rejected() -> None:
    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        topology(
            "a",
            "b",
            "c",
            "d",
            edges=(direct("a", "d"), join(("a", "b"), "d"), join(("a", "c"), "d")),
            entries=("a", "b", "c"),
        )


def test_direct_target_can_run_before_join_and_again_when_join_completes() -> None:
    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        topology(
            "a",
            "b",
            "c",
            edges=(direct("a", "c"), join(("a", "b"), "c")),
            entries=("a", "b"),
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
    assert first == expected_advance(
        (
            routed("a", reference("start", route="right")),
            routed("b", reference("start", route="right")),
        )
    )
    assert resolve_contributions(
        graph,
        continue_for("a", "b"),
        (),
        superstep=1,
        activations=(
            routed("a", reference("start", route="right")),
            routed("b", reference("start", route="right")),
        ),
        settled_activations=(
            reference("start", route="right"),
            reference("a", superstep=1),
            reference("b", superstep=1),
        ),
    ) == expected_advance((routed("end", reference("a", superstep=1), reference("b", superstep=1)),))


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
    assert first == expected_advance((routed("c", reference("a"), reference("b")),))
    second = resolve_contributions(
        graph,
        continue_for("c"),
        (),
        superstep=1,
        activations=(routed("c", reference("a"), reference("b")),),
        settled_activations=(reference("a"), reference("b"), reference("c", superstep=1)),
    )
    assert isinstance(second, AdvanceGraphFrontier)
    assert second == expected_advance(
        (routed("d", reference("c", superstep=1)),),
        (
            GraphJoinProgress(
                (GraphNodeId("c"), GraphNodeId("d")),
                GraphNodeId("e"),
                (reference("c", superstep=1),),
            ),
        ),
    )
    assert resolve_contributions(
        graph,
        continue_for("d"),
        second.join_progress,
        superstep=2,
        activations=(routed("d", reference("c", superstep=1)),),
        settled_activations=(
            reference("a"),
            reference("b"),
            reference("c", superstep=1),
            reference("d", superstep=2),
        ),
    ) == expected_advance((routed("e", reference("c", superstep=1), reference("d", superstep=2)),))


def test_completed_join_emits_one_activation_with_all_arrival_references() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    assert resolve_contributions(graph, continue_for("a", "b"), ()) == expected_advance(
        (routed("c", reference("a"), reference("b")),)
    )


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
    second = resolve_contributions(
        graph,
        continue_for("b"),
        first.join_progress,
        superstep=1,
        activations=(routed("b", reference("a")),),
        settled_activations=(reference("a"), reference("b", superstep=1)),
    )
    assert isinstance(second, AdvanceGraphFrontier)
    third = resolve_contributions(
        graph,
        continue_for("c"),
        second.join_progress,
        superstep=2,
        activations=(routed("c", reference("b", superstep=1)),),
        settled_activations=(reference("a"), reference("b", superstep=1), reference("c", superstep=2)),
    )
    assert second.join_progress == first.join_progress
    assert third == expected_advance((routed("d", reference("a"), reference("c", superstep=2)),))


def test_persisted_join_progress_order_does_not_change_decision() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "d",
        "e",
        edges=(direct("a", "b"), direct("a", "c"), join(("a", "b"), "d"), join(("a", "c"), "e")),
        entries=("a",),
    )
    first = GraphJoinProgress((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("d"), (reference("a"),))
    second = GraphJoinProgress((GraphNodeId("a"), GraphNodeId("c")), GraphNodeId("e"), (reference("a"),))
    activations = (routed("b", reference("a")), routed("c", reference("a")))
    evidence = (reference("a"), reference("b", superstep=1), reference("c", superstep=1))
    assert resolve_contributions(
        graph,
        continue_for("b", "c"),
        (first, second),
        superstep=1,
        activations=activations,
        settled_activations=evidence,
    ) == resolve_contributions(
        graph,
        continue_for("b", "c"),
        (second, first),
        superstep=1,
        activations=activations,
        settled_activations=evidence,
    )


def test_same_step_join_to_end_completes_and_self_loop_reactivates_node() -> None:
    joined = topology("a", "b", edges=(join(("a", "b"), END),), entries=("a", "b"))
    assert resolve_contributions(joined, continue_for("a", "b"), ()) == expected_complete()
    # A control loop is only valid when the compiled topology also exposes a
    # normal exit; the direct END edge keeps this regression about reactivation
    # rather than relying on the execution limit as completion.
    loop = topology("a", edges=(direct("a", "a"), direct("a", END)))
    assert resolve_contributions(loop, continue_for("a"), ()) == expected_advance((routed("a", reference("a")),))


def test_terminal_join_completed_after_a_later_superstep_carries_consumption_proof() -> None:
    graph = topology(
        "a",
        "b",
        "x",
        "middle",
        "y",
        edges=(
            direct("a", "x"),
            direct("b", "middle"),
            direct("middle", "y"),
            join(("x", "y"), END),
        ),
        entries=("a", "b"),
    )

    first = resolve_contributions(graph, continue_for("a", "b"), ())
    assert isinstance(first, AdvanceGraphFrontier)
    second = resolve_contributions(
        graph,
        continue_for("x", "middle"),
        first.join_progress,
        superstep=1,
        activations=(routed("x", reference("a")), routed("middle", reference("b"))),
        settled_activations=(
            reference("a"),
            reference("b"),
            reference("x", superstep=1),
            reference("middle", superstep=1),
        ),
    )
    assert isinstance(second, AdvanceGraphFrontier)
    terminal = resolve_contributions(
        graph,
        continue_for("y"),
        second.join_progress,
        superstep=2,
        activations=(routed("y", reference("middle", superstep=1)),),
        settled_activations=(
            reference("a"),
            reference("b"),
            reference("x", superstep=1),
            reference("middle", superstep=1),
            reference("y", superstep=2),
        ),
    )

    assert terminal == CompleteGraphFrontier(
        0,
        (((GraphNodeId("x"), GraphNodeId("y")), GraphNodeId(END)),),
    )


def test_join_rejects_a_source_with_two_coexisting_activation_paths() -> None:
    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        topology(
            "a",
            "b",
            "x",
            "d",
            "c",
            "y",
            "z",
            edges=(
                direct("a", "x"),
                direct("b", "d"),
                direct("d", "x"),
                direct("b", "c"),
                direct("c", "y"),
                join(("x", "y"), "z"),
            ),
            entries=("a", "b"),
        )


def test_partial_join_without_continuing_work_is_deadlocked() -> None:
    graph = topology("a", "b", "c", "d", edges=(join(("a", "b", "c"), "d"),), entries=("a", "b", "c"))
    with pytest.raises(RoutingDeadlockError):
        resolve_contributions(graph, continue_for("a"), ())


@pytest.mark.parametrize(
    "progress",
    [
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("c"),
            (),
        ),
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("c"),
            (reference("a"), reference("b")),
        ),
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("unknown"),
            (reference("a"),),
        ),
    ],
)
def test_invalid_recovered_join_progress_fails_closed(progress: GraphJoinProgress) -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "work",
        edges=(direct("a", "b"), direct("b", "work"), join(("a", "b"), "c")),
        entries=("a",),
    )
    with pytest.raises((JoinProgressError, InvalidRoutingCommandError)):
        resolve_contributions(
            graph,
            continue_for("b"),
            (progress,),
            superstep=1,
            activations=(routed("b", reference("a")),),
            settled_activations=(reference("a"), reference("b", superstep=1)),
        )


def test_duplicate_recovered_join_progress_fails_closed() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "work",
        edges=(direct("a", "b"), direct("b", "work"), join(("a", "b"), "c")),
        entries=("a",),
    )
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        (reference("a"),),
    )
    with pytest.raises((JoinProgressError, InvalidRoutingCommandError)):
        resolve_contributions(
            graph,
            continue_for("b"),
            (progress, progress),
            superstep=1,
            activations=(routed("b", reference("a")),),
            settled_activations=(reference("a"), reference("b", superstep=1)),
        )


def test_routing_command_is_immutable() -> None:
    command = resolve_contributions(
        topology("a", "b", edges=(direct("a", "b"),)),
        continue_for("a"),
        (),
    )
    assert isinstance(command, AdvanceGraphFrontier)

    with pytest.raises(FrozenInstanceError):
        command.activations = ()  # type: ignore[misc]


def _settled_routing_state(
    node_id: str,
    cause: object,
    *,
    superstep: int = 0,
    evidence: tuple[ActivationReference, ...] = (),
) -> GraphRunState:
    base = running_state(superstep=superstep, frontier=(node_id,))
    return replace(
        base,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId(node_id),
                    SucceededGraphNode(ContinueGraphRouting()),
                    cast(StartActivationCause | RoutedActivationCause, cause),
                ),
            )
        ),
        settled_activations=evidence,
    )


def test_frontier_admission_rejects_unknown_and_provenance_inconsistencies() -> None:
    unknown_graph = topology("a")
    assert _frontier_gate_error(unknown_graph, _settled_routing_state("foreign", StartActivationCause())) == (
        "frontier activation references unknown node 'foreign'"
    )

    entry_graph = topology("a", "b", edges=(direct("a", "b"),), entries=("a",))
    assert _frontier_gate_error(entry_graph, _settled_routing_state("b", StartActivationCause())) == (
        "START activation 'b' is not a compiled graph entry"
    )

    unsupported = _settled_routing_state("a", object())
    assert _frontier_gate_error(unknown_graph, unsupported) == "frontier activation has an unsupported cause"

    routed_graph = topology("a", "b", edges=(direct("a", "b"),), entries=("a",))
    mismatched = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("foreign"),)),
        superstep=1,
        evidence=(reference("foreign"),),
    )
    assert _frontier_gate_error(routed_graph, mismatched) == (
        "frontier activation 'b' does not match exactly one compiled activation gate"
    )

    missing_evidence = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
    )
    assert _frontier_gate_error(routed_graph, missing_evidence) == (
        "frontier activation 'b' lacks committed predecessor settlement evidence"
    )


def test_frontier_admission_rejects_a_ghost_settled_activation_without_keyerror() -> None:
    graph = topology("a", "b", edges=(direct("a", "b"),), entries=("a",))
    state = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
        evidence=(reference("a"), reference("ghost")),
    )

    assert frontier_admission_error(graph, state) == "settled activation references unknown node 'ghost'"
    assert _post_advance_error(graph, state) == "settled activation references unknown node 'ghost'"


def test_feedback_admission_requires_committed_predecessor_evidence() -> None:
    graph = feedback_graph()
    state, _frames = settled_feedback_graph(graph, "continue")
    candidate = replace(
        state,
        superstep=1,
        settled_activations=(),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("loop"),
                    SucceededGraphNode(SelectGraphRoute(GraphRouteId("continue"))),
                    RoutedActivationCause((reference("loop", route="continue"),)),
                ),
            )
        ),
    )

    assert _frontier_gate_error(graph, candidate) == (
        "feedback activation predecessor lacks committed settlement evidence"
    )


def test_gate_matching_rejects_a_reference_count_mismatch() -> None:
    cause = RoutedActivationCause((reference("a"), reference("b")))
    assert not _gate_matches_cause(((GraphNodeId("a"), frozenset({None})),), cause)


@pytest.mark.parametrize(
    ("graph", "evidence", "message"),
    [
        (
            topology("a", "b", edges=(direct("a", "b"),), entries=("a",)),
            (),
            "non-initial frontier has no committed predecessor settlements",
        ),
        (
            topology("a", "b", edges=(conditional("a", "left", "b"),), entries=("a",)),
            (reference("a"),),
            "conditional predecessor settlement lacks its selected route",
        ),
        (
            topology("a", "b", edges=(conditional("a", "left", "b"),), entries=("a",)),
            (reference("a", route="unknown"),),
            "predecessor settlement selected an unknown route",
        ),
    ],
)
def test_post_advance_rejects_missing_or_invalid_predecessor_route_facts(
    graph: CompiledGraph[str],
    evidence: tuple[ActivationReference, ...],
    message: str,
) -> None:
    state = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
        evidence=evidence,
    )
    if message.startswith("predecessor settlement selected"):
        state = replace(
            state,
            frontier=GraphFrontierState(
                (
                    GraphFrontierNode(
                        GraphNodeId("b"),
                        SucceededGraphNode(ContinueGraphRouting()),
                        RoutedActivationCause((reference("a", route="unknown"),)),
                    ),
                )
            ),
        )
    assert _post_advance_error(graph, state) == message


def test_post_advance_accepts_a_terminal_conditional_route() -> None:
    graph = topology("a", edges=(conditional("a", "done", END),), entries=("a",))
    state = _settled_routing_state(
        "a",
        RoutedActivationCause((reference("a", route="done"),)),
        superstep=1,
        evidence=(reference("a", route="done"),),
    )
    assert _post_advance_error(graph, replace(state, frontier=GraphFrontierState(()))) is None


def test_post_advance_rejects_duplicate_join_occurrences_and_accepts_terminal_join() -> None:
    join_graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    duplicate = _settled_routing_state(
        "c",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
        evidence=(reference("a"),),
    )
    duplicate = replace(
        duplicate,
        join_progress=(
            GraphJoinProgress(
                (GraphNodeId("a"), GraphNodeId("b")),
                GraphNodeId("c"),
                (reference("a"), reference("a")),
            ),
        ),
    )
    assert _post_advance_error(join_graph, duplicate) == "Join source activation occurrence repeated"

    terminal_graph = topology("a", "b", edges=(join(("a", "b"), END),), entries=("a", "b"))
    terminal = replace(
        duplicate,
        frontier=GraphFrontierState(()),
        join_progress=(),
        settled_activations=(reference("a"), reference("b")),
    )
    assert _post_advance_error(terminal_graph, terminal) is None


def test_post_advance_rejects_unmatched_and_unexpected_successors() -> None:
    direct_graph = topology("a", "b", "c", edges=(direct("a", "b"),), entries=("a",))
    mismatch = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("c"),)),
        superstep=1,
        evidence=(reference("a"), reference("c")),
    )
    assert _post_advance_error(direct_graph, mismatch) == (
        "frontier target 'b' does not match its compiled successor cause"
    )

    unexpected_graph = topology("a", "c", entries=("a", "c"))
    unexpected = _settled_routing_state(
        "c",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
        evidence=(reference("a"),),
    )
    assert _post_advance_error(unexpected_graph, unexpected) == "frontier contains an unproved successor target: ('c',)"


def test_join_arrival_rejects_two_routes_for_one_activation() -> None:
    edge = join(("a", "b"), "c")
    with pytest.raises(JoinProgressError, match="selected two routes"):
        _join_arrivals_for_frontier(
            edge,
            None,
            (reference("a", route="left"), reference("a", route="right")),
        )


def test_routing_snapshot_rejects_duplicate_join_progress_sources_before_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.engine.routing as routing

    graph = topology("a", "b", "c", "d", edges=(join(("a", "b", "c"), "d"),), entries=("a", "b", "c"))
    state = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
        evidence=(reference("a"), reference("b")),
    )
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b"), GraphNodeId("c")),
        GraphNodeId("d"),
        (reference("a"), reference("a")),
    )
    monkeypatch.setattr(routing, "frontier_admission_error", _allow_frontier_admission)

    with pytest.raises(JoinProgressError, match="repeats one source activation"):
        resolve_routing_facts(
            graph,
            replace(state, join_progress=(progress,)),
            root_scope_run(state.run_id),
            ScopedFrameIndex(),
        )


def test_routing_snapshot_rejects_join_progress_without_settlement_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.engine.routing as routing

    graph = topology(
        "a",
        "b",
        "c",
        "d",
        edges=(join(("a", "b", "c"), "d"),),
        entries=("a", "b", "c"),
    )
    state = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
        evidence=(reference("a"), reference("b")),
    )
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b"), GraphNodeId("c")),
        GraphNodeId("d"),
        (reference("c"),),
    )
    monkeypatch.setattr(routing, "frontier_admission_error", _allow_frontier_admission)

    with pytest.raises(JoinProgressError, match="lacks committed settlement evidence"):
        resolve_routing_facts(
            graph,
            replace(state, join_progress=(progress,)),
            root_scope_run(state.run_id),
            ScopedFrameIndex(),
        )


def test_routing_rejects_a_repeated_join_source_contribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.engine.routing as routing

    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    start = StartActivationCause()
    state = replace(
        running_state(frontier=("a",)),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(GraphNodeId("a"), SucceededGraphNode(ContinueGraphRouting()), start),
                GraphFrontierNode(GraphNodeId("a"), SucceededGraphNode(ContinueGraphRouting()), start),
            )
        ),
    )
    monkeypatch.setattr(routing, "frontier_admission_error", _allow_frontier_admission)

    with pytest.raises(JoinProgressError, match="repeated"):
        resolve_routing_facts(graph, state, root_scope_run(state.run_id), ScopedFrameIndex())


def test_routing_rejects_two_compiled_successor_causes_in_one_frontier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.engine.routing as routing

    base = topology("a", "b", "c", entries=("a", "b"))
    direct_targets = {node_id: tuple(targets) for node_id, targets in base.transition.direct_targets.entries}
    direct_targets[GraphNodeId("a")] = (GraphNodeId("c"),)
    direct_targets[GraphNodeId("b")] = (GraphNodeId("c"),)
    graph = replace(base, transition=replace(base.transition, direct_targets=frozen_map(direct_targets)))
    state = replace(
        running_state(frontier=("a", "b")),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(GraphNodeId("a"), SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),
                GraphFrontierNode(GraphNodeId("b"), SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),
            )
        ),
    )
    monkeypatch.setattr(routing, "frontier_admission_error", _allow_frontier_admission)

    with pytest.raises(InvalidRoutingCommandError, match="activation causes"):
        resolve_routing_facts(graph, state, root_scope_run(state.run_id), ScopedFrameIndex())


def test_post_advance_rejects_multiple_compiled_causes_for_one_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.engine.routing as routing

    base = topology("a", "b", "c", entries=("a", "b"))
    direct_targets = {node_id: tuple(targets) for node_id, targets in base.transition.direct_targets.entries}
    direct_targets[GraphNodeId("a")] = (GraphNodeId("c"),)
    direct_targets[GraphNodeId("b")] = (GraphNodeId("c"),)
    graph = replace(base, transition=replace(base.transition, direct_targets=frozen_map(direct_targets)))
    state = _settled_routing_state(
        "c",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
        evidence=(reference("a"), reference("b")),
    )
    monkeypatch.setattr(routing, "frontier_admission_error", _allow_frontier_admission)

    assert _post_advance_error(graph, state) == "target 'c' has 2 compiled activation causes"
