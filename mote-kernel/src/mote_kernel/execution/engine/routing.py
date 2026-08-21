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
from mote_kernel.execution.graph.ports import GraphInputPort, require_publication_selection
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
    GraphRoutingContribution,
    GraphRunState,
    InterruptedGraphNode,
    SelectGraphRoute,
    SkippedGraphNode,
    SucceededGraphNode,
    routing_contributions,
)

GraphValueT = TypeVar("GraphValueT")
ResolutionCommand: TypeAlias = AdvanceGraphFrontier | CompleteGraphFrontier | AbortGraphRun


@dataclass(frozen=True, slots=True)
class RequiredTarget:
    node_id: GraphNodeId
    inputs_available: bool
    historical_inputs_missing: bool
    unavailable_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoutingFacts:
    control_targets: tuple[RequiredTarget, ...]
    completed_join_targets: tuple[RequiredTarget, ...]
    remaining_join_progress: tuple[GraphJoinProgress, ...]
    data_targets: tuple[RequiredTarget, ...]
    unavailable_graph_outputs: tuple[str, ...]


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
    for binding in graph.graph_outputs.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            graph_input_coordinate: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
                scope_run,
                graph.graph_input_descriptor.identity,
            )
            if not frames.has_graph_input(graph_input_coordinate):
                return False
        else:
            selection = require_publication_selection(
                binding.publication,
                InvalidRoutingCommandError("compiled graph output binding lacks its activation selection"),
            )
            publication_coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
                StableActivation(
                    scope_run,
                    selection.resolve(completion_superstep),
                    source.node_id,
                ),
                graph.publications[source.node_id].descriptor.identity,
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
    for binding in graph.graph_outputs.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            graph_input_coordinate: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
                scope_run, graph.graph_input_descriptor.identity
            )
            if not frames.has_graph_input(graph_input_coordinate):
                unavailable.append(f"{binding.destination.boundary_name}<-graph-input:{source.name}")
            continue
        selection = require_publication_selection(
            binding.publication,
            InvalidRoutingCommandError("compiled graph output binding lacks its activation selection"),
        )
        publication_coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
            StableActivation(scope_run, selection.resolve(completion_superstep), source.node_id),
            graph.publications[source.node_id].descriptor.identity,
        )
        if not frames.has_publication(publication_coordinate):
            unavailable.append(f"{binding.destination.boundary_name}<-{source.node_id}.{source.output_name}")
    return tuple(unavailable)


def unavailable_target_inputs(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
    node_id: GraphNodeId,
) -> tuple[str, ...]:
    unavailable: list[str] = []
    for binding in graph.materializations[node_id].bindings.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            graph_input_coordinate: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
                scope_run, graph.graph_input_descriptor.identity
            )
            if not frames.has_graph_input(graph_input_coordinate):
                unavailable.append(f"{binding.destination.local_name}<-graph-input:{source.name}")
            continue
        selection = require_publication_selection(
            binding.publication,
            InvalidRoutingCommandError("compiled node-output binding lacks its activation selection"),
        )
        publication_coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
            StableActivation(scope_run, selection.resolve(state.superstep + 1), source.node_id),
            graph.publications[source.node_id].descriptor.identity,
        )
        if not frames.has_publication(publication_coordinate):
            unavailable.append(f"{binding.destination.local_name}<-{source.node_id}.{source.output_name}")
    return tuple(unavailable)


