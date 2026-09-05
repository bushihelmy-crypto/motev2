from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import Protocol, TypeVar, cast

import pytest
from tests.execution.engine.factories import (
    conditional,
    direct,
    join,
    join_occurrence,
    join_progress,
    running_state,
    topology,
)
from tests.execution.graph.factories import compiled_join

import mote_kernel.execution.engine.routing as routing_module
from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.routing import (
    PublicationHistoryWindow,
    RequiredTarget,
    frontier_admission_error,
    predecessor_source_for_cause,
    publication_history_window,
    resolve_routing,
    resolve_routing_facts,
    transition_admission_error,
    validate_routing_contribution,
)
from mote_kernel.execution.errors import (
    GraphValidationError,
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    SnapshotMismatchError,
    UnknownRouteError,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    ActivationGate,
    CompiledPredecessorInput,
    NodeOutputPort,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.topology import CompiledGraph, CompiledJoin, frozen_map
from mote_kernel.execution.graph.values import _make_node_output_frame
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation, root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedFrameAvailability,
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
    GraphFailure,
    GraphFrontierActivation,
    GraphFrontierNode,
    GraphFrontierState,
    GraphJoinIdentity,
    GraphJoinOccurrenceIdentity,
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

GraphValueT = TypeVar("GraphValueT")


class _ControlResolutionView(Protocol):
    direct_targets: frozenset[GraphNodeId]
    join_targets: frozenset[GraphNodeId]
    remaining_join_progress: tuple[GraphJoinProgress, ...]
    activations: tuple[GraphFrontierActivation, ...]
    consumed_join_progress: tuple[GraphJoinOccurrenceIdentity, ...]


class _RoutingPrivateView(Protocol):
    _ControlResolution: Callable[..., _ControlResolutionView]
    _declared_joins: Callable[..., object]
    _frontier_gate_error: Callable[..., object]
    _gate_matches_cause: Callable[..., object]
    _historical_join_arrivals: Callable[..., object]
    _pending_join_arrivals: Callable[..., object]
    _post_advance_error: Callable[..., object]
    _required_target: Callable[..., object]

    @staticmethod
    def control_resolution(
        module: object,
        direct_targets: frozenset[GraphNodeId],
        join_targets: frozenset[GraphNodeId],
        remaining_join_progress: tuple[GraphJoinProgress, ...],
        activations: tuple[GraphFrontierActivation, ...],
        consumed_join_progress: tuple[GraphJoinOccurrenceIdentity, ...],
    ) -> _ControlResolutionView:
        view = cast(_RoutingPrivateView, module)
        return view._ControlResolution(
            direct_targets,
            join_targets,
            remaining_join_progress,
            activations,
            consumed_join_progress,
        )

    @staticmethod
    def declared_joins(module: object, graph: CompiledGraph[GraphValueT]) -> dict[GraphJoinIdentity, CompiledJoin]:
        function = cast(
            Callable[[CompiledGraph[GraphValueT]], dict[GraphJoinIdentity, CompiledJoin]],
            cast(_RoutingPrivateView, module)._declared_joins,
        )
        return function(graph)

    @staticmethod
    def frontier_gate_error(module: object, graph: CompiledGraph[GraphValueT], state: GraphRunState) -> str | None:
        function = cast(
            Callable[[CompiledGraph[GraphValueT], GraphRunState], str | None],
            cast(_RoutingPrivateView, module)._frontier_gate_error,
        )
        return function(graph, state)

    @staticmethod
    def gate_matches_cause(module: object, gate: ActivationGate, cause: RoutedActivationCause) -> bool:
        function = cast(
            Callable[[ActivationGate, RoutedActivationCause], bool],
            cast(_RoutingPrivateView, module)._gate_matches_cause,
        )
        return function(gate, cause)

    @staticmethod
    def historical_join_arrivals(
        module: object,
        graph: CompiledGraph[GraphValueT],
        state: GraphRunState,
    ) -> dict[GraphJoinOccurrenceIdentity, tuple[ActivationReference, ...]]:
        function = cast(
            Callable[
                [CompiledGraph[GraphValueT], GraphRunState],
                dict[GraphJoinOccurrenceIdentity, tuple[ActivationReference, ...]],
            ],
            cast(_RoutingPrivateView, module)._historical_join_arrivals,
        )
        return function(graph, state)

    @staticmethod
    def pending_join_arrivals(
        module: object,
        graph: CompiledGraph[GraphValueT],
        state: GraphRunState,
    ) -> dict[GraphJoinOccurrenceIdentity, list[ActivationReference]]:
        function = cast(
            Callable[
                [CompiledGraph[GraphValueT], GraphRunState],
                dict[GraphJoinOccurrenceIdentity, list[ActivationReference]],
            ],
            cast(_RoutingPrivateView, module)._pending_join_arrivals,
        )
        return function(graph, state)

    @staticmethod
    def post_advance_error(module: object, graph: CompiledGraph[GraphValueT], state: GraphRunState) -> str | None:
        function = cast(
            Callable[[CompiledGraph[GraphValueT], GraphRunState], str | None],
            cast(_RoutingPrivateView, module)._post_advance_error,
        )
        return function(graph, state)

    @staticmethod
    def required_target(
        module: object,
        graph: CompiledGraph[GraphValueT],
        target: GraphNodeId,
        activation: GraphFrontierActivation,
        state: GraphRunState,
        scope_run: ScopeRunCoordinate,
        activation_superstep: int,
        frames: ScopedFrameAvailability[GraphValueT],
    ) -> RequiredTarget:
        function = cast(
            Callable[
                [
                    CompiledGraph[GraphValueT],
                    GraphNodeId,
                    GraphFrontierActivation,
                    GraphRunState,
                    ScopeRunCoordinate,
                    int,
                    ScopedFrameAvailability[GraphValueT],
                ],
                RequiredTarget,
            ],
            cast(_RoutingPrivateView, module)._required_target,
        )
        return function(
            graph,
            target,
            activation,
            state,
            scope_run,
            activation_superstep,
            frames,
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
    occurrence = None
    if len(references) > 1:
        occurrence = join_occurrence(
            tuple(reference.activation.node_id for reference in references),
            node_id,
            target_superstep=max(reference.activation.superstep for reference in references) + 1,
            run_id=references[0].activation.run_id,
        )
    return GraphFrontierActivation(GraphNodeId(node_id), RoutedActivationCause(references, occurrence))


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


def predecessor_loop_graph() -> CompiledGraph[int]:
    async def initialize(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(value=values["seed"])

    async def loop(values: Graph.Values[int]) -> Graph.Values[int]:
        return values

    initialize_id = GraphNodeId("initialize")
    loop_id = GraphNodeId("loop")
    initialize_node = CallableNodeDefinition(
        initialize_id,
        initialize,
        normalize_input_bindings({"seed": Graph.graph_input("seed", int)}),
        normalize_output_declarations({"value": int}),
    )
    loop_node = CallableNodeDefinition(
        loop_id,
        loop,
        normalize_input_bindings({"value": Graph.node_output("value")}),
        normalize_output_declarations({"value": int}),
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("predecessor.routing"),
            GraphDefinitionVersion(1),
            (initialize_node, loop_node),
            (
                DirectEdge(initialize_id, loop_id),
                ConditionalEdge(loop_id, GraphRouteId("continue"), loop_id),
                ConditionalEdge(loop_id, GraphRouteId("done"), END),
            ),
            (),
            normalize_graph_output_declarations({"value": Graph.node_output(loop_id, "value")}),
        )
    )


def settled_predecessor_loop(
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
    initialize_token = state.execution.token
    state = reduce_graph_run(
        state,
        SettleGraphNode(
            state.revision,
            initialize_token,
            SucceededGraphNodeOutcome(
                GraphNodeId("initialize"),
                ContinueGraphRouting(),
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
    initialize_descriptor = graph.transition.publications[GraphNodeId("initialize")]
    frames = frames.add_publication(
        ConfirmedPublication(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, 0, GraphNodeId("initialize")),
                initialize_descriptor.identity,
            ),
            _make_node_output_frame(Graph.values(value=3), initialize_descriptor.declarations),
            state.revision,
            ExecutionPublicationProvenance(initialize_token),
        )
    )
    advance = resolve_routing(graph, state, scope_run, frames)
    assert isinstance(advance, AdvanceGraphFrontier)
    state = reduce_graph_run(state, advance)
    state = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("loop-attempt"), None),
    )
    assert state.execution is not None
    loop_token = state.execution.token
    state = reduce_graph_run(
        state,
        SettleGraphNode(
            state.revision,
            loop_token,
            SucceededGraphNodeOutcome(
                GraphNodeId("loop"),
                SelectGraphRoute(GraphRouteId(route)),
            ),
        ),
    )
    loop_descriptor = graph.transition.publications[GraphNodeId("loop")]
    frames = frames.add_publication(
        ConfirmedPublication(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, 1, GraphNodeId("loop")),
                loop_descriptor.identity,
            ),
            _make_node_output_frame(Graph.values(value=4), loop_descriptor.declarations),
            state.revision,
            ExecutionPublicationProvenance(loop_token),
        )
    )
    return state, frames


