"""Whole-invocation recovery availability proof over compiled transition plans."""

from dataclasses import dataclass, field
from enum import IntEnum, auto
from heapq import heappop, heappush
from typing import Generic, TypeVar

from mote_kernel.execution.engine.admission import claim_resource_snapshot, select_executable_tasks
from mote_kernel.execution.engine.claim_stage import project_claim_command
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.resume_input import (
    _require_node_materialization,
    _resume_input_coordinate,
    pending_node_input_available,
)
from mote_kernel.execution.engine.routing import (
    _success_routes,
    graph_outputs_available,
    project_routing_facts,
    resolve_routing_facts,
)
from mote_kernel.execution.engine.settlement import (
    project_failure_settlement,
    project_success_settlement,
)
from mote_kernel.execution.errors import (
    ExecutionLimitError,
    GraphValueUnavailableError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.topology import CompiledGraph, _compiled_graph_at_scope
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation, child_scope_run_for_activation
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.run_context import (
    CandidateFrameAvailability,
    ChildBoundaryAvailabilityCoordinate,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    CompleteGraphFrontier,
    FailedGraphNode,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphFrontierNode,
    GraphFrontierStatus,
    GraphInterruptId,
    GraphJoinProgress,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    ParentGraphActivation,
    PendingGraphNode,
    ResourceId,
    ResourceSnapshot,
    SelectGraphRoute,
    SkippedGraphNode,
    SucceededGraphNode,
    frontier_node,
    frontier_status,
    graph_interrupt_id,
    pending_node_ids,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")
_MAX_RECOVERY_TRANSFER_STATES = 4_096


class RecoverySettlementKind(IntEnum):
    PENDING_MATERIALIZED = auto()
    PENDING_OVERRIDE = auto()
    SUCCEEDED_CONTINUE = auto()
    SUCCEEDED_ROUTE = auto()
    FAILED = auto()
    INTERRUPTED = auto()
    SKIPPED_CONTINUE = auto()
    SKIPPED_ROUTE = auto()


class AdmittedActionKind(IntEnum):
    RESUME_FAILED = auto()
    RESUME_FAILED_WITH = auto()
    RESUME_INTERRUPTED = auto()
    SKIP_FAILED = auto()


@dataclass(frozen=True, slots=True)
class RecoveryFrontierNode:
    node_id: GraphNodeId
    settlement: RecoverySettlementKind
    route: GraphRouteId | None = None
    interrupt_id: GraphInterruptId | None = None


@dataclass(frozen=True, slots=True)
class ResourceLockCoordinate:
    resource_id: ResourceId
    owner: GraphNodeId | None
    waiters: tuple[GraphNodeId, ...]


@dataclass(frozen=True, slots=True)
class ResourceAcquisitionCoordinate:
    node_id: GraphNodeId
    required: tuple[ResourceId, ...]
    acquired: tuple[ResourceId, ...]
    waiting_for: ResourceId | None


@dataclass(frozen=True, slots=True)
class ResourceControlCoordinate:
    locks: tuple[ResourceLockCoordinate, ...]
    acquisitions: tuple[ResourceAcquisitionCoordinate, ...]


@dataclass(frozen=True, slots=True)
class ExecutionControlCoordinate:
    generation: int
    attempt_id: GraphExecutionAttemptId


@dataclass(frozen=True, slots=True)
class ScopeControlStateCoordinate:
    scope_run: ScopeRunCoordinate
    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    status: GraphRunStatus
    superstep: int
    execution_sequence: int
    frontier: tuple[RecoveryFrontierNode, ...]
    join_progress: tuple[GraphJoinProgress, ...]
    resources: ResourceControlCoordinate | None
    execution: ExecutionControlCoordinate | None
    resume_codec_id: GraphResumeInputCodecId | None
    resume_codec_version: int | None
    parent: ParentGraphActivation | None
    revision: int


@dataclass(frozen=True, slots=True)
class ChildControlStateCoordinate:
    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    status: GraphRunStatus
    superstep: int
    execution_sequence: int
    frontier: tuple[RecoveryFrontierNode, ...]
    join_progress: tuple[GraphJoinProgress, ...]
    resources: ResourceControlCoordinate | None
    execution: ExecutionControlCoordinate | None
    resume_codec_id: GraphResumeInputCodecId | None
    resume_codec_version: int | None
    parent: ParentGraphActivation
    revision: int


@dataclass(frozen=True, slots=True)
class RecoveryAvailabilityCoordinates(Generic[GraphValueT]):
    graph_inputs: tuple[GraphInputAvailabilityCoordinate[GraphValueT], ...] = ()
    publications: tuple[PublicationAvailabilityCoordinate[GraphValueT], ...] = ()
    resume_inputs: tuple[ResumeInputAvailabilityCoordinate[GraphValueT], ...] = ()
    child_boundaries: tuple[ChildBoundaryAvailabilityCoordinate[GraphValueT], ...] = ()

    @classmethod
    def from_frames(
        cls,
        frames: ScopedFrameIndex[GraphValueT] | CandidateFrameAvailability[GraphValueT],
    ) -> "RecoveryAvailabilityCoordinates[GraphValueT]":
        if isinstance(frames, CandidateFrameAvailability):
            confirmed = frames.confirmed
            candidate_publications = tuple(substitution.coordinate for substitution in frames.substitutions)
        else:
            confirmed = frames
            candidate_publications = ()
        publications = (*tuple(record.coordinate for record in confirmed.publications), *candidate_publications)
        if len(publications) != len(set(publications)):
            raise SnapshotMismatchError("recovery publication availability coordinates must be unique")
        return cls(
            tuple(sorted(record.coordinate for record in confirmed.graph_inputs)),
            tuple(sorted(publications)),
            tuple(sorted(record.coordinate for record in confirmed.resume_inputs)),
            tuple(sorted(record.coordinate for record in confirmed.child_boundaries)),
        )

    def has_graph_input(
        self,
        coordinate: GraphInputAvailabilityCoordinate[GraphValueT],
    ) -> bool:
        return coordinate in self.graph_inputs

    def has_publication(
        self,
        coordinate: PublicationAvailabilityCoordinate[GraphValueT],
    ) -> bool:
        return coordinate in self.publications

    def has_resume_input(
        self,
        coordinate: ResumeInputAvailabilityCoordinate[GraphValueT],
    ) -> bool:
        return coordinate in self.resume_inputs

    def has_child_boundary(
        self,
        coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT],
    ) -> bool:
        return coordinate in self.child_boundaries

    def with_graph_input(
        self,
        coordinate: GraphInputAvailabilityCoordinate[GraphValueT],
    ) -> "RecoveryAvailabilityCoordinates[GraphValueT]":
        if coordinate in self.graph_inputs:
            return self
        return RecoveryAvailabilityCoordinates(
            tuple(sorted((*self.graph_inputs, coordinate))),
            self.publications,
            self.resume_inputs,
            self.child_boundaries,
        )

    def with_publication(
        self,
        coordinate: PublicationAvailabilityCoordinate[GraphValueT],
    ) -> "RecoveryAvailabilityCoordinates[GraphValueT]":
        if coordinate in self.publications:
            return self
        return RecoveryAvailabilityCoordinates(
            self.graph_inputs,
            tuple(sorted((*self.publications, coordinate))),
            self.resume_inputs,
            self.child_boundaries,
        )

    def with_child_boundary(
        self,
        coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT],
    ) -> "RecoveryAvailabilityCoordinates[GraphValueT]":
        if coordinate in self.child_boundaries:
            return self
        return RecoveryAvailabilityCoordinates(
            self.graph_inputs,
            self.publications,
            self.resume_inputs,
            tuple(sorted((*self.child_boundaries, coordinate))),
        )