def _target_has_historical_gap(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
    node_id: GraphNodeId,
) -> bool:
    unavailable = False
    for binding in graph.materializations[node_id].bindings.entries:
        source = binding.source
        if isinstance(source, GraphInputPort):
            graph_input_coordinate: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
                scope_run, graph.graph_input_descriptor.identity
            )
            unavailable = unavailable or not frames.has_graph_input(graph_input_coordinate)
            continue
        selection = require_publication_selection(
            binding.publication,
            InvalidRoutingCommandError("compiled node-output binding lacks its activation selection"),
        )
        publication_coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
            StableActivation(scope_run, selection.resolve(state.superstep + 1), source.node_id),
            graph.publications[source.node_id].descriptor.identity,
        )
        if frames.has_publication(publication_coordinate):
            continue
        current = next((node for node in state.frontier.nodes if node.node_id == source.node_id), None)
        unavailable = unavailable or not (
            current is not None
            and isinstance(current.settlement, FailedGraphNode | InterruptedGraphNode | SkippedGraphNode)
        )
    return unavailable


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
    data_targets: set[GraphNodeId] = set()
    for node_id, contribution in routing_contributions(state.frontier):
        validate_routing_contribution(graph, node_id, contribution)
        direct_control_targets.update(graph.transition.direct_targets[node_id])
        if isinstance(contribution, SelectGraphRoute):
            target = graph.transition.conditional_targets[node_id][contribution.route]
            if target != END:
                direct_control_targets.add(target)
        for edge in graph.transition.joins_by_source[node_id]:
            arrivals.setdefault(_join_key(edge.sources, edge.target), set()).add(node_id)
    for node in state.frontier.nodes:
        publication_coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
            StableActivation(scope_run, state.superstep, node.node_id),
            graph.publications[node.node_id].descriptor.identity,
        )
        if isinstance(node.settlement, (SucceededGraphNode, SkippedGraphNode)) and frames.has_publication(
            publication_coordinate
        ):
            data_targets.update(graph.transition.data_triggers[node.node_id].targets)
    remaining: list[GraphJoinProgress] = []
    for key in sorted(arrivals):
        edge = declared[key]
        arrived = arrivals[key]
        if arrived == set(edge.sources):
            if edge.target != END:
                completed_join_targets.add(edge.target)
        else:
            remaining.append(GraphJoinProgress(edge.sources, edge.target, frozenset(arrived)))

    def required(target: GraphNodeId) -> RequiredTarget:
        unavailable = unavailable_target_inputs(graph, state, scope_run, frames, target)
        return RequiredTarget(
            target,
            not unavailable,
            _target_has_historical_gap(graph, state, scope_run, frames, target),
            unavailable,
        )

    control_facts = tuple(required(target) for target in sorted(direct_control_targets))
    completed_join_facts = tuple(required(target) for target in sorted(completed_join_targets))
    data_facts = tuple(required(target) for target in sorted(data_targets))
    output_diagnostics = unavailable_graph_outputs(graph, scope_run, state.superstep, frames)
    return RoutingFacts(
        control_facts,
        completed_join_facts,
        tuple(remaining),
        data_facts,
        output_diagnostics,
    )


def project_routing_facts(state: GraphRunState, facts: RoutingFacts) -> ResolutionCommand:
    control_targets = {target.node_id for target in (*facts.control_targets, *facts.completed_join_targets)}
    unavailable_control = tuple(
        target.node_id
        for target in (*facts.control_targets, *facts.completed_join_targets)
        if not target.inputs_available
    )
    if unavailable_control:
        return AbortGraphRun(
            state.revision,
            GraphAbortReason(f"required values unavailable for controlled nodes {unavailable_control!r}"),
        )
    ready_data = {target.node_id for target in facts.data_targets if target.inputs_available}
    next_nodes = control_targets | ready_data
    if next_nodes:
        return AdvanceGraphFrontier(
            state.revision,
            tuple(sorted(next_nodes)),
            facts.remaining_join_progress,
        )
    if facts.remaining_join_progress:
        raise RoutingDeadlockError("partial join progress has no next task able to complete it")
    if (
        not any(
            (
                facts.control_targets,
                facts.completed_join_targets,
                facts.remaining_join_progress,
                facts.data_targets,
            )
        )
        and facts.unavailable_graph_outputs
    ):
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


__all__ = ["_declared_joins"]
