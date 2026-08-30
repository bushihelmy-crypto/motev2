"""Unique compiled control/data resolver for one settled frontier."""

from dataclasses import dataclass
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
    GraphInputPort,
    NodeOutputPort,
    require_publication_selection,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.run_context import (
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedFrameAvailability,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphAbortReason,
    GraphJoinProgress,
    GraphNodeId,
    GraphRouteId,
    GraphRoutingContribution,
    GraphRunState,
    InterruptedGraphNode,
    SelectGraphRoute,
    SkippedGraphNode,
    routing_contributions,
)

GraphValueT = TypeVar("GraphValueT")
ResolutionCommand: TypeAlias = AdvanceGraphFrontier | CompleteGraphFrontier | AbortGraphRun


@dataclass(frozen=True, slots=True)
class RequiredTarget:
    node_id: GraphNodeId
    historical_inputs_missing: bool
    unavailable_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoutingFacts:
    control_targets: tuple[RequiredTarget, ...]
    completed_join_targets: tuple[RequiredTarget, ...]
    remaining_join_progress: tuple[GraphJoinProgress, ...]
    unavailable_graph_outputs: tuple[str, ...]


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
        StableActivation(scope_run, superstep, source.node_id),
        graph.transition.publications[source.node_id].identity,
    )


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


def _declared_joins(
    graph: CompiledGraph[GraphValueT],
) -> dict[tuple[tuple[GraphNodeId, ...], GraphNodeId], JoinEdge]:
    return {
        _join_key(edge.sources, edge.target): edge
        for edges in graph.transition.joins_by_source.values()
        for edge in edges
    }


def graph_outputs_available(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    completion_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
) -> bool:
    for binding in graph.transition.graph_outputs.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            graph_input_coordinate = _graph_input_coordinate(graph, scope_run)
            if not frames.has_graph_input(graph_input_coordinate):
                return False
        else:
            selection = require_publication_selection(
                binding.publication,
                InvalidRoutingCommandError("compiled graph output binding lacks its activation selection"),
            )
            publication_coordinate = _node_output_coordinate(
                graph, scope_run, source, selection.resolve(completion_superstep)
            )
            if not frames.has_publication(publication_coordinate):
                return False
    return True


def unavailable_graph_outputs(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    completion_superstep: int,
    frames: ScopedFrameAvailability[GraphValueT],
) -> tuple[str, ...]:
    unavailable: list[str] = []
    for binding in graph.transition.graph_outputs.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            graph_input_coordinate = _graph_input_coordinate(graph, scope_run)
            if not frames.has_graph_input(graph_input_coordinate):
                unavailable.append(f"{binding.destination.boundary_name}<-graph-input:{source.name}")
            continue
        selection = require_publication_selection(
            binding.publication,
            InvalidRoutingCommandError("compiled graph output binding lacks its activation selection"),
        )
        publication_coordinate = _node_output_coordinate(
            graph, scope_run, source, selection.resolve(completion_superstep)
        )
        if not frames.has_publication(publication_coordinate):
            unavailable.append(f"{binding.destination.boundary_name}<-{source.node_id}.{source.output_name}")
    return tuple(unavailable)


