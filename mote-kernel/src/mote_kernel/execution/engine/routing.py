"""Unique compiled control/data resolver for one settled frontier."""

from dataclasses import dataclass
from itertools import chain
from typing import TypeAlias, TypeVar

from mote_kernel.execution.errors import (
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    UnknownRouteError,
)
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.edge import JoinEdge
from mote_kernel.execution.graph.ports import (
    CompiledActivationRule,
    GraphInputPort,
    NodeOutputPort,
    PublicationSelection,
    PublicationSelectionKind,
    ResolvedValueSource,
    require_publication_selection,
)
from mote_kernel.execution.graph.topology import (
    ActivationGate,
    CompiledGraph,
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
    GraphActivationIdentity,
    GraphFrontierActivation,
    GraphFrontierStatus,
    GraphJoinProgress,
    GraphJoinProgressKey,
    GraphNodeId,
    GraphRouteId,
    GraphRoutingContribution,
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
    consumed_join_progress: tuple[GraphJoinProgressKey, ...]


@dataclass(frozen=True, slots=True)
class PublicationHistoryWindow:
    absolute_supersteps: tuple[int, ...]
    relative_horizon: int


def publication_history_window(graph: CompiledGraph[GraphValueT]) -> PublicationHistoryWindow:
    absolute_supersteps: set[int] = set()
    relative_horizon = 0
    selections = chain(
        (
            (
                binding.source.repeat_selection
                if isinstance(binding.source, CompiledActivationRule)
                else binding.publication
            )
            for _node_id, plan in graph.transition.materializations.entries
            for binding in plan.bindings.entries
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


def _success_routes(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
) -> tuple[GraphRouteId | None, ...]:
    routes = tuple(graph.transition.conditional_targets[node_id])
    return routes or (None,)


def _join_key(
    sources: tuple[GraphNodeId, ...],
    target: GraphNodeId,
) -> tuple[tuple[GraphNodeId, ...], GraphNodeId]:
    return (sources, target)


def require_feedback_activation_cause(
    state: GraphRunState,
    node_id: GraphNodeId,
    rule: CompiledActivationRule[GraphValueT],
) -> GraphActivationIdentity | None:
    """Validate one target's state-owned feedback cause and return its predecessor."""

    source = frontier_node(state.frontier, node_id)
    if source is None:
        raise InvalidRoutingCommandError("feedback activation is not present in the current frontier")
    if state.superstep == 0:
        if type(source.cause) is not StartActivationCause:
            raise InvalidRoutingCommandError("initial feedback activation must carry the START cause")
        return None
    if type(source.cause) is not RoutedActivationCause or len(source.cause.references) != 1:
        raise InvalidRoutingCommandError("feedback activation lacks one predecessor activation cause")
    reference = source.cause.references[0]
    expected = GraphActivationIdentity(state.run_id, state.superstep - 1, node_id)
    if reference.activation != expected or reference.route != rule.feedback_route:
        raise InvalidRoutingCommandError("feedback activation cause is not the immediate predecessor activation")
    if reference not in state.settled_activations:
        raise InvalidRoutingCommandError("feedback activation predecessor lacks committed settlement evidence")
    return reference.activation


def _feedback_rules(graph: CompiledGraph[GraphValueT]) -> dict[GraphNodeId, CompiledActivationRule[GraphValueT]]:
    return {rule.target: rule for rule in graph.transition.activation_rules.entries}


def _declared_joins(
    graph: CompiledGraph[GraphValueT],
) -> dict[tuple[tuple[GraphNodeId, ...], GraphNodeId], JoinEdge]:
    return {
        _join_key(edge.sources, edge.target): edge
        for edges in graph.transition.joins_by_source.values()
        for edge in edges
    }


def _gate_matches_cause(
    gate: ActivationGate,
    cause: RoutedActivationCause,
) -> bool:
    sources = tuple(
        sorted(
            ((reference.activation.node_id, reference.route) for reference in cause.references),
            key=lambda item: (item[0], item[1] is not None, item[1] or ""),
        )
    )
    if len(gate) != len(sources):
        return False
    return all(
        expected_source == actual_source and actual_route in allowed_routes
        for (expected_source, allowed_routes), (actual_source, actual_route) in zip(gate, sources, strict=True)
    )


def _frontier_gate_error(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> str | None:
    feedback_rules = _feedback_rules(graph)
    for node in state.frontier.nodes:
        if node.node_id not in graph.nodes:
            return f"frontier activation references unknown node {node.node_id!r}"
        cause = node.cause
        feedback_rule = feedback_rules.get(node.node_id)
        if feedback_rule is not None:
            try:
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
        if sum(_gate_matches_cause(gate, cause) for gate in gates) != 1:
            return f"frontier activation {node.node_id!r} does not match exactly one compiled activation gate"
        if any(reference not in state.settled_activations for reference in cause.references):
            return f"frontier activation {node.node_id!r} lacks committed predecessor settlement evidence"
    return None


def _append_successor_candidate(
    candidates: dict[GraphNodeId, list[tuple[ActivationReference, ...]]],
    target: GraphNodeId,
    references: tuple[ActivationReference, ...],
) -> None:
    candidates.setdefault(target, []).append(references)


def _join_arrivals_for_frontier(
    edge: JoinEdge,
    progress: GraphJoinProgress | None,
    previous: tuple[ActivationReference, ...],
) -> tuple[ActivationReference, ...]:
    arrivals = list(progress.arrived if progress is not None else ())
    for reference in previous:
        if reference.activation.node_id not in edge.sources:
            continue
        same_activation = tuple(item for item in arrivals if item.activation == reference.activation)
        if same_activation:
            if same_activation[0] != reference:
                raise JoinProgressError("join source activation occurrence selected two routes")
            continue
        arrivals.append(reference)
    return tuple(sorted(arrivals, key=ActivationReference.canonical_key))


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
    candidates: dict[GraphNodeId, list[tuple[ActivationReference, ...]]] = {}
    for reference in previous:
        source = reference.activation.node_id
        routes = graph.transition.conditional_targets[source]
        if routes and reference.route is None:
            return "conditional predecessor settlement lacks its selected route"
        if reference.route is not None and reference.route not in routes:
            return "predecessor settlement selected an unknown route"
        for target in graph.transition.direct_targets[source]:
            _append_successor_candidate(candidates, target, (reference,))
        if reference.route is not None:
            target = routes[reference.route]
            if target != END:
                _append_successor_candidate(candidates, target, (reference,))

    declared = _declared_joins(graph)
    progress_by_key = {_join_key(progress.sources, progress.target): progress for progress in state.join_progress}
    actual = {
        node.node_id: node.cause.references
        for node in state.frontier.nodes
        if isinstance(node.cause, RoutedActivationCause)
    }
    expected_progress: dict[GraphJoinProgressKey, tuple[ActivationReference, ...]] = {}
    for key, edge in declared.items():
        progress = progress_by_key.get(key)
        arrivals = _join_arrivals_for_frontier(edge, progress, previous)
        if not arrivals:
            continue
        source_ids = tuple(reference.activation.node_id for reference in arrivals)
        if len(source_ids) != len(set(source_ids)):
            return "Join source activation occurrence repeated"
        complete = set(source_ids) == set(edge.sources)
        target_cause = actual.get(edge.target)
        target_proves_join = (
            target_cause is not None
            and len(target_cause) == len(edge.sources)
            and {reference.activation.node_id for reference in target_cause} == set(edge.sources)
            and all(reference in state.settled_activations for reference in target_cause)
            and any(reference.activation.superstep == state.superstep - 1 for reference in target_cause)
        )
        if target_proves_join and target_cause is not None and not complete:
            # A completed Join may consume an older partial record on the
            # transition that creates its target.  The post-transition State
            # no longer carries that consumed record, so use the target's
            # complete cause as the proof for this edge.
            _append_successor_candidate(candidates, edge.target, target_cause)
            continue
        if complete:
            if edge.target == END:
                # A terminal Join has no frontier target.  Its complete source
                # evidence is the only durable proof available in this slice.
                continue
            _append_successor_candidate(candidates, edge.target, arrivals)
            continue
        expected_progress[key] = arrivals

    actual_progress = {
        _join_key(progress.sources, progress.target): progress.arrived for progress in state.join_progress
    }
    if set(actual_progress) != set(expected_progress):
        return "frontier transition lost or invented Join progress"
    for key, arrivals in expected_progress.items():
        if actual_progress[key] != arrivals:
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
    scope_run: ScopeRunCoordinate,
    activation_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
) -> RequiredTarget:
    unavailable: list[str] = []
    for binding in graph.transition.materializations[target].bindings.entries:
        source = binding.source
        publication = binding.publication
        if isinstance(source, CompiledActivationRule):
            publication = source.repeat_selection
            source = source.repeat
        if not _value_available(graph, scope_run, source, publication, activation_superstep, frames):
            unavailable.append(f"{binding.destination.local_name}<-{_source_label(source)}")
    return RequiredTarget(target, tuple(unavailable))


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
    feedback_rules = _feedback_rules(graph)
    declared = _declared_joins(graph)
    arrivals: dict[tuple[tuple[GraphNodeId, ...], GraphNodeId], list[ActivationReference]] = {}
    for progress in state.join_progress:
        key = _join_key(progress.sources, progress.target)
        edge = declared.get(key)
        arrived_sources = tuple(reference.activation.node_id for reference in progress.arrived)
        if edge is None or key in arrivals or not progress.arrived or not set(arrived_sources) < set(edge.sources):
            raise JoinProgressError("snapshot contains invalid join progress")
        if len(arrived_sources) != len(set(arrived_sources)):
            raise JoinProgressError("snapshot join progress repeats one source activation")
        if any(reference not in state.settled_activations for reference in progress.arrived):
            raise JoinProgressError("snapshot join progress lacks committed settlement evidence")
        arrivals[key] = list(progress.arrived)
    direct_control_targets: set[GraphNodeId] = set()
    completed_join_targets: set[GraphNodeId] = set()
    candidates: dict[GraphNodeId, list[tuple[ActivationReference, ...]]] = {}

    for node_id, contribution in routing_contributions(state.frontier):
        validate_routing_contribution(graph, node_id, contribution)
        feedback_rule = feedback_rules.get(node_id)
        if feedback_rule is not None:
            require_feedback_activation_cause(state, node_id, feedback_rule)
        selected_route = contribution.route if isinstance(contribution, SelectGraphRoute) else None
        source_activation = GraphActivationIdentity(state.run_id, state.superstep, node_id)
        reference = ActivationReference(source_activation, selected_route)
        for target in graph.transition.direct_targets[node_id]:
            direct_control_targets.add(target)
            _append_successor_candidate(candidates, target, (reference,))
        if isinstance(contribution, SelectGraphRoute):
            target = graph.transition.conditional_targets[node_id][contribution.route]
            if target != END:
                direct_control_targets.add(target)
                _append_successor_candidate(candidates, target, (reference,))
        for edge in graph.transition.joins_by_source[node_id]:
            key = _join_key(edge.sources, edge.target)
            join_arrivals = arrivals.setdefault(key, [])
            if any(reference.activation.node_id == node_id for reference in join_arrivals):
                raise JoinProgressError("join source activation occurrence repeated")
            join_arrivals.append(reference)
    remaining: list[GraphJoinProgress] = []
    consumed_progress: list[GraphJoinProgressKey] = []
    prior_progress_keys = frozenset(_join_key(progress.sources, progress.target) for progress in state.join_progress)
    for key in sorted(arrivals):
        edge = declared[key]
        arrived = tuple(sorted(arrivals[key], key=ActivationReference.canonical_key))
        arrived_sources = tuple(reference.activation.node_id for reference in arrived)
        if set(arrived_sources) == set(edge.sources):
            if edge.target != END:
                completed_join_targets.add(edge.target)
                _append_successor_candidate(candidates, edge.target, arrived)
            elif key in prior_progress_keys:
                consumed_progress.append(key)
        else:
            remaining.append(GraphJoinProgress(edge.sources, edge.target, arrived))

    activations_by_target: dict[GraphNodeId, GraphFrontierActivation] = {}
    for target, target_candidates in candidates.items():
        if len(target_candidates) != 1:
            raise InvalidRoutingCommandError(
                f"target {target!r} has {len(target_candidates)} activation causes in one frontier"
            )
        references = target_candidates[0]
        activations_by_target[target] = GraphFrontierActivation(target, RoutedActivationCause(references))

    required_targets = {
        target: _required_target(
            graph,
            target,
            scope_run,
            state.superstep + 1,
            frames,
        )
        for target in sorted(direct_control_targets | completed_join_targets)
    }
    control_facts = tuple(required_targets[target] for target in sorted(direct_control_targets))
    completed_join_facts = tuple(required_targets[target] for target in sorted(completed_join_targets))
    output_diagnostics = unavailable_graph_outputs(graph, scope_run, state.superstep, frames)
    return RoutingFacts(
        control_facts,
        completed_join_facts,
        tuple(remaining),
        output_diagnostics,
        tuple(activations_by_target[target] for target in sorted(activations_by_target)),
        tuple(sorted(consumed_progress)),
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
]