def test_causal_input_retains_exactly_one_predecessor_superstep() -> None:
    assert publication_history_window(predecessor_loop_graph()) == PublicationHistoryWindow((), 1)


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


def test_compiled_join_source_index_corruption_fails_closed() -> None:
    base = topology(
        "a",
        "b",
        "c",
        edges=(join(("a", "b"), "c"),),
        entries=("a", "b"),
    )
    plan = base.transition.joins_by_source[GraphNodeId("a")][0]

    non_source_index = dict(base.transition.joins_by_source)
    non_source_index[GraphNodeId("c")] = (plan,)
    non_source = replace(
        base,
        transition=replace(base.transition, joins_by_source=frozen_map(non_source_index)),
    )
    with pytest.raises(SnapshotMismatchError, match="non-source"):
        _RoutingPrivateView.declared_joins(routing_module, non_source)

    conflicting_index = dict(base.transition.joins_by_source)
    conflicting_index[GraphNodeId("b")] = (compiled_join(("a", "b"), "c", offsets=(2, 1)),)
    conflicting = replace(
        base,
        transition=replace(base.transition, joins_by_source=frozen_map(conflicting_index)),
    )
    with pytest.raises(SnapshotMismatchError, match="conflicting occurrence projections"):
        _RoutingPrivateView.declared_joins(routing_module, conflicting)

    incomplete_index = dict(base.transition.joins_by_source)
    incomplete_index[GraphNodeId("b")] = ()
    incomplete = replace(
        base,
        transition=replace(base.transition, joins_by_source=frozen_map(incomplete_index)),
    )
    with pytest.raises(SnapshotMismatchError, match="source index is incomplete"):
        _RoutingPrivateView.declared_joins(routing_module, incomplete)
    assert _RoutingPrivateView.frontier_gate_error(routing_module, incomplete, running_state(frontier=("a", "b"))) == (
        "compiled Join source index is incomplete"
    )


