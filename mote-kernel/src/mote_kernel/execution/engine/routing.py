"""Unique compiled control/data resolver for one settled frontier."""

from dataclasses import dataclass
from itertools import chain
from typing import TypeAlias, TypeVar

from mote_kernel.execution.errors import (
    GraphValidationError,
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    SnapshotMismatchError,
    UnknownRouteError,
)
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.ports import (
    ActivationGate,
    CompiledActivationRule,
    GraphInputPort,
    NodeOutputPort,
    PublicationSelection,
    PublicationSelectionKind,
    ResolvedValueSource,
    require_publication_selection,
)
from mote_kernel.execution.graph.topology import (
    CompiledGraph,
    CompiledJoin,
)
from mote_kernel.execution.identity import ScopeRunCoordinate, stable_activation
from mote_kernel.execution.run_context import (
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedFrameAvailability,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ActivationReference,
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    GraphAbortReason,
    GraphActivationCause,
    GraphActivationIdentity,
    GraphFrontierActivation,
    GraphFrontierStatus,
    GraphJoinIdentity,
    GraphJoinOccurrenceIdentity,
    GraphJoinProgress,
    GraphNodeId,
    GraphRouteId,
    GraphRoutingContribution,
    GraphRunCommand,
    GraphRunState,
    GraphRunStatus,
    RoutedActivationCause,
    SelectGraphRoute,
    StartActivationCause,
    frontier_node,
    frontier_status,
    routing_contributions,
)

GraphValueT = TypeVar("GraphValueT")
ResolutionCommand: TypeAlias = AdvanceGraphFrontier | CompleteGraphFrontier | AbortGraphRun


@dataclass(frozen=True, slots=True)
class RequiredTarget:
    node_id: GraphNodeId
    unavailable_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoutingFacts:
    control_targets: tuple[RequiredTarget, ...]
    completed_join_targets: tuple[RequiredTarget, ...]
    remaining_join_progress: tuple[GraphJoinProgress, ...]
    unavailable_graph_outputs: tuple[str, ...]
    activations: tuple[GraphFrontierActivation, ...]
    consumed_join_progress: tuple[GraphJoinOccurrenceIdentity, ...]


@dataclass(frozen=True, slots=True)
class _ControlResolution:
    direct_targets: frozenset[GraphNodeId]
    join_targets: frozenset[GraphNodeId]
    remaining_join_progress: tuple[GraphJoinProgress, ...]
    activations: tuple[GraphFrontierActivation, ...]
    consumed_join_progress: tuple[GraphJoinOccurrenceIdentity, ...]


@dataclass(frozen=True, slots=True)
class PublicationHistoryWindow:
    absolute_supersteps: tuple[int, ...]
    relative_horizon: int


@dataclass(frozen=True, slots=True)
class FeedbackSourceSelection:
    """The source selected by one admitted feedback cause."""

    source: GraphInputPort | NodeOutputPort
    publication: PublicationSelection | None
    predecessor: GraphActivationIdentity | None


def publication_history_window(graph: CompiledGraph[GraphValueT]) -> PublicationHistoryWindow:
    absolute_supersteps: set[int] = set()
    relative_horizon = 0
    selections = chain(
        (
            selection
            for _node_id, plan in graph.transition.materializations.entries
            for binding in plan.bindings.entries
            for selection in (
                (binding.source.initial_selection, binding.source.repeat_selection)
                if isinstance(binding.source, CompiledActivationRule)
                else (binding.publication,)
            )
        ),
        (binding.publication for binding in graph.transition.graph_outputs.entries),
    )
    for selection in selections:
        if selection is None:
            continue
        if selection.kind is PublicationSelectionKind.ABSOLUTE:
            absolute_supersteps.add(selection.superstep)
        else:
            relative_horizon = max(relative_horizon, selection.superstep)
    return PublicationHistoryWindow(tuple(sorted(absolute_supersteps)), relative_horizon)


def _graph_input_coordinate(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
) -> GraphInputAvailabilityCoordinate[GraphValueT]:
    return GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity)


def _node_output_coordinate(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    source: NodeOutputPort,
    superstep: int,
) -> PublicationAvailabilityCoordinate[GraphValueT]:
    return PublicationAvailabilityCoordinate(
        stable_activation(
            scope_run,
            GraphActivationIdentity(scope_run.graph_run_id, superstep, source.node_id),
        ),
        graph.transition.publications[source.node_id].identity,
    )