@dataclass(frozen=True, slots=True)
class ChildRecoveryDisposition:
    child_scope_run: ScopeRunCoordinate
    control: ChildControlStateCoordinate | None


@dataclass(frozen=True, slots=True)
class AdmittedResumeFact:
    target: StableActivation
    action: AdmittedActionKind
    interrupt_id: GraphInterruptId | None
    skip_reason: str | None
    concrete_route: GraphRouteId | None


@dataclass(frozen=True, slots=True)
class RecoveryTransferState(Generic[GraphValueT]):
    control: ScopeControlStateCoordinate
    limits: ExecutionLimits
    live: tuple[GraphNodeId, ...]
    availability: RecoveryAvailabilityCoordinates[GraphValueT]
    children: tuple[ChildRecoveryDisposition, ...]
    admitted_actions: tuple[AdmittedResumeFact, ...]
    invocation_new_children: tuple[GraphNodeId, ...] = ()


@dataclass(frozen=True, slots=True, order=True)
class RecoveryTraversalKey:
    parts: tuple[str, ...]


class _ScopeBoundaryKind(IntEnum):
    COMPLETED = auto()
    ABORTED = auto()
    AWAITING_RESUME = auto()
    EXECUTION_LIMIT = auto()


@dataclass(frozen=True, slots=True)
class _ScopeBoundary(Generic[GraphValueT]):
    kind: _ScopeBoundaryKind
    availability: RecoveryAvailabilityCoordinates[GraphValueT]
    control: ScopeControlStateCoordinate
    state: GraphRunState = field(compare=False, repr=False, hash=False)


@dataclass(frozen=True, slots=True)
class RecoveryStateBinding:
    scope_run: ScopeRunCoordinate
    state: GraphRunState


@dataclass(frozen=True, slots=True)
class RecoveryInvocationSeed(Generic[GraphValueT]):
    root: RecoveryStateBinding
    children: tuple[RecoveryStateBinding, ...]
    frames: ScopedFrameIndex[GraphValueT] | CandidateFrameAvailability[GraphValueT] = field(
        compare=False, repr=False, hash=False
    )
    limits: ExecutionLimits
    admitted_actions: tuple[AdmittedResumeFact, ...] = ()