def test_pending_join_progress_requires_the_compiled_occurrence_projection() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "b"), join(("a", "b"), "c")),
        entries=("a",),
    )
    progress = join_progress(
        ("a", "b"),
        "c",
        (reference("a"),),
        target_superstep=3,
    )
    state = running_state(superstep=1, frontier=("b",), join_progress=(progress,))

    with pytest.raises(JoinProgressError, match="misprojected arrival evidence"):
        _RoutingPrivateView.pending_join_arrivals(routing_module, graph, state)


def test_causal_self_loop_route_emits_one_exact_predecessor_activation() -> None:
    graph = predecessor_loop_graph()
    state, frames = settled_predecessor_loop(graph, "continue")

    command = resolve_routing(graph, state, root_scope_run(state.run_id), frames)

    assert isinstance(command, AdvanceGraphFrontier)
    assert command.activations == (
        GraphFrontierActivation(
            GraphNodeId("loop"),
            RoutedActivationCause(
                (
                    ActivationReference(
                        GraphActivationIdentity(GraphRunId("run"), 1, GraphNodeId("loop")),
                        GraphRouteId("continue"),
                    ),
                )
            ),
        ),
    )


def test_causal_self_loop_terminal_route_completes_without_a_new_activation() -> None:
    graph = predecessor_loop_graph()
    state, frames = settled_predecessor_loop(graph, "done")

    command = resolve_routing(graph, state, root_scope_run(state.run_id), frames)

    assert command == CompleteGraphFrontier(state.revision)


