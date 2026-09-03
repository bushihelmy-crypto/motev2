"""Whole-invocation recovery availability proof over compiled transition plans."""

from dataclasses import dataclass, field
from enum import IntEnum, auto
from heapq import heappop, heappush
from itertools import chain
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.engine.admission import claim_resource_snapshot, select_executable_tasks
from mote_kernel.execution.engine.claim_stage import project_claim_command
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.resume_input import (
    _require_node_materialization,
    _resume_input_coordinate,
    pending_node_input_available,
)
from mote_kernel.execution.engine.routing import (
    PublicationHistoryWindow,
    _success_routes,
    graph_outputs_available,
    project_routing_facts,
    publication_history_window,
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
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity
from mote_kernel.execution.graph.topology import CompiledGraph, _compiled_graph_at_scope
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import (
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    stable_activation,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import SUPERSEDED_CHILD_ABORT_REASON
from mote_kernel.execution.run_context import (
    ChildBoundaryAvailabilityCoordinate,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ActivationReference,
    CompleteGraphFrontier,
    FailedGraphNode,
    FenceGraphExecution,
    GraphActivationIdentity,
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
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResourceId,
    ResourceSnapshot,
    RoutedActivationCause,
    SelectGraphRoute,
    StartActivationCause,
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


class _RecoveryChildFailureKind(IntEnum):
    FAILED = auto()
    ABORTED = auto()
    SUPERSEDED_AWAITING_RESUME = auto()


class RecoveryActivationCauseKind(IntEnum):
    START = auto()
    ROUTED = auto()


@dataclass(frozen=True, slots=True)
class RecoveryActivationReference:
    predecessor_distance: int
    node_id: GraphNodeId
    route: GraphRouteId | None


@dataclass(frozen=True, slots=True)
class RecoveryActivationCause:
    kind: RecoveryActivationCauseKind
    references: tuple[RecoveryActivationReference, ...] = ()


@dataclass(frozen=True, slots=True)
class RecoveryFrontierNode:
    node_id: GraphNodeId
    cause: RecoveryActivationCause
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
    parent: GraphActivationIdentity | None
    revision: int
    settled_activations: tuple[ActivationReference, ...] = ()


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
    parent: GraphActivationIdentity
    revision: int
    settled_activations: tuple[ActivationReference, ...] = ()


@dataclass(frozen=True, slots=True)
class RecoveryAvailabilityCoordinates(Generic[GraphValueT]):
    graph_inputs: tuple[GraphInputAvailabilityCoordinate[GraphValueT], ...] = ()
    publications: tuple[PublicationAvailabilityCoordinate[GraphValueT], ...] = ()
    resume_inputs: tuple[ResumeInputAvailabilityCoordinate[GraphValueT], ...] = ()
    child_boundaries: tuple[ChildBoundaryAvailabilityCoordinate[GraphValueT], ...] = ()

    @classmethod
    def from_frames(
        cls,
        frames: ScopedFrameIndex[GraphValueT],
    ) -> "RecoveryAvailabilityCoordinates[GraphValueT]":
        publications = tuple(record.coordinate for record in frames.publications)
        if len(publications) != len(set(publications)):
            raise SnapshotMismatchError("recovery publication availability coordinates must be unique")
        return cls(
            tuple(sorted(record.coordinate for record in frames.graph_inputs)),
            tuple(sorted(publications)),
            tuple(sorted(record.coordinate for record in frames.resume_inputs)),
            tuple(sorted(record.coordinate for record in frames.child_boundaries)),
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
    interrupt_id: GraphInterruptId


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
    FAILED = auto()
    ABORTED = auto()
    AWAITING_RESUME = auto()
    EXECUTION_LIMIT = auto()
    BOUNDED_RECURRENCE = auto()


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
    frames: ScopedFrameIndex[GraphValueT] = field(compare=False, repr=False, hash=False)
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


_CyclePublicationKey: TypeAlias = tuple[int, GraphNodeId, FrameDescriptorIdentity]
_CycleFrameKey: TypeAlias = tuple[GraphNodeId, FrameDescriptorIdentity]
_CycleSettlementKey: TypeAlias = tuple[bool, int, GraphNodeId, GraphRouteId | None]


@dataclass(frozen=True, slots=True)
class _RecoveryCycleSignature:
    """Facts that can change a quiescent loop position's next transfer."""

    frontier: tuple[RecoveryFrontierNode, ...]
    join_progress: tuple[GraphJoinProgress, ...]
    settled_activations: tuple[_CycleSettlementKey, ...]
    absolute_publications: tuple[_CyclePublicationKey, ...]
    relative_publications: tuple[_CyclePublicationKey, ...]
    current_resume_inputs: tuple[_CycleFrameKey, ...]
    current_child_boundaries: tuple[_CycleFrameKey, ...]
    invocation_new_children: tuple[GraphNodeId, ...]


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


def _activation_cause_coordinate(state: GraphRunState, node: GraphFrontierNode) -> RecoveryActivationCause:
    cause = node.cause
    if isinstance(cause, StartActivationCause):
        return RecoveryActivationCause(RecoveryActivationCauseKind.START)
    return RecoveryActivationCause(
        RecoveryActivationCauseKind.ROUTED,
        tuple(
            RecoveryActivationReference(
                state.superstep - reference.activation.superstep,
                reference.activation.node_id,
                reference.route,
            )
            for reference in cause.references
        ),
    )


def _settlement_coordinate(state: GraphRunState, node: GraphFrontierNode) -> RecoveryFrontierNode:
    node_id = node.node_id
    cause = _activation_cause_coordinate(state, node)
    settlement = node.settlement
    if isinstance(settlement, PendingGraphNode):
        kind = (
            RecoverySettlementKind.PENDING_OVERRIDE
            if isinstance(settlement.input, OverrideGraphNodeInput)
            else RecoverySettlementKind.PENDING_MATERIALIZED
        )
        return RecoveryFrontierNode(node_id, cause, kind)
    if isinstance(settlement, SucceededGraphNode):
        if isinstance(settlement.routing, SelectGraphRoute):
            return RecoveryFrontierNode(
                node_id,
                cause,
                RecoverySettlementKind.SUCCEEDED_ROUTE,
                settlement.routing.route,
            )
        return RecoveryFrontierNode(node_id, cause, RecoverySettlementKind.SUCCEEDED_CONTINUE)
    if isinstance(settlement, FailedGraphNode):
        return RecoveryFrontierNode(node_id, cause, RecoverySettlementKind.FAILED)
    identity = settlement.interrupt.identity
    return RecoveryFrontierNode(
        node_id,
        cause,
        RecoverySettlementKind.INTERRUPTED,
        interrupt_id=graph_interrupt_id(
            identity.run_id,
            identity.superstep,
            identity.node_id,
            identity.execution_generation,
        ),
    )


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
        tuple(_settlement_coordinate(state, node) for node in state.frontier.nodes),
        state.join_progress,
        _resource_coordinate(state.resources),
        _execution_coordinate(state),
        codec.codec_id if codec is not None else None,
        codec.version if codec is not None else None,
        state.parent,
        state.revision,
        state.settled_activations,
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
            control.settled_activations,
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
                str(node.cause.kind.value),
                str(node.settlement.value),
                _atom(node.route or ""),
                _atom(node.interrupt_id or ""),
            )
        )
        for reference in node.cause.references:
            parts.extend(
                (
                    str(reference.predecessor_distance),
                    _atom(reference.node_id),
                    _atom(reference.route or ""),
                )
            )
    for reference in state.control.settled_activations:
        parts.extend(
            (
                _atom(reference.activation.run_id),
                str(reference.activation.superstep),
                _atom(reference.activation.node_id),
                _atom(reference.route or ""),
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
    for coordinate in chain(state.availability.publications, state.availability.resume_inputs):
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
                _atom(action.interrupt_id),
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


def _recovery_cycle_signature(
    graph: CompiledGraph[GraphValueT],
    item: _RecoveryWorkItem[GraphValueT],
    scope_run: ScopeRunCoordinate,
    window: PublicationHistoryWindow,
) -> _RecoveryCycleSignature | None:
    state = item.state
    if state.execution is not None or item.live or item.children:
        return None

    absolute_steps = frozenset(window.absolute_supersteps)
    absolute_publications: tuple[_CyclePublicationKey, ...] = tuple(
        sorted(
            (
                coordinate.activation.superstep,
                coordinate.activation.node_id,
                coordinate.descriptor,
            )
            for coordinate in item.availability.publications
            if coordinate.activation.scope_run == scope_run and coordinate.activation.superstep in absolute_steps
        )
    )
    relative_publications: tuple[_CyclePublicationKey, ...] = tuple(
        sorted(
            (
                state.superstep - coordinate.activation.superstep,
                coordinate.activation.node_id,
                coordinate.descriptor,
            )
            for coordinate in item.availability.publications
            if coordinate.activation.scope_run == scope_run
            and 0 <= state.superstep - coordinate.activation.superstep <= window.relative_horizon
        )
    )
    current_resume_inputs: tuple[_CycleFrameKey, ...] = tuple(
        sorted(
            (coordinate.activation.node_id, coordinate.descriptor)
            for coordinate in item.availability.resume_inputs
            if coordinate.activation.scope_run == scope_run and coordinate.activation.superstep == state.superstep
        )
    )
    current_child_boundaries: list[_CycleFrameKey] = []
    for node_id, _child_graph in graph.nested_graphs.entries:
        parent = GraphActivationIdentity(state.run_id, state.superstep, node_id)
        child_scope_run = child_scope_run_for_activation(scope_run, parent)
        current_child_boundaries.extend(
            (node_id, coordinate.descriptor)
            for coordinate in item.availability.child_boundaries
            if coordinate.child_scope_run == child_scope_run
        )
    referenced_settlements = {
        reference
        for node in state.frontier.nodes
        if isinstance(node.cause, RoutedActivationCause)
        for reference in node.cause.references
    }
    referenced_settlements.update(reference for progress in state.join_progress for reference in progress.arrived)
    settled_activations: tuple[_CycleSettlementKey, ...] = tuple(
        sorted(
            (
                (
                    True,
                    reference.activation.superstep,
                    reference.activation.node_id,
                    reference.route,
                )
                if reference.activation.superstep in absolute_steps
                else (
                    False,
                    state.superstep - reference.activation.superstep,
                    reference.activation.node_id,
                    reference.route,
                )
            )
            for reference in state.settled_activations
            if reference in referenced_settlements
            or reference.activation.superstep in absolute_steps
            or 0 <= state.superstep - reference.activation.superstep <= window.relative_horizon
        )
    )
    return _RecoveryCycleSignature(
        frontier=tuple(_settlement_coordinate(state, node) for node in state.frontier.nodes),
        join_progress=state.join_progress,
        settled_activations=settled_activations,
        absolute_publications=absolute_publications,
        relative_publications=relative_publications,
        current_resume_inputs=current_resume_inputs,
        current_child_boundaries=tuple(sorted(current_child_boundaries)),
        invocation_new_children=tuple(sorted(item.invocation_new_children)),
    )


def _publication_coordinate(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    state: GraphRunState,
    node_id: GraphNodeId,
) -> PublicationAvailabilityCoordinate[GraphValueT]:
    return PublicationAvailabilityCoordinate(
        stable_activation(
            scope_run,
            GraphActivationIdentity(state.run_id, state.superstep, node_id),
        ),
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
        parent = GraphActivationIdentity(state.run_id, state.superstep, node_id)
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
    parent = GraphActivationIdentity(parent_state.run_id, parent_state.superstep, node_id)
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
    if child_state.status is GraphRunStatus.FAILED:
        boundary = _boundary(
            _ScopeBoundaryKind.FAILED,
            child_state,
            coordinate,
            child_availability,
        )
        return (_NestedOutcome(node_id, boundary),)
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
    plans = (_NestedCombination((), availability),)
    for node_id in node_ids:
        next_plans: list[_NestedCombination[GraphValueT]] = []
        for plan in plans:
            outcomes = _child_outcomes(
                graph,
                state,
                scope_run,
                node_id,
                plan.availability,
                family,
            )
            family.budget.admit(len(outcomes))
            next_plans.extend(
                _NestedCombination(
                    (*plan.outcomes, outcome),
                    outcome.boundary.availability,
                )
                for outcome in outcomes
            )
        plans = tuple(next_plans)
    return plans


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
        elif outcome.boundary.kind is _ScopeBoundaryKind.FAILED:
            current = _settle_recovery_child_failure(
                current,
                outcome.node_id,
                _RecoveryChildFailureKind.FAILED,
            )
        elif outcome.boundary.kind is _ScopeBoundaryKind.ABORTED:
            current = _settle_recovery_child_failure(
                current,
                outcome.node_id,
                _RecoveryChildFailureKind.ABORTED,
            )
        elif outcome.boundary.kind is _ScopeBoundaryKind.AWAITING_RESUME:
            continue
        else:
            raise SnapshotMismatchError("non-terminal child outcome reached nested settlement")
    return current, availability


def _settle_recovery_child_failure(
    state: GraphRunState,
    node_id: GraphNodeId,
    kind: _RecoveryChildFailureKind,
) -> GraphRunState:
    """Project proof-only child disposition through the real State reducer."""

    if kind is _RecoveryChildFailureKind.FAILED:
        reason = "recovery proof: nested child failed"
    elif kind is _RecoveryChildFailureKind.ABORTED:
        reason = "recovery proof: nested child aborted"
    else:
        reason = str(SUPERSEDED_CHILD_ABORT_REASON)
    return reduce_graph_run(state, project_failure_settlement(state, node_id, reason))


def _child_control_awaits_resume(control: ChildControlStateCoordinate | None) -> bool:
    if control is None or control.status is not GraphRunStatus.RUNNING:
        return False
    kinds = tuple(node.settlement for node in control.frontier)
    return (
        bool(kinds)
        and RecoverySettlementKind.INTERRUPTED in kinds
        and RecoverySettlementKind.PENDING_MATERIALIZED not in kinds
        and RecoverySettlementKind.PENDING_OVERRIDE not in kinds
        and RecoverySettlementKind.FAILED not in kinds
    )


def _settle_awaiting_children_after_failure(
    state: GraphRunState,
    children: tuple[ChildRecoveryDisposition, ...],
) -> GraphRunState:
    if not any(isinstance(node.settlement, FailedGraphNode) for node in state.frontier.nodes):
        return state
    current = state
    for child in children:
        control = child.control
        if not _child_control_awaits_resume(control):
            continue
        assert control is not None
        node_id = control.parent.node_id
        current = _settle_recovery_child_failure(
            current,
            node_id,
            _RecoveryChildFailureKind.SUPERSEDED_AWAITING_RESUME,
        )
    return current


def _finish_recovery_execution(
    state: GraphRunState,
    children: tuple[ChildRecoveryDisposition, ...],
) -> GraphRunState:
    settled = _settle_awaiting_children_after_failure(state, children)
    execution = settled.execution
    if execution is None:
        return settled
    return reduce_graph_run(
        settled,
        FenceGraphExecution(settled.revision, execution.token),
    )


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
                if outcome.boundary.kind
                in (
                    _ScopeBoundaryKind.EXECUTION_LIMIT,
                    _ScopeBoundaryKind.BOUNDED_RECURRENCE,
                )
            ),
            None,
        )
        if limited is not None:
            successors.append(limited.boundary)
            continue
        terminal_failure = any(isinstance(node.settlement, FailedGraphNode) for node in state.frontier.nodes) or any(
            outcome.boundary.kind in (_ScopeBoundaryKind.FAILED, _ScopeBoundaryKind.ABORTED)
            for outcome in combination.outcomes
        )
        settleable_child = any(
            outcome.boundary.kind
            in (
                _ScopeBoundaryKind.COMPLETED,
                _ScopeBoundaryKind.FAILED,
                _ScopeBoundaryKind.ABORTED,
            )
            for outcome in combination.outcomes
        )
        callable_pending = any(isinstance(graph.nodes[task.node_id], CallableNodeDefinition) for task in tasks)
        if (
            not terminal_failure
            and not settleable_child
            and not callable_pending
            and any(outcome.boundary.kind is _ScopeBoundaryKind.AWAITING_RESUME for outcome in combination.outcomes)
        ):
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
        outcome_children = tuple(
            _child_disposition_from_control(outcome.boundary.control) for outcome in combination.outcomes
        )
        live = _select_live(graph, settled, family.limits, ())
        if not live:
            settled = _finish_recovery_execution(settled, outcome_children)
        successors.append(
            _RecoveryWorkItem(
                settled,
                availability,
                live,
                outcome_children or children,
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
        if not live:
            settled = _finish_recovery_execution(settled, item.children)
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
        missing_inputs = tuple(
            (target.node_id, target.unavailable_inputs) for target in required if target.unavailable_inputs
        )
        raise GraphValueUnavailableError(
            f"resume actions {family.action_node_ids()!r} require unavailable historical values "
            f"at {scope_run!r}; "
            f"consumer inputs={missing_inputs!r}; "
            f"graph outputs={facts.unavailable_graph_outputs!r}"
        )
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
    history_window = publication_history_window(graph)
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
    cycle_entries: dict[_RecoveryCycleSignature, int] = {}
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
        if current.status is GraphRunStatus.FAILED:
            boundaries.add(_boundary(_ScopeBoundaryKind.FAILED, current, scope_run, item.availability))
            continue
        if current.status is GraphRunStatus.ABORTED:
            boundaries.add(_boundary(_ScopeBoundaryKind.ABORTED, current, scope_run, item.availability))
            continue
        status = frontier_status(current.frontier)
        if status is GraphFrontierStatus.FAILED:
            raise SnapshotMismatchError("running recovery state retained a terminal failed frontier")
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
            signature = _recovery_cycle_signature(graph, item, scope_run, history_window)
            previous_superstep = cycle_entries.get(signature) if signature is not None else None
            if previous_superstep is not None and previous_superstep < current.superstep:
                boundaries.add(
                    _boundary(
                        _ScopeBoundaryKind.BOUNDED_RECURRENCE,
                        current,
                        scope_run,
                        item.availability,
                    )
                )
                continue
            if signature is not None:
                cycle_entries[signature] = current.superstep
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
    family = _RecoveryFamily(bindings, seed.limits, seed.admitted_actions, _RecoveryProofBudget())
    for action in seed.admitted_actions:
        binding = family.binding(action.target.scope_run)
        if binding is None or binding.state.superstep != action.target.superstep:
            raise SnapshotMismatchError("recovery admitted resume action does not match a simulated scoped successor")
        node = frontier_node(binding.state.frontier, action.target.node_id)
        if node is None:
            raise SnapshotMismatchError("recovery admitted resume action target is absent from its simulated successor")
        if not isinstance(node.settlement, PendingGraphNode):
            raise SnapshotMismatchError("recovery resume action does not match its simulated successor settlement")
    availability: RecoveryAvailabilityCoordinates[GraphValueT] = RecoveryAvailabilityCoordinates[
        GraphValueT
    ].from_frames(seed.frames)
    for action in seed.admitted_actions:
        scoped_graph = _compiled_graph_at_scope(graph, action.target.scope_run.scope)
        plan = _require_node_materialization(scoped_graph, action.target.node_id)
        expected = _resume_input_coordinate(action.target, plan)
        if not availability.has_resume_input(expected):
            raise SnapshotMismatchError("recovery admitted resume action lacks its exact resume-input availability")
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