@dataclass(frozen=True, slots=True)
class _RecoveryWorkItem(Generic[GraphValueT]):
    state: GraphRunState = field(repr=False)
    availability: RecoveryAvailabilityCoordinates[GraphValueT]
    live: tuple[GraphNodeId, ...] = ()
    children: tuple[ChildRecoveryDisposition, ...] = ()
    invocation_new_children: tuple[GraphNodeId, ...] = ()


@dataclass(frozen=True, slots=True)
class _NestedOutcome(Generic[GraphValueT]):
    node_id: GraphNodeId
    boundary: _ScopeBoundary[GraphValueT]


@dataclass(frozen=True, slots=True)
class _NestedCombination(Generic[GraphValueT]):
    outcomes: tuple[_NestedOutcome[GraphValueT], ...]
    availability: RecoveryAvailabilityCoordinates[GraphValueT]


@dataclass(slots=True)
class _RecoveryProofBudget:
    admitted_states: int = 0

    def admit(self, count: int) -> None:
        if self.admitted_states + count > _MAX_RECOVERY_TRANSFER_STATES:
            raise ExecutionLimitError("recovery proof exceeded its bounded transfer-state budget")
        self.admitted_states += count


@dataclass(frozen=True, slots=True)
class _RecoveryFamily:
    bindings: tuple[RecoveryStateBinding, ...]
    limits: ExecutionLimits
    admitted_actions: tuple[AdmittedResumeFact, ...]
    budget: _RecoveryProofBudget

    def binding(self, coordinate: ScopeRunCoordinate) -> RecoveryStateBinding | None:
        return next((binding for binding in self.bindings if binding.scope_run == coordinate), None)

    def action_node_ids(self) -> tuple[GraphNodeId, ...]:
        return tuple(sorted({action.target.node_id for action in self.admitted_actions}))


def _resource_coordinate(snapshot: ResourceSnapshot | None) -> ResourceControlCoordinate | None:
    if snapshot is None:
        return None
    return ResourceControlCoordinate(
        tuple(ResourceLockCoordinate(lock.resource_id, lock.owner, lock.waiters) for lock in snapshot.resources),
        tuple(
            ResourceAcquisitionCoordinate(
                acquisition.node_id,
                acquisition.required,
                acquisition.acquired,
                acquisition.waiting_for,
            )
            for acquisition in snapshot.acquisitions
        ),
    )


def _execution_coordinate(state: GraphRunState) -> ExecutionControlCoordinate | None:
    if state.execution is None:
        return None
    return ExecutionControlCoordinate(
        state.execution.token.generation,
        state.execution.token.attempt_id,
    )


def _settlement_coordinate(node: GraphFrontierNode) -> RecoveryFrontierNode:
    node_id = node.node_id
    settlement = node.settlement
    if isinstance(settlement, PendingGraphNode):
        kind = (
            RecoverySettlementKind.PENDING_OVERRIDE
            if isinstance(settlement.input, OverrideGraphNodeInput)
            else RecoverySettlementKind.PENDING_MATERIALIZED
        )
        return RecoveryFrontierNode(node_id, kind)
    if isinstance(settlement, SucceededGraphNode):
        if isinstance(settlement.routing, SelectGraphRoute):
            return RecoveryFrontierNode(
                node_id,
                RecoverySettlementKind.SUCCEEDED_ROUTE,
                settlement.routing.route,
            )
        return RecoveryFrontierNode(node_id, RecoverySettlementKind.SUCCEEDED_CONTINUE)
    if isinstance(settlement, FailedGraphNode):
        return RecoveryFrontierNode(node_id, RecoverySettlementKind.FAILED)
    if isinstance(settlement, InterruptedGraphNode):
        identity = settlement.interrupt.identity
        return RecoveryFrontierNode(
            node_id,
            RecoverySettlementKind.INTERRUPTED,
            interrupt_id=graph_interrupt_id(
                identity.run_id,
                identity.superstep,
                identity.node_id,
                identity.execution_generation,
            ),
        )
    if isinstance(settlement.routing, SelectGraphRoute):
        return RecoveryFrontierNode(
            node_id,
            RecoverySettlementKind.SKIPPED_ROUTE,
            settlement.routing.route,
        )
    return RecoveryFrontierNode(node_id, RecoverySettlementKind.SKIPPED_CONTINUE)


def _scope_control(state: GraphRunState, scope_run: ScopeRunCoordinate) -> ScopeControlStateCoordinate:
    if state.run_id != scope_run.graph_run_id:
        raise SnapshotMismatchError("recovery scope-run identity does not match authoritative state")
    codec = state.resume_input_codec
    return ScopeControlStateCoordinate(
        scope_run,
        state.definition_id,
        state.definition_version,
        state.status,
        state.superstep,
        state.execution_sequence,
        tuple(_settlement_coordinate(node) for node in state.frontier.nodes),
        state.join_progress,
        _resource_coordinate(state.resources),
        _execution_coordinate(state),
        codec.codec_id if codec is not None else None,
        codec.version if codec is not None else None,
        state.parent,
        state.revision,
    )