def test_causal_self_loop_rejects_a_forged_start_cause_after_round_zero() -> None:
    graph = predecessor_loop_graph()
    state, frames = settled_predecessor_loop(graph, "continue")
    node = state.frontier.nodes[0]
    forged = replace(
        state,
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

    with pytest.raises(InvalidRoutingCommandError, match="cannot carry the START cause"):
        resolve_routing(graph, forged, root_scope_run(forged.run_id), frames)


@pytest.mark.parametrize(
    ("run_id", "superstep", "route", "match"),
    [
        ("other", 1, "continue", "immediate committed settlement"),
        ("run", 1, "other", "immediate committed settlement"),
        ("run", 2, "continue", "immediate committed settlement"),
    ],
)
def test_causal_self_loop_rejects_a_forged_routed_predecessor(
    run_id: str,
    superstep: int,
    route: str,
    match: str,
) -> None:
    graph = predecessor_loop_graph()
    state, frames = settled_predecessor_loop(graph, "continue")
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


def test_routing_never_resolves_a_failed_causal_loop_frontier() -> None:
    graph = predecessor_loop_graph()
    state, frames = settled_predecessor_loop(graph, "continue")
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
            join_progress(
                ("a", "b"),
                "c",
                (reference("a"),),
                target_superstep=2,
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
            join_progress(
                ("c", "d"),
                "e",
                (reference("c", superstep=1),),
                target_superstep=3,
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
    first = join_progress(("a", "b"), "d", (reference("a"),), target_superstep=2)
    second = join_progress(("a", "c"), "e", (reference("a"),), target_superstep=2)
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
        (join_occurrence(("x", "y"), END, target_superstep=3),),
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
    graph = topology(
        "a",
        "x",
        "b",
        "c",
        "d",
        edges=(direct("x", "b"), direct("b", "c"), join(("a", "c"), "d")),
        entries=("a", "x"),
    )
    with pytest.raises(RoutingDeadlockError):
        resolve_contributions(graph, continue_for("a"), ())


def test_routing_rejects_join_arrivals_that_cannot_match_the_next_coordinate() -> None:
    base = topology(
        "a",
        "b",
        "c",
        edges=(join(("a", "b"), "c"),),
        entries=("a", "b"),
    )
    delayed = compiled_join(("a", "b"), "c", offsets=(2, 2))
    join_index = dict(base.transition.joins_by_source)
    join_index[GraphNodeId("a")] = (delayed,)
    join_index[GraphNodeId("b")] = (delayed,)
    delayed_graph = replace(
        base,
        transition=replace(base.transition, joins_by_source=frozen_map(join_index)),
    )

    with pytest.raises(JoinProgressError, match="completed Join occurrence has the wrong target coordinate"):
        resolve_contributions(delayed_graph, continue_for("a", "b"), ())
    with pytest.raises(JoinProgressError, match="partial Join occurrence cannot reach its target coordinate"):
        resolve_contributions(base, continue_for("a"), ())


@pytest.mark.parametrize(
    "progress",
    [
        join_progress(
            ("a", "b"),
            "c",
            (),
            target_superstep=2,
        ),
        join_progress(
            ("a", "b"),
            "c",
            (reference("a"), reference("b")),
            target_superstep=2,
        ),
        join_progress(
            ("a", "b"),
            "unknown",
            (reference("a"),),
            target_superstep=2,
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
    progress = join_progress(
        ("a", "b"),
        "c",
        (reference("a"),),
        target_superstep=2,
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


def test_stale_join_occurrence_cannot_combine_with_a_later_source_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.engine.routing as routing

    graph = topology(
        "a",
        "b",
        "c",
        "work",
        edges=(direct("a", "b"), direct("b", "work"), join(("a", "b"), "c")),
        entries=("a",),
    )
    stale = join_progress(("a", "b"), "c", (reference("a"),), target_superstep=2)
    state = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("a", superstep=1),)),
        superstep=2,
        evidence=(reference("a"), reference("a", superstep=1), reference("b", superstep=2)),
    )
    monkeypatch.setattr(routing, "frontier_admission_error", _allow_frontier_admission)

    with pytest.raises(JoinProgressError, match="invalid Join progress"):
        resolve_routing_facts(
            graph,
            replace(state, join_progress=(stale,)),
            root_scope_run(state.run_id),
            ScopedFrameIndex(),
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
    assert _RoutingPrivateView.frontier_gate_error(
        routing_module, unknown_graph, _settled_routing_state("foreign", StartActivationCause())
    ) == ("frontier activation references unknown node 'foreign'")

    entry_graph = topology("a", "b", edges=(direct("a", "b"),), entries=("a",))
    assert _RoutingPrivateView.frontier_gate_error(
        routing_module, entry_graph, _settled_routing_state("b", StartActivationCause())
    ) == ("START activation 'b' is not a compiled graph entry")

    unsupported = _settled_routing_state("a", object())
    assert _RoutingPrivateView.frontier_gate_error(routing_module, unknown_graph, unsupported) == (
        "frontier activation has an unsupported cause"
    )

    routed_graph = topology("a", "b", edges=(direct("a", "b"),), entries=("a",))
    mismatched = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("foreign"),)),
        superstep=1,
        evidence=(reference("foreign"),),
    )
    assert _RoutingPrivateView.frontier_gate_error(routing_module, routed_graph, mismatched) == (
        "frontier activation 'b' does not match exactly one compiled activation gate"
    )

    missing_evidence = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
    )
    assert _RoutingPrivateView.frontier_gate_error(routing_module, routed_graph, missing_evidence) == (
        "frontier activation 'b' lacks committed predecessor settlement evidence"
    )


def test_frontier_admission_rejects_misprojected_join_occurrence_evidence() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "b"), join(("a", "b"), "c")),
        entries=("a",),
    )
    arrivals = (reference("a"), reference("b"))
    state = _settled_routing_state(
        "c",
        RoutedActivationCause(
            arrivals,
            join_occurrence(("a", "b"), "c", target_superstep=2),
        ),
        superstep=2,
        evidence=arrivals,
    )

    assert _RoutingPrivateView.frontier_gate_error(routing_module, graph, state) == (
        "frontier activation 'c' has misprojected Join evidence"
    )