def _value_available(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    source: ResolvedValueSource,
    publication: PublicationSelection | None,
    activation_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
) -> bool:
    if isinstance(source, GraphInputPort):
        return frames.has_graph_input(_graph_input_coordinate(graph, scope_run))
    selection = require_publication_selection(
        publication,
        InvalidRoutingCommandError("compiled value binding lacks its activation selection"),
    )
    return frames.has_publication(
        _node_output_coordinate(graph, scope_run, source, selection.resolve(activation_superstep))
    )


def _source_label(source: ResolvedValueSource) -> str:
    if isinstance(source, GraphInputPort):
        return f"graph-input:{source.name}"
    return f"{source.node_id}.{source.output_name}"


def validate_routing_contribution(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
    contribution: GraphRoutingContribution,
) -> None:
    if node_id not in graph.nodes:
        raise InvalidRoutingCommandError("routing contribution references an unknown node")
    conditional = graph.transition.conditional_targets[node_id]
    if isinstance(contribution, ContinueGraphRouting):
        if conditional:
            raise InvalidRoutingCommandError("a conditional node must select one declared route")
    else:
        if not conditional:
            raise InvalidRoutingCommandError("a non-conditional node cannot select a route")
        if contribution.route not in conditional:
            raise UnknownRouteError("node selected an unknown conditional route")