def _child_disposition_from_control(control: ScopeControlStateCoordinate) -> ChildRecoveryDisposition:
    parent = control.parent
    if parent is None:
        raise SnapshotMismatchError("nested recovery state is missing its parent activation")
    return ChildRecoveryDisposition(
        control.scope_run,
        ChildControlStateCoordinate(
            control.definition_id,
            control.definition_version,
            control.status,
            control.superstep,
            control.execution_sequence,
            control.frontier,
            control.join_progress,
            control.resources,
            control.execution,
            control.resume_codec_id,
            control.resume_codec_version,
            parent,
            control.revision,
        ),
    )


def _atom(value: str) -> str:
    return f"{len(value)}:{value}"


def _coordinate_parts(scope_run: ScopeRunCoordinate) -> tuple[str, ...]:
    return (
        str(len(scope_run.scope)),
        *(_atom(segment) for segment in scope_run.scope),
        _atom(scope_run.graph_run_id),
    )


def recovery_traversal_key(state: RecoveryTransferState[GraphValueT]) -> RecoveryTraversalKey:
    parts: list[str] = [
        str(state.limits.max_supersteps),
        str(state.limits.max_parallel_tasks),
        *_coordinate_parts(state.control.scope_run),
        str(state.control.status.value),
        str(state.control.superstep),
        str(state.control.execution_sequence),
        str(state.control.revision),
    ]
    for node in state.control.frontier:
        parts.extend(
            (
                _atom(node.node_id),
                str(node.settlement.value),
                _atom(node.route or ""),
                _atom(node.interrupt_id or ""),
            )
        )
    pending = tuple(
        node.node_id
        for node in state.control.frontier
        if node.settlement in (RecoverySettlementKind.PENDING_MATERIALIZED, RecoverySettlementKind.PENDING_OVERRIDE)
    )
    resource_waiting = tuple(node_id for node_id in pending if node_id not in state.live)
    for positions in (state.live, resource_waiting):
        parts.append(str(len(positions)))
        parts.extend(_atom(node_id) for node_id in positions)
    parts.append(str(max(0, state.limits.max_parallel_tasks - len(state.live))))
    for coordinate in state.availability.graph_inputs:
        parts.extend((*_coordinate_parts(coordinate.scope_run), str(coordinate.descriptor)))
    for coordinate in state.availability.publications:
        parts.extend(
            (
                *_coordinate_parts(coordinate.activation.scope_run),
                str(coordinate.activation.superstep),
                _atom(coordinate.activation.node_id),
                str(coordinate.descriptor),
            )
        )
    for coordinate in state.availability.resume_inputs:
        parts.extend(
            (
                *_coordinate_parts(coordinate.activation.scope_run),
                str(coordinate.activation.superstep),
                _atom(coordinate.activation.node_id),
                str(coordinate.descriptor),
            )
        )
    for coordinate in state.availability.child_boundaries:
        parts.extend((*_coordinate_parts(coordinate.child_scope_run), str(coordinate.descriptor)))
    for child in state.children:
        parts.extend((*_coordinate_parts(child.child_scope_run), str(child.control is not None)))
        if child.control is not None:
            parts.extend(
                (
                    str(child.control.status.value),
                    str(child.control.superstep),
                    str(child.control.execution_sequence),
                    str(child.control.revision),
                )
            )
    for action in state.admitted_actions:
        parts.extend(
            (
                *_coordinate_parts(action.target.scope_run),
                str(action.target.superstep),
                _atom(action.target.node_id),
                str(action.action.value),
                _atom(action.interrupt_id or ""),
                _atom(action.skip_reason or ""),
                _atom(action.concrete_route or ""),
            )
        )
    parts.append(str(len(state.invocation_new_children)))
    parts.extend(_atom(node_id) for node_id in state.invocation_new_children)
    return RecoveryTraversalKey(tuple(parts))


def _transfer_state(
    scope_run: ScopeRunCoordinate,
    item: _RecoveryWorkItem[GraphValueT],
    family: _RecoveryFamily,
) -> RecoveryTransferState[GraphValueT]:
    return RecoveryTransferState(
        _scope_control(item.state, scope_run),
        family.limits,
        tuple(sorted(item.live)),
        item.availability,
        tuple(sorted(item.children, key=lambda child: child.child_scope_run)),
        family.admitted_actions,
        tuple(sorted(item.invocation_new_children)),
    )