def test_frontier_admission_requires_the_declared_join_occurrence() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(join(("a", "b"), "c"),),
        entries=("a", "b"),
    )
    arrivals = (reference("a"), reference("b"))
    missing = object.__new__(RoutedActivationCause)
    object.__setattr__(missing, "references", arrivals)
    object.__setattr__(missing, "join_occurrence", None)
    missing_state = _settled_routing_state(
        "c",
        missing,
        superstep=1,
        evidence=arrivals,
    )
    assert _RoutingPrivateView.frontier_gate_error(routing_module, graph, missing_state) == (
        "frontier activation 'c' lacks its compiled Join occurrence"
    )

    unknown = RoutedActivationCause(
        arrivals,
        join_occurrence(("a", "b"), "foreign", target_superstep=1),
    )
    unknown_state = _settled_routing_state(
        "c",
        unknown,
        superstep=1,
        evidence=arrivals,
    )
    assert _RoutingPrivateView.frontier_gate_error(routing_module, graph, unknown_state) == (
        "frontier activation 'c' has an unknown Join occurrence"
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
    assert _RoutingPrivateView.post_advance_error(routing_module, graph, state) == (
        "settled activation references unknown node 'ghost'"
    )


def test_completion_transition_admission_replays_the_previous_control_decision() -> None:
    completed = replace(
        running_state(),
        status=GraphRunStatus.COMPLETED,
        frontier=GraphFrontierState(()),
    )
    assert (
        transition_admission_error(
            topology("a"),
            None,
            CompleteGraphFrontier(0),
            completed,
        )
        == "completed graph state lacks its admitted completion transition"
    )

    previous = _settled_routing_state(
        "a",
        StartActivationCause(),
        evidence=(reference("a"),),
    )
    invalid_previous = replace(previous, settled_activations=(reference("ghost"),))
    assert (
        transition_admission_error(
            topology("a"),
            invalid_previous,
            CompleteGraphFrontier(0),
            completed,
        )
        == "settled activation references unknown node 'ghost'"
    )

    join_graph = topology(
        "a",
        "b",
        "c",
        edges=(join(("a", "b"), "c"),),
        entries=("a", "b"),
    )
    assert (
        transition_admission_error(
            join_graph,
            previous,
            CompleteGraphFrontier(0),
            completed,
        )
        == "partial Join occurrence cannot reach its target coordinate"
    )

    forged_consumption = join_occurrence(("a", "b"), "c", target_superstep=1)
    assert (
        transition_admission_error(
            topology("a"),
            previous,
            CompleteGraphFrontier(0, (forged_consumption,)),
            completed,
        )
        == "graph completion consumed the wrong Join occurrences"
    )


def predecessor_binding(graph: CompiledGraph[int]) -> CompiledPredecessorInput:
    source = graph.transition.materializations[GraphNodeId("loop")].bindings.entries[0].source
    assert isinstance(source, CompiledPredecessorInput)
    return source


def test_predecessor_admission_requires_committed_predecessor_evidence() -> None:
    graph = predecessor_loop_graph()
    state, _frames = settled_predecessor_loop(graph, "continue")
    candidate = replace(state, settled_activations=())

    assert _RoutingPrivateView.frontier_gate_error(routing_module, graph, candidate) == (
        "predecessor activation lacks immediate committed settlement evidence"
    )


@pytest.mark.parametrize(("field", "match"), [("target", "target input"), ("input", "target input")])
def test_predecessor_source_rejects_a_binding_for_another_target_input(field: str, match: str) -> None:
    graph = predecessor_loop_graph()
    state, _frames = settled_predecessor_loop(graph, "continue")
    binding = predecessor_binding(graph)
    mismatched = (
        replace(binding, target=GraphNodeId("foreign")) if field == "target" else replace(binding, input_name="foreign")
    )

    with pytest.raises(InvalidRoutingCommandError, match=match):
        predecessor_source_for_cause(
            state,
            GraphNodeId("loop"),
            "value",
            state.superstep,
            state.frontier.nodes[0].cause,
            mismatched,
        )


def test_predecessor_source_selects_the_exact_causal_publication() -> None:
    graph = predecessor_loop_graph()
    state, _frames = settled_predecessor_loop(graph, "continue")

    selected = predecessor_source_for_cause(
        state,
        GraphNodeId("loop"),
        "value",
        state.superstep,
        state.frontier.nodes[0].cause,
        predecessor_binding(graph),
    )

    assert selected.source == NodeOutputPort((), GraphNodeId("initialize"), "value")
    assert selected.predecessor == GraphActivationIdentity(state.run_id, 0, GraphNodeId("initialize"))


def test_predecessor_source_rejects_a_non_immediate_or_uncommitted_reference() -> None:
    graph = predecessor_loop_graph()
    state, _frames = settled_predecessor_loop(graph, "continue")
    cause = state.frontier.nodes[0].cause
    binding = predecessor_binding(graph)

    with pytest.raises(InvalidRoutingCommandError, match="immediate committed settlement"):
        predecessor_source_for_cause(
            replace(state, superstep=2),
            GraphNodeId("loop"),
            "value",
            2,
            cause,
            binding,
        )
    with pytest.raises(InvalidRoutingCommandError, match="immediate committed settlement"):
        predecessor_source_for_cause(
            replace(state, settled_activations=()),
            GraphNodeId("loop"),
            "value",
            1,
            cause,
            binding,
        )


def test_predecessor_source_rejects_malformed_causes() -> None:
    graph = predecessor_loop_graph()
    state, _frames = settled_predecessor_loop(graph, "continue")
    binding = predecessor_binding(graph)
    join_cause = routed(
        "loop",
        reference("initialize"),
        reference("other"),
    ).cause
    assert isinstance(join_cause, RoutedActivationCause)

    with pytest.raises(InvalidRoutingCommandError, match="invalid target coordinate"):
        predecessor_source_for_cause(
            state,
            GraphNodeId("loop"),
            "value",
            0,
            StartActivationCause(),
            binding,
        )
    with pytest.raises(InvalidRoutingCommandError, match="cannot carry the START cause"):
        predecessor_source_for_cause(
            state,
            GraphNodeId("loop"),
            "value",
            1,
            StartActivationCause(),
            binding,
        )
    with pytest.raises(InvalidRoutingCommandError, match="unsupported cause"):
        predecessor_source_for_cause(
            state,
            GraphNodeId("loop"),
            "value",
            1,
            cast(StartActivationCause | RoutedActivationCause, object()),
            binding,
        )
    with pytest.raises(InvalidRoutingCommandError, match="one non-Join predecessor"):
        predecessor_source_for_cause(
            state,
            GraphNodeId("loop"),
            "value",
            1,
            join_cause,
            binding,
        )


def test_predecessor_source_rejects_an_uncompiled_source() -> None:
    graph = predecessor_loop_graph()
    state, _frames = settled_predecessor_loop(graph, "continue")
    binding = replace(
        predecessor_binding(graph),
        sources=(NodeOutputPort((), GraphNodeId("other"), "value"),),
    )

    with pytest.raises(InvalidRoutingCommandError, match="not an allowed predecessor source"):
        predecessor_source_for_cause(
            state,
            GraphNodeId("loop"),
            "value",
            state.superstep,
            state.frontier.nodes[0].cause,
            binding,
        )


def test_required_target_rejects_a_successor_with_a_different_target() -> None:
    graph = topology("a", "b")
    state = running_state(frontier=("a",))
    activation = GraphFrontierActivation(GraphNodeId("wrong"), StartActivationCause())

    with pytest.raises(InvalidRoutingCommandError, match="does not match its target"):
        _RoutingPrivateView.required_target(
            routing_module,
            graph,
            GraphNodeId("b"),
            activation,
            state,
            root_scope_run(state.run_id),
            1,
            ScopedFrameIndex(),
        )


def test_routing_facts_reject_a_control_target_without_an_admitted_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.engine.routing as routing

    graph = topology("a", "b", edges=(direct("a", "b"),))
    state = _settled_routing_state("a", StartActivationCause(), evidence=(reference("a"),))

    def missing_activation(_graph: CompiledGraph[str], _state: GraphRunState) -> _ControlResolutionView:
        return _RoutingPrivateView.control_resolution(
            routing_module,
            frozenset({GraphNodeId("b")}),
            frozenset(),
            (),
            (),
            (),
        )

    monkeypatch.setattr(routing, "_resolve_control", missing_activation)
    with pytest.raises(InvalidRoutingCommandError, match="lacks an admitted activation"):
        resolve_routing_facts(graph, state, root_scope_run(state.run_id), ScopedFrameIndex())


def test_gate_matching_rejects_a_reference_count_mismatch() -> None:
    cause = RoutedActivationCause(
        (reference("a"), reference("b")),
        join_occurrence(("a", "b"), "target", target_superstep=1),
    )
    assert not _RoutingPrivateView.gate_matches_cause(
        routing_module,
        ((GraphNodeId("a"), frozenset({None})),),
        cause,
    )


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
    assert _RoutingPrivateView.post_advance_error(routing_module, graph, state) == message


def test_post_advance_accepts_a_terminal_conditional_route() -> None:
    graph = topology("a", edges=(conditional("a", "done", END),), entries=("a",))
    state = _settled_routing_state(
        "a",
        RoutedActivationCause((reference("a", route="done"),)),
        superstep=1,
        evidence=(reference("a", route="done"),),
    )
    assert (
        _RoutingPrivateView.post_advance_error(routing_module, graph, replace(state, frontier=GraphFrontierState(())))
        is None
    )


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
            join_progress(
                ("a", "b"),
                "c",
                (reference("a"), reference("a")),
                target_superstep=2,
            ),
        ),
    )
    assert _RoutingPrivateView.post_advance_error(routing_module, join_graph, duplicate) == (
        "snapshot Join progress repeats one source activation"
    )

    terminal_graph = topology("a", "b", edges=(join(("a", "b"), END),), entries=("a", "b"))
    terminal = replace(
        duplicate,
        frontier=GraphFrontierState(()),
        join_progress=(),
        settled_activations=(reference("a"), reference("b")),
    )
    assert _RoutingPrivateView.post_advance_error(routing_module, terminal_graph, terminal) is None


