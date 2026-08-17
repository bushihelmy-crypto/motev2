"""Unique compiled control/data resolver for one settled frontier."""

from dataclasses import dataclass
from typing import TypeAlias, TypeVar

from mote_kernel.execution.engine.resume_input import node_inputs_available
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
    GraphAbortReason,
    GraphJoinProgress,
    GraphNodeId,
    GraphRoutingContribution,
    GraphRunState,
    SelectGraphRoute,
    SkippedGraphNode,
    SucceededGraphNode,
    routing_contributions,
)

GraphValueT = TypeVar("GraphValueT")
ResolutionCommand: TypeAlias = AdvanceGraphFrontier | CompleteGraphFrontier | AbortGraphRun


@dataclass(frozen=True, slots=True)
class RoutingResolution:
    command: ResolutionCommand
    selected_control_targets: tuple[GraphNodeId, ...]
    selected_data_targets: tuple[GraphNodeId, ...]
    unavailable_control_targets: tuple[GraphNodeId, ...]
    completion_outputs_available: bool


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


def plan_routing(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
) -> RoutingResolution:
    declared = _declared_joins(graph)
    arrivals: dict[tuple[tuple[GraphNodeId, ...], GraphNodeId], set[GraphNodeId]] = {}
    for progress in state.join_progress:
        key = _join_key(progress.sources, progress.target)
        edge = declared.get(key)
        if edge is None or key in arrivals or not progress.arrived or not progress.arrived < frozenset(edge.sources):
            raise JoinProgressError("snapshot contains invalid join progress")
        arrivals[key] = set(progress.arrived)
    control_targets: set[GraphNodeId] = set()
    data_targets: set[GraphNodeId] = set()
    for node_id, contribution in routing_contributions(state.frontier):
        validate_routing_contribution(graph, node_id, contribution)
        control_targets.update(graph.transition.direct_targets[node_id])
        if isinstance(contribution, SelectGraphRoute):
            target = graph.transition.conditional_targets[node_id][contribution.route]
            if target != END:
                control_targets.add(target)
        for edge in graph.transition.joins_by_source[node_id]:
            arrivals.setdefault(_join_key(edge.sources, edge.target), set()).add(node_id)
    for node in state.frontier.nodes:
        if isinstance(node.settlement, SucceededGraphNode):
            data_targets.update(graph.transition.data_triggers[node.node_id].targets)
        elif isinstance(node.settlement, SkippedGraphNode):
            continue
    remaining: list[GraphJoinProgress] = []
    for key in sorted(arrivals):
        edge = declared[key]
        arrived = arrivals[key]
        if arrived == set(edge.sources):
            if edge.target != END:
                control_targets.add(edge.target)
        else:
            remaining.append(GraphJoinProgress(edge.sources, edge.target, frozenset(arrived)))
    unavailable_control = tuple(
        sorted(
            target
            for target in control_targets
            if not node_inputs_available(
                graph,
                scope_run,
                state.superstep + 1,
                frames,
                target,
            )
        )
    )
    if unavailable_control:
        return RoutingResolution(
            AbortGraphRun(
                state.revision,
                GraphAbortReason(f"required values unavailable for controlled nodes {unavailable_control!r}"),
            ),
            tuple(sorted(control_targets)),
            (),
            unavailable_control,
            True,
        )
    ready_data = {
        target
        for target in data_targets
        if node_inputs_available(
            graph,
            scope_run,
            state.superstep + 1,
            frames,
            target,
        )
    }
    next_nodes = control_targets | ready_data
    if next_nodes:
        return RoutingResolution(
            AdvanceGraphFrontier(state.revision, tuple(sorted(next_nodes)), tuple(remaining)),
            tuple(sorted(control_targets)),
            tuple(sorted(ready_data)),
            (),
            True,
        )
    if remaining:
        raise RoutingDeadlockError("partial join progress has no next task able to complete it")
    if not graph_outputs_available(graph, scope_run, state.superstep, frames):
        return RoutingResolution(
            AbortGraphRun(
                state.revision,
                GraphAbortReason("required graph output values are unavailable at completion"),
            ),
            (),
            (),
            (),
            False,
        )
    return RoutingResolution(CompleteGraphFrontier(state.revision), (), (), (), True)


def resolve_routing(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
) -> ResolutionCommand:
    return plan_routing(graph, state, scope_run, frames).command


__all__: list[str] = []