def settled_activation_admission_error(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> str | None:
    """Return a topology error for one State-owned settled activation ledger.

    ``GraphRunState`` owns the ledger, while the compiled graph owns its node
    and route universe.  State validation can check the shape of a reference,
    but only this compiled-graph admission boundary can reject a canonical
    reference to a node or route that this graph never declared.
    """

    for reference in state.settled_activations:
        activation = reference.activation
        node_id = activation.node_id
        if node_id not in graph.nodes:
            return f"settled activation references unknown node {node_id!r}"
        conditional = graph.transition.conditional_targets[node_id]
        route = reference.route
        if conditional:
            if route is None:
                return f"conditional settled activation {node_id!r} lacks its selected route"
            if route not in conditional:
                return f"settled activation {node_id!r} selected an unknown route {route!r}"
        elif route is not None:
            return f"non-conditional settled activation {node_id!r} selected route {route!r}"
    return None


def _success_routes(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
) -> tuple[GraphRouteId | None, ...]:
    routes = tuple(graph.transition.conditional_targets[node_id])
    return routes or (None,)


def _gate_matches_cause(
    gate: ActivationGate,
    cause: RoutedActivationCause,
) -> bool:
    actual = tuple(
        sorted(
            ((reference.activation.node_id, reference.route) for reference in cause.references),
            key=lambda item: (item[0], item[1] is not None, item[1] or ""),
        )
    )
    if len(actual) != len(gate):
        return False
    return all(
        source == actual_source and actual_route in allowed_routes
        for (source, allowed_routes), (actual_source, actual_route) in zip(gate, actual, strict=True)
    )


def feedback_source_for_cause(
    state: GraphRunState,
    node_id: GraphNodeId,
    target_superstep: int,
    cause: GraphActivationCause,
    rule: CompiledActivationRule[GraphValueT],
) -> FeedbackSourceSelection:
    """Resolve a feedback input from one exact activation cause.

    The cause is the only phase discriminator.  A graph-input initial source
    can be selected only by START at superstep zero; a node-output initial or
    repeat source must match exactly one compiled gate and its publication
    coordinate.  No fallback to a newer/older publication is permitted.
    """

    if rule.target != node_id:
        raise InvalidRoutingCommandError("feedback rule target does not match activation target")
    if type(target_superstep) is not int or target_superstep < 0:
        raise InvalidRoutingCommandError("feedback activation has an invalid target coordinate")
    if type(cause) is StartActivationCause:
        if target_superstep != 0 or not isinstance(rule.initial, GraphInputPort):
            if rule.repeat.node_id == node_id and target_superstep > 0:
                raise InvalidRoutingCommandError("feedback predecessor activation cause must be routed")
            raise InvalidRoutingCommandError("initial feedback activation must carry the START cause")
        return FeedbackSourceSelection(rule.initial, None, None)
    if type(cause) is not RoutedActivationCause:
        if rule.repeat.node_id == node_id and isinstance(rule.initial, GraphInputPort):
            raise InvalidRoutingCommandError("feedback activation predecessor cause must be routed")
        raise InvalidRoutingCommandError("feedback activation has an unsupported cause")

    # Keep the original direct self-feedback guarantee explicit.  The general
    # gate/selection proof below handles multi-node and Join cycles, while this
    # closed shape additionally requires the immediately preceding activation
    # of the same node.
    if (
        rule.repeat.node_id == node_id
        and isinstance(rule.initial, GraphInputPort)
        and (
            len(cause.references) != 1
            or cause.join_occurrence is not None
            or target_superstep < 1
            or cause.references[0].activation != GraphActivationIdentity(state.run_id, target_superstep - 1, node_id)
            or rule.repeat_selection.kind is not PublicationSelectionKind.RELATIVE
            or rule.repeat_selection.superstep != 1
            or len(rule.repeat_gates) != 1
            or rule.repeat_gates[0] != ((node_id, frozenset((cause.references[0].route,))),)
        )
    ):
        raise InvalidRoutingCommandError(
            "initial feedback activation must carry the START cause"
            if target_superstep == 0
            else "feedback activation cause is not the immediate predecessor activation"
        )

    initial_matches = tuple(_gate_matches_cause(gate, cause) for gate in rule.initial_gates)
    repeat_matches = tuple(_gate_matches_cause(gate, cause) for gate in rule.repeat_gates)
    initial_count = sum(initial_matches)
    repeat_count = sum(repeat_matches)
    if initial_count + repeat_count != 1:
        raise InvalidRoutingCommandError("feedback activation does not match exactly one initial or repeat gate")
    initial = initial_count == 1
    source = rule.initial if initial else rule.repeat
    publication = rule.initial_selection if initial else rule.repeat_selection
    if not isinstance(source, NodeOutputPort):
        raise InvalidRoutingCommandError("routed feedback activation requires a node-output source")
    references = tuple(reference for reference in cause.references if reference.activation.node_id == source.node_id)
    if len(references) != 1:
        raise InvalidRoutingCommandError("feedback activation cause lacks its declared source publication")
    reference = references[0]
    if (
        reference.activation.run_id != state.run_id
        or reference.activation.superstep >= target_superstep
        or reference not in state.settled_activations
    ):
        raise InvalidRoutingCommandError("feedback activation predecessor lacks committed settlement evidence")
    selection = require_publication_selection(
        publication,
        InvalidRoutingCommandError("feedback source lacks a compiled publication selection"),
    )
    try:
        selected_superstep = selection.resolve(target_superstep)
    except GraphValidationError as error:
        raise InvalidRoutingCommandError(
            "feedback activation cause does not select its compiled publication"
        ) from error
    if selected_superstep != reference.activation.superstep:
        raise InvalidRoutingCommandError("feedback activation cause does not select its compiled publication")
    return FeedbackSourceSelection(source, selection, reference.activation)


def require_feedback_activation_cause(
    state: GraphRunState,
    node_id: GraphNodeId,
    rule: CompiledActivationRule[GraphValueT],
) -> GraphActivationIdentity | None:
    """Validate the current frontier's feedback cause and return its predecessor."""

    source = frontier_node(state.frontier, node_id)
    if source is None:
        raise InvalidRoutingCommandError("feedback activation is not present in the current frontier")
    return feedback_source_for_cause(state, node_id, state.superstep, source.cause, rule).predecessor


def _declared_joins(
    graph: CompiledGraph[GraphValueT],
) -> dict[GraphJoinIdentity, CompiledJoin]:
    declared: dict[GraphJoinIdentity, CompiledJoin] = {}
    indexed_sources: dict[GraphJoinIdentity, set[GraphNodeId]] = {}
    for source, joins in graph.transition.joins_by_source.items():
        for join in joins:
            if source not in join.identity.sources:
                raise SnapshotMismatchError("compiled Join is indexed under a non-source node")
            existing = declared.setdefault(join.identity, join)
            if existing != join:
                raise SnapshotMismatchError("compiled Join identity has conflicting occurrence projections")
            indexed_sources.setdefault(join.identity, set()).add(source)
    if any(indexed_sources[identity] != set(identity.sources) for identity in declared):
        raise SnapshotMismatchError("compiled Join source index is incomplete")
    return declared


def _pending_join_arrivals(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> dict[GraphJoinOccurrenceIdentity, list[ActivationReference]]:
    declared = _declared_joins(graph)
    arrivals: dict[GraphJoinOccurrenceIdentity, list[ActivationReference]] = {}
    for progress in state.join_progress:
        occurrence = progress.occurrence
        plan = declared.get(occurrence.join)
        arrived_sources = tuple(reference.activation.node_id for reference in progress.arrived)
        if (
            plan is None
            or occurrence in arrivals
            or occurrence.run_id != state.run_id
            or occurrence.target_superstep <= state.superstep
            or not progress.arrived
            or not set(arrived_sources) < set(occurrence.join.sources)
        ):
            raise JoinProgressError("snapshot contains invalid Join progress")
        if len(arrived_sources) != len(set(arrived_sources)):
            raise JoinProgressError("snapshot Join progress repeats one source activation")
        if any(reference not in state.settled_activations for reference in progress.arrived):
            raise JoinProgressError("snapshot Join progress lacks committed settlement evidence")
        if any(plan.occurrence_for(reference.activation) != occurrence for reference in progress.arrived):
            raise JoinProgressError("snapshot Join progress has misprojected arrival evidence")
        arrivals[occurrence] = list(progress.arrived)
    return arrivals


def _frontier_gate_error(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> str | None:
    try:
        declared_joins = _declared_joins(graph)
    except SnapshotMismatchError as error:
        return str(error)
    for node in state.frontier.nodes:
        if node.node_id not in graph.nodes:
            return f"frontier activation references unknown node {node.node_id!r}"
        cause = node.cause
        try:
            for feedback_rule in graph.transition.activation_rules.for_target(node.node_id):
                require_feedback_activation_cause(state, node.node_id, feedback_rule)
        except InvalidRoutingCommandError as error:
            return str(error)
        if type(cause) is StartActivationCause:
            if node.node_id not in graph.transition.entries:
                return f"START activation {node.node_id!r} is not a compiled graph entry"
            continue
        if type(cause) is not RoutedActivationCause:
            return "frontier activation has an unsupported cause"
        gates = graph.transition.activation_gates[node.node_id]
        matching_gates = tuple(gate for gate in gates if _gate_matches_cause(gate, cause))
        if len(matching_gates) != 1:
            return f"frontier activation {node.node_id!r} does not match exactly one compiled activation gate"
        occurrence = cause.join_occurrence
        if occurrence is None:
            if len(matching_gates[0]) != 1:
                return f"frontier activation {node.node_id!r} lacks its compiled Join occurrence"
        else:
            plan = declared_joins.get(occurrence.join)
            if (
                plan is None
                or occurrence.join.target != node.node_id
                or occurrence.run_id != state.run_id
                or occurrence.target_superstep != state.superstep
            ):
                return f"frontier activation {node.node_id!r} has an unknown Join occurrence"
            if any(plan.occurrence_for(reference.activation) != occurrence for reference in cause.references):
                return f"frontier activation {node.node_id!r} has misprojected Join evidence"
        if any(reference not in state.settled_activations for reference in cause.references):
            return f"frontier activation {node.node_id!r} lacks committed predecessor settlement evidence"
    return None


def _append_successor_candidate(
    candidates: dict[GraphNodeId, list[RoutedActivationCause]],
    target: GraphNodeId,
    cause: RoutedActivationCause,
) -> None:
    candidates.setdefault(target, []).append(cause)


def _historical_join_arrivals(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> dict[GraphJoinOccurrenceIdentity, tuple[ActivationReference, ...]]:
    """Rebuild every live Join occurrence from committed settlement evidence."""

    arrivals: dict[GraphJoinOccurrenceIdentity, list[ActivationReference]] = {}
    for reference in state.settled_activations:
        activation = reference.activation
        if activation.superstep >= state.superstep:
            continue
        if activation.node_id not in graph.nodes:
            raise JoinProgressError(f"settled activation references unknown node {activation.node_id!r}")
        for plan in graph.transition.joins_by_source[activation.node_id]:
            occurrence = plan.occurrence_for(activation)
            bucket = arrivals.setdefault(occurrence, [])
            existing = next(
                (item for item in bucket if item.activation.node_id == activation.node_id),
                None,
            )
            if existing is not None:
                if existing != reference:
                    raise JoinProgressError("Join source activation occurrence selected two routes")
                raise JoinProgressError("Join source activation occurrence repeated")
            bucket.append(reference)
    return {
        occurrence: tuple(sorted(references, key=ActivationReference.canonical_key))
        for occurrence, references in arrivals.items()
    }


def _post_advance_error(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> str | None:
    """Check that a non-initial frontier is the successor of the prior one.

    The reducer protects live transitions; this check protects a state-led
    recovery from accepting an omitted or invented successor.  Join progress
    is treated as the durable arrival set already carried by the current
    state, while a current target cause proves that an older partial Join was
    consumed on this transition.
    """

    if state.superstep == 0:
        return None
    previous = tuple(
        reference for reference in state.settled_activations if reference.activation.superstep == state.superstep - 1
    )
    if not previous:
        return "non-initial frontier has no committed predecessor settlements"
    candidates: dict[GraphNodeId, list[RoutedActivationCause]] = {}
    for reference in previous:
        source = reference.activation.node_id
        if source not in graph.nodes:
            # Keep this private helper total even when called directly by an
            # owner-level diagnostic or a malformed snapshot bypassing the
            # outer admission function.
            return f"settled activation references unknown node {source!r}"
        routes = graph.transition.conditional_targets[source]
        if routes and reference.route is None:
            return "conditional predecessor settlement lacks its selected route"
        if reference.route is not None and reference.route not in routes:
            return "predecessor settlement selected an unknown route"
        for target in graph.transition.direct_targets[source]:
            _append_successor_candidate(candidates, target, RoutedActivationCause((reference,)))
        if reference.route is not None:
            target = routes[reference.route]
            if target != END:
                _append_successor_candidate(candidates, target, RoutedActivationCause((reference,)))

    actual = {
        node.node_id: node.cause for node in state.frontier.nodes if isinstance(node.cause, RoutedActivationCause)
    }
    expected_progress: dict[GraphJoinOccurrenceIdentity, tuple[ActivationReference, ...]] = {}
    try:
        _pending_join_arrivals(graph, state)
        join_arrivals = _historical_join_arrivals(graph, state)
    except (JoinProgressError, SnapshotMismatchError) as error:
        return str(error)
    for occurrence, arrivals in join_arrivals.items():
        source_ids = tuple(reference.activation.node_id for reference in arrivals)
        identity = occurrence.join
        complete = set(source_ids) == set(identity.sources)
        if occurrence.target_superstep < state.superstep:
            if not complete:
                return "historical Join occurrence passed its target without every source"
            continue
        if complete:
            if occurrence.target_superstep != state.superstep:
                return "Join occurrence completed before its target coordinate"
            if identity.target != END:
                _append_successor_candidate(
                    candidates,
                    identity.target,
                    RoutedActivationCause(arrivals, occurrence),
                )
            continue
        if occurrence.target_superstep == state.superstep:
            return "Join occurrence reached its target coordinate without every source"
        expected_progress[occurrence] = arrivals

    actual_progress = {progress.occurrence: progress.arrived for progress in state.join_progress}
    if set(actual_progress) != set(expected_progress):
        return "frontier transition lost or invented Join progress"
    for occurrence, arrivals in expected_progress.items():
        if actual_progress[occurrence] != arrivals:
            return "frontier transition changed Join progress without a proven arrival"

    for target, target_candidates in candidates.items():
        if len(target_candidates) != 1:
            return f"target {target!r} has {len(target_candidates)} compiled activation causes"
        if actual.get(target) != target_candidates[0]:
            return f"frontier target {target!r} does not match its compiled successor cause"
    unexpected = tuple(sorted(set(actual) - set(candidates)))
    if unexpected:
        return f"frontier contains an unproved successor target: {unexpected!r}"
    return None


def frontier_admission_error(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> str | None:
    """Return a deterministic topology/provenance error for one snapshot."""

    ledger_error = settled_activation_admission_error(graph, state)
    if ledger_error is not None:
        return ledger_error
    if state.status is GraphRunStatus.COMPLETED:
        return None
    return _frontier_gate_error(graph, state) or _post_advance_error(graph, state)


def graph_outputs_available(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    completion_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
) -> bool:
    return not unavailable_graph_outputs(graph, scope_run, completion_superstep, frames)


def unavailable_graph_outputs(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    completion_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
) -> tuple[str, ...]:
    unavailable: list[str] = []
    for binding in graph.transition.graph_outputs.entries:
        source = binding.source
        if not _value_available(graph, scope_run, source, binding.publication, completion_superstep, frames):
            unavailable.append(f"{binding.destination.boundary_name}<-{_source_label(source)}")
    return tuple(unavailable)


def _required_target(
    graph: CompiledGraph[GraphValueT],
    target: GraphNodeId,
    activation: GraphFrontierActivation,
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    activation_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
) -> RequiredTarget:
    if activation.node_id != target:
        raise InvalidRoutingCommandError("compiled successor activation does not match its target")
    unavailable: list[str] = []
    for binding in graph.transition.materializations[target].bindings.entries:
        source = binding.source
        publication = binding.publication
        if isinstance(source, CompiledActivationRule):
            selected = feedback_source_for_cause(
                state,
                target,
                activation_superstep,
                activation.cause,
                source,
            )
            source = selected.source
            publication = selected.publication
        if not _value_available(graph, scope_run, source, publication, activation_superstep, frames):
            unavailable.append(f"{binding.destination.local_name}<-{_source_label(source)}")
    return RequiredTarget(target, tuple(unavailable))


def _resolve_control(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> _ControlResolution:
    """Resolve the sole compiled control successor and Join progression."""

    arrivals = _pending_join_arrivals(graph, state)
    direct_control_targets: set[GraphNodeId] = set()
    completed_join_targets: set[GraphNodeId] = set()
    candidates: dict[GraphNodeId, list[RoutedActivationCause]] = {}

    for node_id, contribution in routing_contributions(state.frontier):
        validate_routing_contribution(graph, node_id, contribution)
        for feedback_rule in graph.transition.activation_rules.for_target(node_id):
            require_feedback_activation_cause(state, node_id, feedback_rule)
        selected_route = contribution.route if isinstance(contribution, SelectGraphRoute) else None
        source_activation = GraphActivationIdentity(state.run_id, state.superstep, node_id)
        reference = ActivationReference(source_activation, selected_route)
        for target in graph.transition.direct_targets[node_id]:
            direct_control_targets.add(target)
            _append_successor_candidate(candidates, target, RoutedActivationCause((reference,)))
        if isinstance(contribution, SelectGraphRoute):
            target = graph.transition.conditional_targets[node_id][contribution.route]
            if target != END:
                direct_control_targets.add(target)
                _append_successor_candidate(candidates, target, RoutedActivationCause((reference,)))
        for plan in graph.transition.joins_by_source[node_id]:
            occurrence = plan.occurrence_for(source_activation)
            join_arrivals = arrivals.setdefault(occurrence, [])
            if any(item.activation.node_id == node_id for item in join_arrivals):
                raise JoinProgressError("join source activation occurrence repeated")
            join_arrivals.append(reference)
    remaining: list[GraphJoinProgress] = []
    consumed_progress: list[GraphJoinOccurrenceIdentity] = []
    prior_occurrences = frozenset(progress.occurrence for progress in state.join_progress)
    for occurrence in sorted(arrivals):
        identity = occurrence.join
        arrived = tuple(sorted(arrivals[occurrence], key=ActivationReference.canonical_key))
        arrived_sources = tuple(reference.activation.node_id for reference in arrived)
        if set(arrived_sources) == set(identity.sources):
            if occurrence.target_superstep != state.superstep + 1:
                raise JoinProgressError("completed Join occurrence has the wrong target coordinate")
            if identity.target != END:
                completed_join_targets.add(identity.target)
                _append_successor_candidate(
                    candidates,
                    identity.target,
                    RoutedActivationCause(arrived, occurrence),
                )
            elif occurrence in prior_occurrences:
                consumed_progress.append(occurrence)
        else:
            if occurrence.target_superstep <= state.superstep + 1:
                raise JoinProgressError("partial Join occurrence cannot reach its target coordinate")
            remaining.append(GraphJoinProgress(occurrence, arrived))

    activations_by_target: dict[GraphNodeId, GraphFrontierActivation] = {}
    for target, target_candidates in candidates.items():
        if len(target_candidates) != 1:
            raise InvalidRoutingCommandError(
                f"target {target!r} has {len(target_candidates)} activation causes in one frontier"
            )
        activations_by_target[target] = GraphFrontierActivation(target, target_candidates[0])

    return _ControlResolution(
        frozenset(direct_control_targets),
        frozenset(completed_join_targets),
        tuple(remaining),
        tuple(activations_by_target[target] for target in sorted(activations_by_target)),
        tuple(sorted(consumed_progress)),
    )


def transition_admission_error(
    graph: CompiledGraph[GraphValueT],
    previous_state: GraphRunState | None,
    command: GraphRunCommand,
    candidate_state: GraphRunState,
) -> str | None:
    """Validate topology facts that cannot survive a terminal State reduction."""

    candidate_error = frontier_admission_error(graph, candidate_state)
    if candidate_error is not None or candidate_state.status is not GraphRunStatus.COMPLETED:
        return candidate_error
    if previous_state is None or type(command) is not CompleteGraphFrontier:
        return "completed graph state lacks its admitted completion transition"
    previous_error = frontier_admission_error(graph, previous_state)
    if previous_error is not None:
        return previous_error
    try:
        control = _resolve_control(graph, previous_state)
    except (InvalidRoutingCommandError, JoinProgressError, SnapshotMismatchError) as error:
        return str(error)
    if control.activations or control.remaining_join_progress:
        return "graph completion discarded a compiled successor or partial Join occurrence"
    if control.consumed_join_progress != command.consumed_join_progress:
        return "graph completion consumed the wrong Join occurrences"
    return None


def resolve_routing_facts(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
) -> RoutingFacts:
    if frontier_status(state.frontier) is not GraphFrontierStatus.SETTLED:
        raise InvalidRoutingCommandError("routing requires a settled frontier without failures or interrupts")
    admission_error = frontier_admission_error(graph, state)
    if admission_error is not None:
        raise InvalidRoutingCommandError(admission_error)
    control = _resolve_control(graph, state)
    activation_by_target = {activation.node_id: activation for activation in control.activations}

    required_targets: dict[GraphNodeId, RequiredTarget] = {}
    for target in sorted(control.direct_targets | control.join_targets):
        activation = activation_by_target.get(target)
        if activation is None:
            raise InvalidRoutingCommandError(f"compiled successor target {target!r} lacks an admitted activation")
        required_targets[target] = _required_target(
            graph,
            target,
            activation,
            state,
            scope_run,
            state.superstep + 1,
            frames,
        )
    control_facts = tuple(required_targets[target] for target in sorted(control.direct_targets))
    completed_join_facts = tuple(required_targets[target] for target in sorted(control.join_targets))
    output_diagnostics = unavailable_graph_outputs(graph, scope_run, state.superstep, frames)
    return RoutingFacts(
        control_facts,
        completed_join_facts,
        control.remaining_join_progress,
        output_diagnostics,
        control.activations,
        control.consumed_join_progress,
    )


def project_routing_facts(state: GraphRunState, facts: RoutingFacts) -> ResolutionCommand:
    required_targets = facts.control_targets + facts.completed_join_targets
    control_targets = tuple(sorted(target.node_id for target in required_targets))
    unavailable_control = tuple(target.node_id for target in required_targets if target.unavailable_inputs)
    if unavailable_control:
        return AbortGraphRun(
            state.revision,
            GraphAbortReason(f"required values unavailable for controlled nodes {unavailable_control!r}"),
        )
    if control_targets:
        return AdvanceGraphFrontier(
            state.revision,
            facts.activations,
            facts.remaining_join_progress,
            facts.consumed_join_progress,
        )
    if facts.remaining_join_progress:
        raise RoutingDeadlockError("partial join progress has no next task able to complete it")
    if facts.unavailable_graph_outputs:
        return AbortGraphRun(
            state.revision,
            GraphAbortReason("required graph output values are unavailable at completion"),
        )
    return CompleteGraphFrontier(state.revision, facts.consumed_join_progress)


def resolve_routing(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
) -> ResolutionCommand:
    return project_routing_facts(state, resolve_routing_facts(graph, state, scope_run, frames))


__all__ = [
    "_declared_joins",
    "_graph_input_coordinate",
    "_node_output_coordinate",
    "_success_routes",
    "frontier_admission_error",
    "settled_activation_admission_error",
]