def test_post_advance_rejects_unmatched_and_unexpected_successors() -> None:
    direct_graph = topology("a", "b", "c", edges=(direct("a", "b"),), entries=("a",))
    mismatch = _settled_routing_state(
        "b",
        RoutedActivationCause((reference("c"),)),
        superstep=1,
        evidence=(reference("a"), reference("c")),
    )
    assert _RoutingPrivateView.post_advance_error(routing_module, direct_graph, mismatch) == (
        "frontier target 'b' does not match its compiled successor cause"
    )

    unexpected_graph = topology("a", "c", entries=("a", "c"))
    unexpected = _settled_routing_state(
        "c",
        RoutedActivationCause((reference("a"),)),
        superstep=1,
        evidence=(reference("a"),),
    )
    assert _RoutingPrivateView.post_advance_error(routing_module, unexpected_graph, unexpected) == (
        "frontier contains an unproved successor target: ('c',)"
    )


def test_join_arrival_rejects_two_routes_for_one_activation() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    state = replace(
        running_state(superstep=1, frontier=("c",)),
        settled_activations=(reference("a", route="left"), reference("a", route="right")),
    )
    with pytest.raises(JoinProgressError, match="selected two routes"):
        _RoutingPrivateView.historical_join_arrivals(routing_module, graph, state)

    repeated = replace(
        state,
        settled_activations=(reference("a"), reference("a")),
    )
    with pytest.raises(JoinProgressError, match="occurrence repeated"):
        _RoutingPrivateView.historical_join_arrivals(routing_module, graph, repeated)