def _publication_coordinate(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    state: GraphRunState,
    node_id: GraphNodeId,
) -> PublicationAvailabilityCoordinate[GraphValueT]:
    return PublicationAvailabilityCoordinate(
        StableActivation(scope_run, state.superstep, node_id),
        graph.transition.publications[node_id].identity,
    )


def _select_live(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    limits: ExecutionLimits,
    live: tuple[GraphNodeId, ...],
) -> tuple[GraphNodeId, ...]:
    selected = select_executable_tasks(
        graph,
        plan_tasks(graph, state, limits),
        state.resources,
        limits,
        active_count=len(live),
        started_node_ids=frozenset(live),
    )
    return tuple(sorted((*live, *(task.node_id for task in selected))))


def _initial_children(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    family: _RecoveryFamily,
    invocation_new: tuple[GraphNodeId, ...],
) -> tuple[ChildRecoveryDisposition, ...]:
    dispositions: list[ChildRecoveryDisposition] = []
    for node_id in pending_node_ids(state.frontier):
        child_graph = graph.nested_graphs.get(node_id)
        if child_graph is None:
            continue
        parent = ParentGraphActivation(state.run_id, state.superstep, node_id)
        coordinate = child_scope_run_for_activation(scope_run, parent)
        binding = family.binding(coordinate)
        if binding is None and node_id not in invocation_new:
            raise GraphValueUnavailableError(
                f"resume actions {family.action_node_ids()!r} lack child snapshot/nested boundary at {coordinate!r}"
            )
        if binding is None:
            disposition = ChildRecoveryDisposition(coordinate, None)
        else:
            disposition = _child_disposition_from_control(_scope_control(binding.state, coordinate))
        dispositions.append(disposition)
    return tuple(sorted(dispositions, key=lambda disposition: disposition.child_scope_run))


def _boundary(
    kind: _ScopeBoundaryKind,
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    availability: RecoveryAvailabilityCoordinates[GraphValueT],
) -> _ScopeBoundary[GraphValueT]:
    return _ScopeBoundary(kind, availability, _scope_control(state, scope_run), state)


def _completed_child_outcome(
    node_id: GraphNodeId,
    graph: CompiledGraph[GraphValueT],
    boundary: _ScopeBoundary[GraphValueT],
) -> _NestedOutcome[GraphValueT]:
    availability = boundary.availability
    boundary_coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
        boundary.control.scope_run, graph.graph_output_descriptor.identity
    )
    availability = availability.with_child_boundary(boundary_coordinate)
    return _NestedOutcome(
        node_id,
        _ScopeBoundary(boundary.kind, availability, boundary.control, boundary.state),
    )


def _child_outcomes(
    parent_graph: CompiledGraph[GraphValueT],
    parent_state: GraphRunState,
    parent_scope_run: ScopeRunCoordinate,
    node_id: GraphNodeId,
    availability: RecoveryAvailabilityCoordinates[GraphValueT],
    family: _RecoveryFamily,
) -> tuple[_NestedOutcome[GraphValueT], ...]:
    child_graph = parent_graph.nested_graphs[node_id]
    parent = ParentGraphActivation(parent_state.run_id, parent_state.superstep, node_id)
    coordinate = child_scope_run_for_activation(parent_scope_run, parent)
    binding = family.binding(coordinate)
    if binding is None:
        child_state = reduce_graph_run(
            None,
            project_start_graph_command(child_graph, coordinate.graph_run_id, parent),
        )
        child_input_coordinate: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
            coordinate, child_graph.graph_input_descriptor.identity
        )
        child_availability = availability.with_graph_input(child_input_coordinate)
    else:
        child_state = binding.state
        if child_state.parent != parent:
            raise SnapshotMismatchError("child recovery snapshot does not match its parent activation")
        child_availability = availability
    if child_state.status is GraphRunStatus.COMPLETED:
        if not graph_outputs_available(
            child_graph,
            coordinate,
            child_state.superstep,
            child_availability,
        ):
            raise GraphValueUnavailableError(
                f"resume actions {family.action_node_ids()!r} require completed child output history/nested boundary "
                f"at {coordinate!r}"
            )
        boundary = _boundary(
            _ScopeBoundaryKind.COMPLETED,
            child_state,
            coordinate,
            child_availability,
        )
        return (_completed_child_outcome(node_id, child_graph, boundary),)
    if child_state.status is GraphRunStatus.ABORTED:
        boundary = _boundary(
            _ScopeBoundaryKind.ABORTED,
            child_state,
            coordinate,
            child_availability,
        )
        return (_NestedOutcome(node_id, boundary),)
    boundaries = _prove_scope(
        child_graph,
        child_state,
        coordinate,
        child_availability,
        family,
    )
    outcomes: list[_NestedOutcome[GraphValueT]] = []
    for boundary in boundaries:
        if boundary.kind is _ScopeBoundaryKind.COMPLETED:
            outcomes.append(_completed_child_outcome(node_id, child_graph, boundary))
        else:
            outcomes.append(_NestedOutcome(node_id, boundary))
    return tuple(outcomes)


