"""Pure whole-invocation admission for resume substitution candidates."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.routing import resolve_routing_facts
from mote_kernel.execution.errors import GraphValuePublicationError, GraphValueUnavailableError, SnapshotMismatchError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.run_context import (
    AdmittedSubstitution,
    CandidateFrameAvailability,
    PublicationAvailabilityCoordinate,
    ScopedFrameIndex,
    SkipSubstitutionProvenance,
)
from mote_kernel.state.graph_state import (
    GraphNodeId,
    GraphRunState,
    ResumeGraphNodes,
    SelectGraphRoute,
    SkipFailedNode,
    SkippedGraphNode,
    frontier_node,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class ScopedResumeCandidate(Generic[GraphValueT]):
    graph: CompiledGraph[GraphValueT]
    scope_run: ScopeRunCoordinate
    previous: GraphRunState
    successor: GraphRunState
    substitutions: tuple[AdmittedSubstitution[GraphValueT], ...]
    command: ResumeGraphNodes


def admit_resume_candidates(
    candidates: tuple[ScopedResumeCandidate[GraphValueT], ...],
    frames: ScopedFrameIndex[GraphValueT],
) -> CandidateFrameAvailability[GraphValueT]:
    substitutions = tuple(substitution for candidate in candidates for substitution in candidate.substitutions)
    candidate_skip_actions: list[tuple[SkipFailedNode, ...]] = []
    for candidate in candidates:
        if candidate.previous.run_id != candidate.scope_run.graph_run_id or candidate.successor.run_id != (
            candidate.scope_run.graph_run_id
        ):
            raise SnapshotMismatchError("resume candidate states do not match their scoped graph run")
        if reduce_graph_run(candidate.previous, candidate.command) != candidate.successor:
            raise SnapshotMismatchError("resume candidate successor is not the exact command reduction")
        skip_actions = tuple(action for action in candidate.command.actions if isinstance(action, SkipFailedNode))
        candidate_skip_actions.append(skip_actions)
        for substitution in candidate.substitutions:
            activation = substitution.coordinate.activation
            try:
                publication = candidate.graph.transition.publications[activation.node_id]
            except KeyError as error:
                raise SnapshotMismatchError("resume substitution references an unknown publication node") from error
            node = frontier_node(candidate.successor.frontier, activation.node_id)
            action = next(
                (action for action in skip_actions if action.node_id == activation.node_id),
                None,
            )
            route = (
                node.settlement.routing.route
                if node is not None
                and isinstance(node.settlement, SkippedGraphNode)
                and isinstance(node.settlement.routing, SelectGraphRoute)
                else None
            )
            action_route = (
                action.routing.route if action is not None and isinstance(action.routing, SelectGraphRoute) else None
            )
            if (
                activation.scope_run != candidate.scope_run
                or activation.superstep != candidate.previous.superstep
                or substitution.expected_revision != candidate.successor.revision
                or type(substitution.provenance) is not SkipSubstitutionProvenance
                or substitution.coordinate.descriptor != publication.identity
                or node is None
                or not isinstance(node.settlement, SkippedGraphNode)
                or action is None
                or node.settlement.reason != action.reason
                or route != action_route
            ):
                raise SnapshotMismatchError("resume substitution evidence does not match its admitted scoped successor")
    canonical = tuple(sorted(substitutions, key=lambda substitution: substitution.coordinate))
    publication_counts: dict[PublicationAvailabilityCoordinate[GraphValueT], int] = {}
    duplicate_coordinates: list[PublicationAvailabilityCoordinate[GraphValueT]] = []
    collision_nodes: list[GraphNodeId] = []
    for substitution in canonical:
        coordinate = substitution.coordinate
        count = publication_counts.get(coordinate, 0) + 1
        publication_counts[coordinate] = count
        if count == 2:
            duplicate_coordinates.append(coordinate)
        if frames.has_publication(coordinate):
            collision_nodes.append(coordinate.activation.node_id)
    if duplicate_coordinates:
        duplicates = tuple(sorted({coordinate.activation.node_id for coordinate in duplicate_coordinates}))
        raise GraphValuePublicationError(
            f"resume substitution nodes {duplicates!r} supplied duplicate publication coordinates"
        )
    if collision_nodes:
        collisions = tuple(sorted(collision_nodes))
        raise GraphValuePublicationError(
            f"resume substitution nodes {collisions!r} collide with confirmed publications"
        )
    availability = CandidateFrameAvailability(frames, canonical)
    for candidate, skip_actions in zip(candidates, candidate_skip_actions, strict=True):
        facts = resolve_routing_facts(candidate.graph, candidate.successor, candidate.scope_run, availability)
        unavailable = tuple(
            sorted(
                {
                    (target.node_id, target.unavailable_inputs)
                    for target in (*facts.control_targets, *facts.completed_join_targets, *facts.data_targets)
                    if target.unavailable_inputs
                }
            )
        )
        if unavailable:
            raise GraphValueUnavailableError(
                f"resume of scoped graph {candidate.scope_run.scope!r} "
                f"for actions {tuple(action.node_id for action in skip_actions)!r} "
                f"leaves required nodes and consumer inputs {unavailable!r} unavailable"
            )
        skip_publication_coordinates: set[PublicationAvailabilityCoordinate[GraphValueT]] = {
            PublicationAvailabilityCoordinate(
                StableActivation(candidate.scope_run, candidate.previous.superstep, action.node_id),
                candidate.graph.transition.publications[action.node_id].identity,
            )
            for action in skip_actions
        }
        pure_skip_coordinates = skip_publication_coordinates.difference(
            substitution.coordinate for substitution in candidate.substitutions
        )
        if (
            pure_skip_coordinates
            and not any(
                (
                    facts.control_targets,
                    facts.completed_join_targets,
                    facts.remaining_join_progress,
                    facts.data_targets,
                )
            )
            and facts.unavailable_graph_outputs
        ):
            actions = tuple(action.node_id for action in skip_actions)
            raise GraphValueUnavailableError(
                f"resume actions {actions!r} in scoped graph {candidate.scope_run.scope!r} "
                f"leave graph outputs/bindings {facts.unavailable_graph_outputs!r} unavailable"
            )
    return availability


__all__: list[str] = []