def test_historical_join_arrival_rejects_an_unknown_older_ledger_node() -> None:
    graph = topology("a", "b", "c", edges=(join(("a", "b"), "c"),), entries=("a", "b"))
    state = replace(
        running_state(superstep=2, frontier=("c",)),
        settled_activations=(reference("ghost"), reference("a", superstep=1)),
    )

    with pytest.raises(JoinProgressError, match="unknown node 'ghost'"):
        _RoutingPrivateView.historical_join_arrivals(routing_module, graph, state)


@pytest.mark.parametrize(
    ("superstep", "work_superstep", "message"),
    [
        (2, 1, "Join occurrence reached its target coordinate without every source"),
        (3, 2, "historical Join occurrence passed its target without every source"),
    ],
)
def test_post_advance_rejects_a_partial_join_at_or_after_its_target_coordinate(
    superstep: int,
    work_superstep: int,
    message: str,
) -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "work",
        edges=(direct("a", "b"), direct("b", "work"), join(("a", "b"), "c")),
        entries=("a",),
    )
    state = replace(
        running_state(superstep=superstep, frontier=("work",)),
        settled_activations=(reference("a"), reference("work", superstep=work_superstep)),
    )

    assert _RoutingPrivateView.post_advance_error(routing_module, graph, state) == message