def _nested_outcome_plans(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    node_ids: tuple[GraphNodeId, ...],
    availability: RecoveryAvailabilityCoordinates[GraphValueT],
    family: _RecoveryFamily,
) -> tuple[_NestedCombination[GraphValueT], ...]:
    selected: list[_NestedOutcome[GraphValueT]] = []
    alternatives: list[tuple[int, _NestedOutcome[GraphValueT]]] = []
    boundaries: list[_NestedCombination[GraphValueT]] = []
    current_availability = availability
    for node_id in node_ids:
        outcomes = _child_outcomes(
            graph,
            state,
            scope_run,
            node_id,
            current_availability,
            family,
        )
        boundaries.extend(
            _NestedCombination((*selected, outcome), outcome.boundary.availability)
            for outcome in outcomes
            if outcome.boundary.kind in (_ScopeBoundaryKind.AWAITING_RESUME, _ScopeBoundaryKind.EXECUTION_LIMIT)
        )
        completed = next(
            (outcome for outcome in outcomes if outcome.boundary.kind is _ScopeBoundaryKind.COMPLETED),
            None,
        )
        aborted = next(
            (outcome for outcome in outcomes if outcome.boundary.kind is _ScopeBoundaryKind.ABORTED),
            None,
        )
        primary = completed if completed is not None else aborted
        if primary is None:
            return tuple(boundaries)
        if completed is not None and aborted is not None:
            alternatives.append((len(selected), aborted))
        selected.append(primary)
        current_availability = primary.boundary.availability
    primary_plan = _NestedCombination(tuple(selected), current_availability)
    variations = tuple(
        _NestedCombination(
            tuple(alternative if position == index else outcome for position, outcome in enumerate(selected)),
            current_availability,
        )
        for index, alternative in alternatives
    )
    return (primary_plan, *variations, *boundaries)


def _settle_nested_outcomes(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    combination: _NestedCombination[GraphValueT],
) -> tuple[GraphRunState, RecoveryAvailabilityCoordinates[GraphValueT]]:
    current = state
    availability = combination.availability
    for outcome in combination.outcomes:
        if outcome.boundary.kind is _ScopeBoundaryKind.COMPLETED:
            previous = current
            current = reduce_graph_run(
                current,
                project_success_settlement(graph, current, outcome.node_id, None),
            )
            availability = availability.with_publication(
                _publication_coordinate(graph, scope_run, previous, outcome.node_id)
            )
        else:
            current = reduce_graph_run(
                current,
                project_failure_settlement(current, outcome.node_id, "recovery-preflight-failure"),
            )
    return current, availability


def _expand_quiescent_executable(
    graph: CompiledGraph[GraphValueT],
    item: _RecoveryWorkItem[GraphValueT],
    scope_run: ScopeRunCoordinate,
    family: _RecoveryFamily,
) -> tuple[_RecoveryWorkItem[GraphValueT] | _ScopeBoundary[GraphValueT], ...]:
    state = item.state
    try:
        tasks = plan_tasks(graph, state, family.limits)
    except ExecutionLimitError:
        return (_boundary(_ScopeBoundaryKind.EXECUTION_LIMIT, state, scope_run, item.availability),)
    nested_ids = tuple(task.node_id for task in tasks if task.node_id in graph.nested_graphs)
    children = _initial_children(
        graph,
        state,
        scope_run,
        family,
        item.invocation_new_children,
    )
    combinations = _nested_outcome_plans(
        graph,
        state,
        scope_run,
        nested_ids,
        item.availability,
        family,
    )
    successors: list[_RecoveryWorkItem[GraphValueT] | _ScopeBoundary[GraphValueT]] = []
    for combination in combinations:
        limited = next(
            (
                outcome
                for outcome in combination.outcomes
                if outcome.boundary.kind is _ScopeBoundaryKind.EXECUTION_LIMIT
            ),
            None,
        )
        if limited is not None:
            successors.append(limited.boundary)
            continue
        if any(outcome.boundary.kind is _ScopeBoundaryKind.AWAITING_RESUME for outcome in combination.outcomes):
            successors.append(
                _boundary(
                    _ScopeBoundaryKind.AWAITING_RESUME,
                    state,
                    scope_run,
                    combination.availability,
                )
            )
            continue
        unavailable_inputs = tuple(
            task.node_id
            for task in tasks
            if isinstance(graph.nodes[task.node_id], CallableNodeDefinition)
            and not pending_node_input_available(
                graph,
                state,
                scope_run,
                combination.availability,
                task.node_id,
            )
        )
        if unavailable_inputs:
            raise GraphValueUnavailableError(
                f"resume actions {family.action_node_ids()!r} require unavailable historical values "
                f"for pending nodes {unavailable_inputs!r} at {scope_run!r}; "
                "node input or nested boundary materialization is unavailable"
            )
        claimed = reduce_graph_run(
            state,
            project_claim_command(
                state,
                GraphExecutionAttemptId("recovery-preflight"),
                claim_resource_snapshot(graph, tasks),
            ),
        )
        settled, availability = _settle_nested_outcomes(
            graph,
            claimed,
            scope_run,
            combination,
        )
        live = _select_live(graph, settled, family.limits, ())
        successors.append(
            _RecoveryWorkItem(
                settled,
                availability,
                live,
                tuple(_child_disposition_from_control(outcome.boundary.control) for outcome in combination.outcomes)
                or children,
                (),
            )
        )
    return tuple(successors)