def resolve_routing_facts(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
) -> RoutingFacts:
    declared = _declared_joins(graph)
    arrivals: dict[tuple[tuple[GraphNodeId, ...], GraphNodeId], set[GraphNodeId]] = {}
    for progress in state.join_progress:
        key = _join_key(progress.sources, progress.target)
        edge = declared.get(key)
        if edge is None or key in arrivals or not progress.arrived or not progress.arrived < frozenset(edge.sources):
            raise JoinProgressError("snapshot contains invalid join progress")
        arrivals[key] = set(progress.arrived)
    direct_control_targets: set[GraphNodeId] = set()
    completed_join_targets: set[GraphNodeId] = set()
    for node_id, contribution in routing_contributions(state.frontier):
        validate_routing_contribution(graph, node_id, contribution)
        direct_control_targets.update(graph.transition.direct_targets[node_id])
        if isinstance(contribution, SelectGraphRoute):
            target = graph.transition.conditional_targets[node_id][contribution.route]
            if target != END:
                direct_control_targets.add(target)
        for edge in graph.transition.joins_by_source[node_id]:
            arrivals.setdefault(_join_key(edge.sources, edge.target), set()).add(node_id)
    remaining: list[GraphJoinProgress] = []
    for key in sorted(arrivals):
        edge = declared[key]
        arrived = arrivals[key]
        if arrived == set(edge.sources):
            if edge.target != END:
                completed_join_targets.add(edge.target)
        else:
            remaining.append(GraphJoinProgress(edge.sources, edge.target, frozenset(arrived)))

    required_targets: dict[GraphNodeId, RequiredTarget] = {}

    def required(target: GraphNodeId) -> RequiredTarget:
        cached = required_targets.get(target)
        if cached is not None:
            return cached
        historical_inputs_missing = False
        unavailable_inputs: list[str] = []
        for binding in graph.transition.materializations[target].bindings.entries:
            source = binding.source
            if isinstance(source, GraphInputPort):
                graph_input_coordinate = _graph_input_coordinate(graph, scope_run)
                if not frames.has_graph_input(graph_input_coordinate):
                    unavailable_inputs.append(f"{binding.destination.local_name}<-graph-input:{source.name}")
                    historical_inputs_missing = True
                continue
            selection = require_publication_selection(
                binding.publication,
                InvalidRoutingCommandError("compiled node-output binding lacks its activation selection"),
            )
            publication_coordinate = _node_output_coordinate(
                graph, scope_run, source, selection.resolve(state.superstep + 1)
            )
            if frames.has_publication(publication_coordinate):
                continue
            unavailable_inputs.append(f"{binding.destination.local_name}<-{source.node_id}.{source.output_name}")
            current = next((node for node in state.frontier.nodes if node.node_id == source.node_id), None)
            historical_inputs_missing = historical_inputs_missing or not (
                current is not None
                and isinstance(current.settlement, FailedGraphNode | InterruptedGraphNode | SkippedGraphNode)
            )
        resolved = RequiredTarget(target, historical_inputs_missing, tuple(unavailable_inputs))
        required_targets[target] = resolved
        return resolved

    control_facts = tuple(required(target) for target in sorted(direct_control_targets))
    completed_join_facts = tuple(required(target) for target in sorted(completed_join_targets))
    output_diagnostics = unavailable_graph_outputs(graph, scope_run, state.superstep, frames)
    return RoutingFacts(
        control_facts,
        completed_join_facts,
        tuple(remaining),
        output_diagnostics,
    )


def project_routing_facts(state: GraphRunState, facts: RoutingFacts) -> ResolutionCommand:
    required_targets = facts.control_targets + facts.completed_join_targets
    control_targets = {target.node_id for target in required_targets}
    unavailable_control = tuple(target.node_id for target in required_targets if target.unavailable_inputs)
    if unavailable_control:
        return AbortGraphRun(
            state.revision,
            GraphAbortReason(f"required values unavailable for controlled nodes {unavailable_control!r}"),
        )
    if control_targets:
        return AdvanceGraphFrontier(
            state.revision,
            tuple(sorted(control_targets)),
            facts.remaining_join_progress,
        )
    if facts.remaining_join_progress:
        raise RoutingDeadlockError("partial join progress has no next task able to complete it")
    if facts.unavailable_graph_outputs:
        return AbortGraphRun(
            state.revision,
            GraphAbortReason("required graph output values are unavailable at completion"),
        )
    return CompleteGraphFrontier(state.revision)


def resolve_routing(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
) -> ResolutionCommand:
    return project_routing_facts(state, resolve_routing_facts(graph, state, scope_run, frames))


__all__ = ["_declared_joins", "_graph_input_coordinate", "_node_output_coordinate", "_success_routes"]
