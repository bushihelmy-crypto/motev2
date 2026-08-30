"""Pure preparation and whole-invocation admission for graph resumes."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.resume_input import (
    _require_node_materialization,
    _resume_input_coordinate,
    decode_resume_input,
    encode_resume_input,
    materialize_node_input,
    require_resume_input_binding,
)
from mote_kernel.execution.engine.routing import resolve_routing_facts, validate_routing_contribution
from mote_kernel.execution.engine.snapshot_guard import require_scoped_snapshot_matches_graph
from mote_kernel.execution.errors import GraphValuePublicationError, GraphValueUnavailableError, SnapshotMismatchError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import NodeInputFrame, _make_node_output_frame
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
)
from mote_kernel.execution.result import PreparedResume
from mote_kernel.execution.run_context import (
    AdmittedResumeInput,
    AdmittedSubstitution,
    CandidateFrameAvailability,
    PreparedSubstitution,
    PublicationAvailabilityCoordinate,
    ScopedFrameIndex,
    SkipSubstitutionProvenance,
)
from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    FailedGraphNode,
    GraphFrontierNode,
    GraphFrontierState,
    GraphNodeId,
    GraphNodeSettlement,
    GraphRouteId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResumeFailedNode,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    SelectGraphRoute,
    SkipFailedNode,
    SkippedGraphNode,
    UseStepRequestInput,
    frontier_node,
    graph_interrupt_id,
    reduce_graph_run,
)
from mote_kernel.state.graph_state.validation import validate_graph_frontier

GraphValueT = TypeVar("GraphValueT")


def _admit_override_resume_input(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
    override: OverrideNodeInput[GraphValueT],
) -> tuple[OverrideGraphNodeInput, NodeInputFrame[GraphValueT]]:
    binding = encode_resume_input(graph, override.values)
    frame = decode_resume_input(graph, node_id, bytes(binding.payload))
    return binding, frame


def prepare_resume(
    graph: CompiledGraph[GraphValueT],
    request: ResumeRequest[GraphValueT],
) -> PreparedResume[GraphValueT]:
    """Prepare one resume without creating a live execution owner."""

    state = request.state
    require_scoped_snapshot_matches_graph(graph, state, request.scope_run)
    if state.status is not GraphRunStatus.RUNNING or state.execution is not None or state.resources is not None:
        raise SnapshotMismatchError("resume requires one quiescent running graph")
    require_resume_input_binding(graph, state)
    requested_ids = tuple(action.node_id for action in request.actions)
    if (
        not requested_ids
        or requested_ids != tuple(sorted(set(requested_ids)))
        or any(action.scope != request.scope_run.scope for action in request.actions)
    ):
        raise SnapshotMismatchError("resume actions must be non-empty, distinct, canonical, and scoped")
    actions: list[ResumeFailedNode | ResumeInterruptedNode | SkipFailedNode] = []
    replacements: dict[GraphNodeId, GraphNodeSettlement] = {}
    admitted_inputs: list[AdmittedResumeInput[GraphValueT]] = []
    substitutions: list[PreparedSubstitution[GraphValueT]] = []
    for requested in request.actions:
        current = frontier_node(state.frontier, requested.node_id)
        if current is None:
            raise SnapshotMismatchError("resume request references an unknown frontier node")
        activation = StableActivation(request.scope_run, state.superstep, requested.node_id)
        if isinstance(requested, ResumeFailedNodeRequest | ResumeInterruptedNodeRequest):
            plan = _require_node_materialization(graph, requested.node_id)
            if isinstance(requested, ResumeFailedNodeRequest):
                if not isinstance(current.settlement, FailedGraphNode):
                    raise SnapshotMismatchError("failure resume requires a failed node")
                if isinstance(requested.input, OverrideNodeInput):
                    binding, frame = _admit_override_resume_input(graph, requested.node_id, requested.input)
                else:
                    binding = UseStepRequestInput()
                    frame = materialize_node_input(
                        graph,
                        state,
                        request.scope_run,
                        request.frames,
                        requested.node_id,
                        failed_retry_input=binding,
                    )
                actions.append(ResumeFailedNode(requested.node_id, binding))
                replacements[requested.node_id] = PendingGraphNode(binding)
            else:
                if not isinstance(current.settlement, InterruptedGraphNode):
                    raise SnapshotMismatchError("interrupt resume requires an interrupted node")
                identity = current.settlement.interrupt.identity
                if requested.interrupt_id != graph_interrupt_id(
                    identity.run_id,
                    identity.superstep,
                    identity.node_id,
                    identity.execution_generation,
                ):
                    raise SnapshotMismatchError("interrupt resume ID does not match the current node interrupt")
                binding, frame = _admit_override_resume_input(graph, requested.node_id, requested.input)
                actions.append(ResumeInterruptedNode(requested.node_id, requested.interrupt_id, binding))
                replacements[requested.node_id] = PendingGraphNode(binding)
            admitted_inputs.append(
                AdmittedResumeInput(
                    _resume_input_coordinate(activation, plan),
                    frame,
                )
            )
        else:
            if not isinstance(current.settlement, FailedGraphNode):
                raise SnapshotMismatchError("skip requires a failed node")
            routing = (
                ContinueGraphRouting() if requested.route is None else SelectGraphRoute(GraphRouteId(requested.route))
            )
            validate_routing_contribution(graph, requested.node_id, routing)
            reason = GraphSkipReason(requested.reason)
            actions.append(SkipFailedNode(requested.node_id, reason, routing))
            replacements[requested.node_id] = SkippedGraphNode(
                current.settlement.failure,
                reason,
                routing,
            )
            if requested.output is not None:
                publication = graph.transition.publications[requested.node_id]
                frame = _make_node_output_frame(requested.output, publication.declarations)
                substitutions.append(
                    PreparedSubstitution(
                        PublicationAvailabilityCoordinate(activation, publication.identity),
                        frame,
                        SkipSubstitutionProvenance(),
                    )
                )
    simulated = GraphFrontierState(
        tuple(
            GraphFrontierNode(node.node_id, replacements.get(node.node_id, node.settlement))
            for node in state.frontier.nodes
        )
    )
    validate_graph_frontier(state, simulated)
    return PreparedResume(
        ResumeGraphNodes(state.revision, tuple(actions)),
        tuple(admitted_inputs),
        tuple(substitutions),
    )


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
    candidate_skip_actions = tuple(
        tuple(action for action in candidate.command.actions if isinstance(action, SkipFailedNode))
        for candidate in candidates
    )
    for candidate, skip_actions in zip(candidates, candidate_skip_actions, strict=True):
        if candidate.previous.run_id != candidate.scope_run.graph_run_id or candidate.successor.run_id != (
            candidate.scope_run.graph_run_id
        ):
            raise SnapshotMismatchError("resume candidate states do not match their scoped graph run")
        if reduce_graph_run(candidate.previous, candidate.command) != candidate.successor:
            raise SnapshotMismatchError("resume candidate successor is not the exact command reduction")
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
                or node.settlement.routing != action.routing
            ):
                raise SnapshotMismatchError("resume substitution evidence does not match its admitted scoped successor")
    canonical = tuple(sorted(substitutions, key=lambda substitution: substitution.coordinate))
    publication_counts: dict[PublicationAvailabilityCoordinate[GraphValueT], int] = {}
    duplicate_nodes: set[GraphNodeId] = set()
    collision_nodes: list[GraphNodeId] = []
    for substitution in canonical:
        coordinate = substitution.coordinate
        count = publication_counts.get(coordinate, 0) + 1
        publication_counts[coordinate] = count
        if count == 2:
            duplicate_nodes.add(coordinate.activation.node_id)
        if frames.has_publication(coordinate):
            collision_nodes.append(coordinate.activation.node_id)
    if duplicate_nodes:
        duplicates = tuple(sorted(duplicate_nodes))
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
                    for target in (*facts.control_targets, *facts.completed_join_targets)
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