def _expand_live(
    graph: CompiledGraph[GraphValueT],
    item: _RecoveryWorkItem[GraphValueT],
    scope_run: ScopeRunCoordinate,
    family: _RecoveryFamily,
) -> tuple[_RecoveryWorkItem[GraphValueT], ...]:
    successors: list[_RecoveryWorkItem[GraphValueT]] = []
    node_id = item.live[0]
    remaining_live = item.live[1:]
    for route in _success_routes(graph, node_id):
        settled = reduce_graph_run(
            item.state,
            project_success_settlement(graph, item.state, node_id, route),
        )
        availability = item.availability.with_publication(
            _publication_coordinate(graph, scope_run, item.state, node_id)
        )
        live = _select_live(graph, settled, family.limits, remaining_live)
        successors.append(
            _RecoveryWorkItem(
                settled,
                availability,
                live,
                item.children,
                item.invocation_new_children,
            )
        )
    return tuple(successors)


def _resolve_quiescent(
    graph: CompiledGraph[GraphValueT],
    item: _RecoveryWorkItem[GraphValueT],
    scope_run: ScopeRunCoordinate,
    family: _RecoveryFamily,
) -> _RecoveryWorkItem[GraphValueT] | _ScopeBoundary[GraphValueT]:
    state = item.state
    facts = resolve_routing_facts(graph, state, scope_run, item.availability)
    command = project_routing_facts(state, facts)
    if isinstance(command, AbortGraphRun):
        required = (*facts.control_targets, *facts.completed_join_targets)
        if any(target.historical_inputs_missing for target in required if target.unavailable_inputs) or (
            not any(
                (
                    facts.control_targets,
                    facts.completed_join_targets,
                    facts.remaining_join_progress,
                )
            )
            and facts.unavailable_graph_outputs
        ):
            missing_inputs = tuple(
                (target.node_id, target.unavailable_inputs) for target in required if target.unavailable_inputs
            )
            raise GraphValueUnavailableError(
                f"resume actions {family.action_node_ids()!r} require unavailable historical values "
                f"at {scope_run!r}; "
                f"consumer inputs={missing_inputs!r}; "
                f"graph outputs={facts.unavailable_graph_outputs!r}"
            )
        aborted = reduce_graph_run(state, command)
        return _boundary(_ScopeBoundaryKind.ABORTED, aborted, scope_run, item.availability)
    resolved = reduce_graph_run(state, command)
    if isinstance(command, CompleteGraphFrontier):
        return _boundary(_ScopeBoundaryKind.COMPLETED, resolved, scope_run, item.availability)
    invocation_new = tuple(node_id for node_id in pending_node_ids(resolved.frontier) if node_id in graph.nested_graphs)
    return _RecoveryWorkItem(
        resolved,
        item.availability,
        (),
        (),
        invocation_new,
    )