def test_post_advance_rejects_a_complete_join_before_its_compiled_target_coordinate() -> None:
    base = topology(
        "a",
        "b",
        "c",
        edges=(join(("a", "b"), "c"),),
        entries=("a", "b"),
    )
    delayed = compiled_join(("a", "b"), "c", offsets=(2, 2))
    join_index = dict(base.transition.joins_by_source)
    join_index[GraphNodeId("a")] = (delayed,)
    join_index[GraphNodeId("b")] = (delayed,)
    graph = replace(
        base,
        transition=replace(base.transition, joins_by_source=frozen_map(join_index)),
    )
    state = replace(
        running_state(superstep=1, frontier=("c",)),
        settled_activations=(reference("a"), reference("b")),
    )

    assert _RoutingPrivateView.post_advance_error(routing_module, graph, state) == (
        "Join occurrence completed before its target coordinate"
    )


def test_post_advance_requires_the_exact_reconstructed_partial_join_progress() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        edges=(direct("a", "b"), join(("a", "b"), "c")),
        entries=("a",),
    )
    missing = replace(
        running_state(superstep=1, frontier=("b",)),
        settled_activations=(reference("a"),),
    )
    assert _RoutingPrivateView.post_advance_error(routing_module, graph, missing) == (
        "frontier transition lost or invented Join progress"
    )

    base = topology(
        "a",
        "b",
        "c",
        "target",
        edges=(join(("a", "b", "c"), "target"),),
        entries=("a", "b", "c"),
    )
    delayed = compiled_join(("a", "b", "c"), "target", offsets=(2, 2, 2))
    join_index = dict(base.transition.joins_by_source)
    for source in delayed.identity.sources:
        join_index[source] = (delayed,)
    delayed_graph = replace(
        base,
        transition=replace(base.transition, joins_by_source=frozen_map(join_index)),
    )
    occurrence = join_occurrence(("a", "b", "c"), "target", target_superstep=2)
    incomplete_record = join_progress(
        ("a", "b", "c"),
        "target",
        (reference("a"),),
        target_superstep=2,
    )
    changed = replace(
        running_state(superstep=1, frontier=("target",)),
        settled_activations=(reference("a"), reference("b")),
        join_progress=(incomplete_record,),
    )
    assert incomplete_record.occurrence == occurrence
    assert _RoutingPrivateView.post_advance_error(routing_module, delayed_graph, changed) == (
        "frontier transition changed Join progress without a proven arrival"
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
    progress = join_progress(
        ("a", "b", "c"),
        "d",
        (reference("a"), reference("a")),
        target_superstep=2,
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
    progress = join_progress(
        ("a", "b", "c"),
        "d",
        (reference("c"),),
        target_superstep=2,
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

    assert _RoutingPrivateView.post_advance_error(routing_module, graph, state) == (
        "target 'c' has 2 compiled activation causes"
    )