def _prove_scope(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    availability: RecoveryAvailabilityCoordinates[GraphValueT],
    family: _RecoveryFamily,
) -> tuple[_ScopeBoundary[GraphValueT], ...]:
    initial = _RecoveryWorkItem(
        state,
        availability,
        children=_initial_children(graph, state, scope_run, family, ()),
    )
    family.budget.admit(1)
    sequence = 0
    pending: list[tuple[RecoveryTraversalKey, int, _RecoveryWorkItem[GraphValueT]]] = [
        (recovery_traversal_key(_transfer_state(scope_run, initial, family)), sequence, initial)
    ]

    def enqueue(candidate: _RecoveryWorkItem[GraphValueT]) -> None:
        nonlocal sequence
        sequence += 1
        heappush(
            pending,
            (
                recovery_traversal_key(_transfer_state(scope_run, candidate, family)),
                sequence,
                candidate,
            ),
        )

    seen: set[RecoveryTransferState[GraphValueT]] = set()
    boundaries: set[_ScopeBoundary[GraphValueT]] = set()
    while pending:
        _key, _sequence, item = heappop(pending)
        transfer = _transfer_state(scope_run, item, family)
        if transfer in seen:
            continue
        seen.add(transfer)
        current = item.state
        if current.status is GraphRunStatus.COMPLETED:
            if not graph_outputs_available(
                graph,
                scope_run,
                current.superstep,
                item.availability,
            ):
                raise GraphValueUnavailableError(
                    f"resume actions {family.action_node_ids()!r} require completed graph output history "
                    f"at {scope_run!r}"
                )
            boundaries.add(_boundary(_ScopeBoundaryKind.COMPLETED, current, scope_run, item.availability))
            continue
        if current.status is GraphRunStatus.ABORTED:
            boundaries.add(_boundary(_ScopeBoundaryKind.ABORTED, current, scope_run, item.availability))
            continue
        status = frontier_status(current.frontier)
        if status is GraphFrontierStatus.AWAITING_RESUME:
            boundaries.add(
                _boundary(
                    _ScopeBoundaryKind.AWAITING_RESUME,
                    current,
                    scope_run,
                    item.availability,
                )
            )
            continue
        successors: tuple[
            _RecoveryWorkItem[GraphValueT] | _ScopeBoundary[GraphValueT],
            ...,
        ]
        if current.execution is not None:
            if not item.live:
                raise SnapshotMismatchError("recovery simulated execution has no legal live task")
            successors = _expand_live(graph, item, scope_run, family)
        elif status is GraphFrontierStatus.SETTLED:
            successors = (_resolve_quiescent(graph, item, scope_run, family),)
        else:
            successors = _expand_quiescent_executable(graph, item, scope_run, family)
        family.budget.admit(len(successors))
        for successor in successors:
            if isinstance(successor, _ScopeBoundary):
                boundaries.add(successor)
            else:
                enqueue(successor)
    return tuple(
        sorted(
            boundaries,
            key=lambda boundary: (
                boundary.kind,
                recovery_traversal_key(
                    RecoveryTransferState(
                        boundary.control,
                        family.limits,
                        (),
                        boundary.availability,
                        (),
                        family.admitted_actions,
                    )
                ),
            ),
        )
    )


def preflight_recovery(
    graph: CompiledGraph[GraphValueT],
    seed: RecoveryInvocationSeed[GraphValueT],
) -> tuple[RecoveryTransferState[GraphValueT], ...]:
    """Prove every reachable branch reaches a result or exact planner limit first."""

    if seed.root.scope_run.scope or seed.root.scope_run.graph_run_id != seed.root.state.run_id:
        raise SnapshotMismatchError("recovery root binding has an invalid scope-run coordinate")
    bindings = (seed.root, *seed.children)
    coordinates = tuple(binding.scope_run for binding in bindings)
    if coordinates != tuple(sorted(set(coordinates))):
        raise SnapshotMismatchError("recovery state bindings must be unique and canonical")
    action_targets = tuple(action.target for action in seed.admitted_actions)
    if action_targets != tuple(sorted(set(action_targets))):
        raise SnapshotMismatchError("recovery admitted resume actions must be unique and canonical")
    for action in seed.admitted_actions:
        binding = next(
            (candidate for candidate in bindings if candidate.scope_run == action.target.scope_run),
            None,
        )
        if binding is None or binding.state.superstep != action.target.superstep:
            raise SnapshotMismatchError("recovery admitted resume action does not match a simulated scoped successor")
        node = frontier_node(binding.state.frontier, action.target.node_id)
        if node is None:
            raise SnapshotMismatchError("recovery admitted resume action target is absent from its simulated successor")
        if action.action is AdmittedActionKind.SKIP_FAILED:
            if not isinstance(node.settlement, SkippedGraphNode):
                raise SnapshotMismatchError("recovery skip action does not match its simulated successor settlement")
            route = node.settlement.routing.route if isinstance(node.settlement.routing, SelectGraphRoute) else None
            if node.settlement.reason != action.skip_reason or route != action.concrete_route:
                raise SnapshotMismatchError("recovery skip action facts do not match its simulated successor")
        elif not isinstance(node.settlement, PendingGraphNode):
            raise SnapshotMismatchError("recovery resume action does not match its simulated successor settlement")
    availability: RecoveryAvailabilityCoordinates[GraphValueT] = RecoveryAvailabilityCoordinates[
        GraphValueT
    ].from_frames(seed.frames)
    for action in seed.admitted_actions:
        if action.action is AdmittedActionKind.SKIP_FAILED:
            continue
        scoped_graph = _compiled_graph_at_scope(graph, action.target.scope_run.scope)
        plan = _require_node_materialization(scoped_graph, action.target.node_id)
        expected = _resume_input_coordinate(action.target, plan)
        if not availability.has_resume_input(expected):
            raise SnapshotMismatchError("recovery admitted resume action lacks its exact resume-input availability")
    family = _RecoveryFamily(bindings, seed.limits, seed.admitted_actions, _RecoveryProofBudget())
    boundaries = _prove_scope(
        graph,
        seed.root.state,
        seed.root.scope_run,
        availability,
        family,
    )
    return tuple(
        RecoveryTransferState(
            boundary.control,
            seed.limits,
            (),
            boundary.availability,
            (),
            seed.admitted_actions,
        )
        for boundary in boundaries
    )


__all__: list[str] = []
