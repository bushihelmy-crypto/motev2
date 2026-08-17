"""Single public graph composition and scoped family-driving facade."""

from collections.abc import Callable, Mapping
from dataclasses import InitVar, dataclass, replace
from typing import ClassVar, Generic, Never, Protocol, Self, TypeAlias, TypeVar, final, overload
from uuid import uuid4

from mote_kernel.execution.engine.admission import (
    admit_child_graph_input,
    admit_graph_input,
    project_graph_outputs,
)
from mote_kernel.execution.engine.recovery import (
    AdmittedActionKind,
    AdmittedResumeFact,
    RecoveryInvocationSeed,
    RecoveryStateBinding,
    preflight_recovery,
)
from mote_kernel.execution.engine.resume_input import (
    materialize_node_input,
    pending_node_input_available,
)
from mote_kernel.execution.engine.routing import graph_outputs_available
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.errors import (
    ExecutionError,
    ExecutionLimitError,
    GraphValidationError,
    GraphValueAdmissionError,
    GraphValuePublicationError,
    GraphValueUnavailableError,
    RoutingError,
    SnapshotMismatchError,
)
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END, START
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition, NodeCallable
from mote_kernel.execution.graph.outcome import (
    GraphOutcome,
    _failure,
    _GraphFailureOutcome,
    _GraphInterruptOutcome,
    _GraphSuccessOutcome,
    _interrupt,
    _success,
)
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    GraphOutputDeclarations,
    InputBindings,
    NodeOutputRef,
    canonical_nominal_type,
    canonical_port_name,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.validation import require_graph_identity
from mote_kernel.execution.graph.values import (
    FactoryValueT,
    _admit_graph_input_frame,
    _admit_graph_output_view,
    _admit_node_input_frame,
    _admit_node_output_frame,
    _GraphValues,
    _make_graph_values,
    _public_values,
    _require_graph_values,
)
from mote_kernel.execution.identity import (
    ExecutionRequestAttemptId,
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeNodeRequest,
    ResumeRequest,
    SkipFailedNodeRequest,
    StepRequest,
    UseMaterializedInput,
)
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.execution.result import (
    AbortedChild,
    AbortedGraph,
    ActiveChild,
    AwaitingResume,
    CompletedChild,
    CompletedGraph,
    ExecutableFrontier,
    GraphAbortView,
    GraphBoundary,
    GraphCommitResult,
    GraphFailureView,
    GraphInterruptView,
    GraphResult,
    MissingChild,
    PreparedResume,
    ReadyToResolve,
    StartMissingChildren,
    TaskResult,
    TaskSuccess,
    WaitingForChildren,
    _aborted_result,
    _AbortedGraphResult,
    _awaiting_result,
    _AwaitingResumeGraphResult,
    _commit_result,
    _completed_result,
    _CompletedGraphResult,
    _GraphFailureResult,
    _GraphInterruptResult,
    _GraphSuccessResult,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedResumeInput,
    ChildBoundaryAvailabilityCoordinate,
    ChildStateBinding,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    GraphInputAvailabilityCoordinate,
    GraphRunContext,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
    _CompiledFamilyIdentity,
    _context_from_continuation,
    _continuation,
    _GraphContinuation,
    _new_context,
    _new_family_identity,
)
from mote_kernel.state.graph_state import (
    AdvanceGraphFrontier,
    FailedGraphNode,
    FenceGraphExecution,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionToken,
    GraphFrontierStatus,
    GraphInterruptId,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    ParentGraphActivation,
    PendingGraphNode,
    SucceededGraphNode,
    frontier_node,
    frontier_status,
    graph_interrupt_id,
    pending_node_ids,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")
ValueT = TypeVar("ValueT")


@dataclass(frozen=True, slots=True)
class _NestedNodeCandidate(Generic[GraphValueT]):
    node_id: GraphNodeId
    graph: "Graph[GraphValueT]"
    inputs: InputBindings[GraphValueT]


NodeCandidate: TypeAlias = CallableNodeDefinition[GraphValueT] | _NestedNodeCandidate[GraphValueT]


@dataclass(frozen=True, slots=True)
class _GraphBuilderState(Generic[GraphValueT]):
    nodes: tuple[NodeCandidate[GraphValueT], ...] = ()
    edges: tuple[Edge, ...] = ()
    entries: tuple[GraphNodeId, ...] = ()
    outputs: GraphOutputDeclarations[GraphValueT] | None = None
    resources: tuple[ResourceDefinition, ...] = ()
    resume_input: ResumeInputBinding[GraphValueT] | None = None


@dataclass(frozen=True, slots=True)
class _ResumeCodec(Generic[GraphValueT]):
    encoder: Callable[[_GraphValues[GraphValueT]], bytes]
    decoder: Callable[[bytes], _GraphValues[GraphValueT]]

    def encode(self, value: _GraphValues[GraphValueT]) -> bytes:
        return self.encoder(value)

    def decode(self, payload: bytes) -> _GraphValues[GraphValueT]:
        return self.decoder(payload)


class _TransitionSeal:
    __slots__ = ()


_TRANSITION_SEAL = _TransitionSeal()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphTransition(Generic[GraphValueT]):
    scope: tuple[str, ...]
    previous_state: GraphRunState | None
    command: GraphRunCommand
    candidate_state: GraphRunState
    result: GraphCommitResult[GraphValueT] | None
    _seal: InitVar[_TransitionSeal]

    def __post_init__(self, _seal: _TransitionSeal) -> None:
        if _seal is not _TRANSITION_SEAL:
            raise SnapshotMismatchError("graph transitions can only be produced by the family driver")


class _GraphCommit(Protocol[GraphValueT]):
    async def __call__(
        self,
        transition: _GraphTransition[GraphValueT],
        /,
    ) -> GraphRunState: ...


@dataclass(frozen=True, slots=True)
class _CompiledOwner(Generic[GraphValueT]):
    graph: CompiledGraph[GraphValueT]
    family_identity: _CompiledFamilyIdentity


@dataclass(frozen=True, slots=True)
class _PlannedState:
    scope_run: ScopeRunCoordinate
    state: GraphRunState
    parent_activation: StableActivation | None


@dataclass(frozen=True, slots=True)
class _PlannedFence:
    scope_run: ScopeRunCoordinate
    command: FenceGraphExecution


@dataclass(frozen=True, slots=True)
class _PlannedResume(Generic[GraphValueT]):
    scope_run: ScopeRunCoordinate
    prepared: PreparedResume[GraphValueT]


@dataclass(frozen=True, slots=True)
class _AdvancedFrontier:
    pass


class _MissingRunValues:
    __slots__ = ()


_MISSING_RUN_VALUES = _MissingRunValues()


def _canonical_scope(scope: tuple[str, ...]) -> tuple[GraphNodeId, ...]:
    if type(scope) is not tuple:
        raise SnapshotMismatchError("resume scope must be a tuple of nested node identities")
    return tuple(GraphNodeId(canonical_port_name(segment, kind="scope")) for segment in scope)


def _canonical_resources(resources: tuple[str, ...]) -> tuple[ResourceId, ...]:
    if type(resources) is not tuple:
        raise GraphValidationError("node resources must be a tuple")
    normalized = tuple(ResourceId(canonical_port_name(resource, kind="resource")) for resource in resources)
    if len(normalized) != len(set(normalized)):
        raise GraphValidationError("a node cannot repeat one resource requirement")
    return normalized


def _compiled_at(
    root: CompiledGraph[GraphValueT],
    scope: tuple[GraphNodeId, ...],
) -> CompiledGraph[GraphValueT]:
    current = root
    for segment in scope:
        try:
            current = current.nested_graphs[segment]
        except KeyError as error:
            raise SnapshotMismatchError(f"scope references unknown nested node {segment!r}") from error
    return current


def _executors(root: CompiledGraph[GraphValueT]) -> dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]]:
    values: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]] = {}

    def collect(graph: CompiledGraph[GraphValueT]) -> None:
        values[graph.definition_scope] = GraphExecutor(graph)
        for child in graph.nested_graphs.values():
            collect(child)

    collect(root)
    return values


def _planned_state(
    states: tuple[_PlannedState, ...],
    coordinate: ScopeRunCoordinate,
) -> _PlannedState:
    match = next((binding for binding in states if binding.scope_run == coordinate), None)
    if match is None:
        raise SnapshotMismatchError(f"lineage does not contain one state at {coordinate!r}")
    return match


def _replace_planned_state(
    states: tuple[_PlannedState, ...],
    replacement: _PlannedState,
) -> tuple[_PlannedState, ...]:
    return tuple(
        sorted(
            (replacement if binding.scope_run == replacement.scope_run else binding for binding in states),
            key=lambda binding: binding.scope_run,
        )
    )


def _lineage_states(context: GraphRunContext[GraphValueT]) -> tuple[_PlannedState, ...]:
    root_state = context.root_binding.state
    values = [_PlannedState(root_scope_run(root_state.run_id), root_state, None)]
    values.extend(
        _PlannedState(binding.coordinate, binding.state, binding.parent_activation) for binding in context.child_states
    )
    canonical = tuple(sorted(values, key=lambda binding: binding.scope_run))
    coordinates = tuple(binding.scope_run for binding in canonical)
    if len(coordinates) != len(set(coordinates)):
        raise SnapshotMismatchError("lineage repeats one scoped graph run")
    return canonical


def _plan_fences(
    graph: CompiledGraph[GraphValueT],
    states: tuple[_PlannedState, ...],
    executors: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]],
) -> tuple[tuple[_PlannedState, ...], tuple[_PlannedFence, ...]]:
    planned = states
    fences: list[_PlannedFence] = []
    for binding in states:
        _compiled_at(graph, binding.scope_run.scope)
        executor = executors[binding.scope_run.scope]
        executor.validate_state(binding.state)
        if binding.state.run_id != binding.scope_run.graph_run_id:
            raise SnapshotMismatchError("lineage state does not match its scoped run identity")
        if binding.parent_activation is not None:
            expected_parent = ParentGraphActivation(
                binding.parent_activation.scope_run.graph_run_id,
                binding.parent_activation.superstep,
                binding.parent_activation.node_id,
            )
            if (
                not binding.scope_run.scope
                or binding.state.parent != expected_parent
                or child_scope_run_for_activation(
                    binding.parent_activation.scope_run,
                    expected_parent,
                )
                != binding.scope_run
            ):
                raise SnapshotMismatchError("child lineage binding has inconsistent parent coordinates")
        execution = binding.state.execution
        if execution is None:
            continue
        command = FenceGraphExecution(binding.state.revision, execution.token)
        candidate = reduce_graph_run(binding.state, command)
        fences.append(_PlannedFence(binding.scope_run, command))
        planned = _replace_planned_state(
            planned,
            _PlannedState(binding.scope_run, candidate, binding.parent_activation),
        )
    return planned, tuple(fences)


def _resolve_scope_run(
    graph: CompiledGraph[GraphValueT],
    states: tuple[_PlannedState, ...],
    scope: tuple[GraphNodeId, ...],
) -> ScopeRunCoordinate:
    root = states[0]
    coordinate = root.scope_run
    scoped_graph = graph
    for segment in scope:
        state = _planned_state(states, coordinate).state
        nested = scoped_graph.nested_graphs.get(segment)
        node = frontier_node(state.frontier, segment)
        if nested is None or node is None or not isinstance(node.settlement, PendingGraphNode):
            raise SnapshotMismatchError(f"resume scope segment {segment!r} is not one current nested activation")
        parent = ParentGraphActivation(state.run_id, state.superstep, segment)
        coordinate = child_scope_run_for_activation(coordinate, parent)
        _planned_state(states, coordinate)
        scoped_graph = nested
    return coordinate


def _resume_facts(
    scope_run: ScopeRunCoordinate,
    superstep: int,
    actions: tuple[ResumeNodeRequest[GraphValueT], ...],
    prepared: PreparedResume[GraphValueT],
) -> tuple[AdmittedResumeFact[GraphValueT], ...]:
    facts: list[AdmittedResumeFact[GraphValueT]] = []
    for action in actions:
        activation = StableActivation(scope_run, superstep, action.node_id)
        admitted = next(
            (item.coordinate for item in prepared.inputs if item.coordinate.activation.node_id == action.node_id),
            None,
        )
        if isinstance(action, ResumeFailedNodeRequest):
            kind = (
                AdmittedActionKind.RESUME_FAILED_WITH
                if isinstance(action.input, OverrideNodeInput)
                else AdmittedActionKind.RESUME_FAILED
            )
            facts.append(AdmittedResumeFact(activation, kind, None, None, None, admitted))
        elif isinstance(action, ResumeInterruptedNodeRequest):
            facts.append(
                AdmittedResumeFact(
                    activation,
                    AdmittedActionKind.RESUME_INTERRUPTED,
                    action.interrupt_id,
                    None,
                    None,
                    admitted,
                )
            )
        else:
            facts.append(
                AdmittedResumeFact(
                    activation,
                    AdmittedActionKind.SKIP_FAILED,
                    None,
                    action.reason,
                    GraphRouteId(action.route) if action.route is not None else None,
                    None,
                )
            )
    return tuple(facts)


def _forbid_aborted_child_restart(
    graph: CompiledGraph[GraphValueT],
    states: tuple[_PlannedState, ...],
    scope_run: ScopeRunCoordinate,
    state: GraphRunState,
    actions: tuple[ResumeNodeRequest[GraphValueT], ...],
) -> None:
    scoped_graph = _compiled_at(graph, scope_run.scope)
    for action in actions:
        if not isinstance(action, ResumeFailedNodeRequest) or action.node_id not in scoped_graph.nested_graphs:
            continue
        parent = ParentGraphActivation(state.run_id, state.superstep, action.node_id)
        child_coordinate = child_scope_run_for_activation(scope_run, parent)
        _planned_state(states, child_coordinate)
        raise SnapshotMismatchError("an aborted nested child cannot be restarted with the same run identity")


def _plan_resumes(
    graph: CompiledGraph[GraphValueT],
    states: tuple[_PlannedState, ...],
    frames: ScopedFrameIndex[GraphValueT],
    resume: tuple[ResumeNodeRequest[GraphValueT], ...],
    executors: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]],
) -> tuple[
    tuple[_PlannedState, ...],
    ScopedFrameIndex[GraphValueT],
    tuple[_PlannedResume[GraphValueT], ...],
    tuple[AdmittedResumeFact[GraphValueT], ...],
]:
    if not resume:
        return states, frames, (), ()
    if type(resume) is not tuple:
        raise SnapshotMismatchError("resume actions must be supplied as a tuple")
    canonical = tuple(sorted(resume, key=lambda action: (action.scope, action.node_id)))
    if canonical != resume:
        raise SnapshotMismatchError("resume actions must be supplied in canonical scope/node order")
    planned_states = states
    candidate_frames = frames
    plans: list[_PlannedResume[GraphValueT]] = []
    facts: list[AdmittedResumeFact[GraphValueT]] = []
    scopes = tuple(dict.fromkeys(action.scope for action in canonical))
    for scope in scopes:
        actions = tuple(action for action in canonical if action.scope == scope)
        scope_run = _resolve_scope_run(graph, planned_states, scope)
        binding = _planned_state(planned_states, scope_run)
        _forbid_aborted_child_restart(graph, planned_states, scope_run, binding.state, actions)
        prepared = executors[scope].resume(ResumeRequest(binding.state, scope_run, candidate_frames, actions))
        candidate = reduce_graph_run(binding.state, prepared.command)
        action_facts = _resume_facts(scope_run, binding.state.superstep, actions, prepared)
        for admitted in prepared.inputs:
            candidate_frames = candidate_frames.add_resume_input(admitted)
        plans.append(_PlannedResume(scope_run, prepared))
        facts.extend(action_facts)
        planned_states = _replace_planned_state(
            planned_states,
            _PlannedState(scope_run, candidate, binding.parent_activation),
        )
    return planned_states, candidate_frames, tuple(plans), tuple(facts)


def _recovery_seed(
    states: tuple[_PlannedState, ...],
    frames: ScopedFrameIndex[GraphValueT],
    limits: ExecutionLimits,
    facts: tuple[AdmittedResumeFact[GraphValueT], ...],
) -> RecoveryInvocationSeed[GraphValueT]:
    root = states[0]
    children = tuple(binding for binding in states if binding.scope_run.scope)
    return RecoveryInvocationSeed(
        RecoveryStateBinding(root.scope_run, root.state),
        tuple(RecoveryStateBinding(binding.scope_run, binding.state) for binding in children),
        frames,
        limits,
        facts,
    )


def _admit_state_owned_overrides(
    graph: CompiledGraph[GraphValueT],
    states: tuple[_PlannedState, ...],
    frames: ScopedFrameIndex[GraphValueT],
) -> None:
    for binding in states:
        if binding.state.status is not GraphRunStatus.RUNNING:
            continue
        scoped_graph = _compiled_at(graph, binding.scope_run.scope)
        for node in binding.state.frontier.nodes:
            if isinstance(node.settlement, PendingGraphNode) and isinstance(
                node.settlement.input,
                OverrideGraphNodeInput,
            ):
                materialize_node_input(
                    scoped_graph,
                    binding.state,
                    binding.scope_run,
                    frames,
                    node.node_id,
                )


def _validate_frame_index(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
) -> None:
    bindings = _lineage_states(context)
    coordinates = frozenset(binding.scope_run for binding in bindings)
    if any(
        type(record) is not AdmittedGraphInput or type(record.coordinate) is not GraphInputAvailabilityCoordinate
        for record in context.frames.graph_inputs
    ):
        raise SnapshotMismatchError("continuation graph input segment contains a malformed record")
    if any(
        type(record) is not ConfirmedPublication or type(record.coordinate) is not PublicationAvailabilityCoordinate
        for record in context.frames.publications
    ):
        raise SnapshotMismatchError("continuation publication segment contains a malformed record")
    if any(
        type(record) is not AdmittedResumeInput or type(record.coordinate) is not ResumeInputAvailabilityCoordinate
        for record in context.frames.resume_inputs
    ):
        raise SnapshotMismatchError("continuation resume input segment contains a malformed record")
    if any(
        type(record) is not ConfirmedChildBoundary or type(record.coordinate) is not ChildBoundaryAvailabilityCoordinate
        for record in context.frames.child_boundaries
    ):
        raise SnapshotMismatchError("continuation child boundary segment contains a malformed record")
    graph_input_coordinates = tuple(record.coordinate for record in context.frames.graph_inputs)
    publication_coordinates = tuple(record.coordinate for record in context.frames.publications)
    resume_coordinates = tuple(record.coordinate for record in context.frames.resume_inputs)
    boundary_coordinates = tuple(record.coordinate for record in context.frames.child_boundaries)
    for name, segment in (
        ("graph input", graph_input_coordinates),
        ("publication", publication_coordinates),
        ("resume input", resume_coordinates),
        ("child boundary", boundary_coordinates),
    ):
        if len(segment) != len(set(segment)) or segment != tuple(sorted(segment)):
            raise SnapshotMismatchError(f"continuation {name} coordinates are not unique and canonical")
    for record in context.frames.graph_inputs:
        coordinate = record.coordinate
        if coordinate.scope_run not in coordinates:
            raise SnapshotMismatchError("continuation graph input belongs to an unknown scoped run")
        scoped_graph = _compiled_at(graph, coordinate.scope_run.scope)
        if coordinate.descriptor != scoped_graph.graph_input_descriptor.identity:
            raise SnapshotMismatchError("continuation graph input descriptor does not match its scope")
        declarations = tuple(
            (declaration.name, declaration.descriptor)
            for declaration in scoped_graph.graph_input_descriptor.declarations.entries
        )
        try:
            _admit_graph_input_frame(record.frame, declarations)
        except GraphValueAdmissionError as error:
            raise SnapshotMismatchError("continuation graph input frame does not match its descriptor") from error
    for record in context.frames.publications:
        coordinate = record.coordinate
        binding = _planned_state(bindings, coordinate.activation.scope_run)
        scoped_graph = _compiled_at(graph, coordinate.activation.scope_run.scope)
        publication = scoped_graph.publications.get(coordinate.activation.node_id)
        if (
            publication is None
            or coordinate.descriptor != publication.descriptor.identity
            or coordinate.activation.superstep > binding.state.superstep
            or record.acknowledged_revision < 1
            or record.execution_token.generation < 1
        ):
            raise SnapshotMismatchError("continuation publication has inconsistent coordinates")
        declarations = tuple(
            (declaration.name, declaration.descriptor) for declaration in publication.descriptor.declarations.entries
        )
        try:
            _admit_node_output_frame(record.frame, declarations)
        except GraphValueAdmissionError as error:
            raise SnapshotMismatchError("continuation publication frame does not match its descriptor") from error
    for record in context.frames.resume_inputs:
        coordinate = record.coordinate
        binding = _planned_state(bindings, coordinate.activation.scope_run)
        scoped_graph = _compiled_at(graph, coordinate.activation.scope_run.scope)
        materialization = scoped_graph.materializations.get(coordinate.activation.node_id)
        if (
            materialization is None
            or coordinate.descriptor != materialization.descriptor.identity
            or coordinate.activation.superstep > binding.state.superstep
        ):
            raise SnapshotMismatchError("continuation resume input has inconsistent coordinates")
        declarations = tuple(
            (declaration.name, declaration.descriptor)
            for declaration in materialization.descriptor.declarations.entries
        )
        try:
            _admit_node_input_frame(record.frame, declarations)
        except GraphValueAdmissionError as error:
            raise SnapshotMismatchError("continuation resume input frame does not match its descriptor") from error
    for record in context.frames.child_boundaries:
        coordinate = record.coordinate
        binding = _planned_state(bindings, coordinate.child_scope_run)
        scoped_graph = _compiled_at(graph, coordinate.child_scope_run.scope)
        if (
            coordinate.descriptor != scoped_graph.graph_output_descriptor.identity
            or binding.state.status is not GraphRunStatus.COMPLETED
        ):
            raise SnapshotMismatchError("continuation child boundary has inconsistent coordinates")
        declarations = tuple(
            (declaration.name, declaration.descriptor)
            for declaration in scoped_graph.graph_output_descriptor.declarations.entries
        )
        try:
            _admit_graph_output_view(record.frame, declarations)
        except GraphValueAdmissionError as error:
            raise SnapshotMismatchError("continuation child boundary frame does not match its descriptor") from error


def _validate_complete_context(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
) -> None:
    states = _lineage_states(context)
    admitted_inputs = frozenset(record.coordinate.scope_run for record in context.frames.graph_inputs)
    if admitted_inputs != frozenset(binding.scope_run for binding in states):
        raise SnapshotMismatchError("complete continuation must retain every scoped graph input")
    for binding in states:
        scoped_graph = _compiled_at(graph, binding.scope_run.scope)
        state = binding.state
        for node in state.frontier.nodes:
            if isinstance(node.settlement, SucceededGraphNode):
                coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
                    StableActivation(binding.scope_run, state.superstep, node.node_id),
                    scoped_graph.publications[node.node_id].descriptor.identity,
                )
                if not context.frames.has_publication(coordinate):
                    raise SnapshotMismatchError("complete continuation is missing a current success publication")
            if isinstance(node.settlement, PendingGraphNode):
                if node.node_id in scoped_graph.nested_graphs:
                    parent = ParentGraphActivation(state.run_id, state.superstep, node.node_id)
                    child_coordinate = child_scope_run_for_activation(binding.scope_run, parent)
                    _planned_state(states, child_coordinate)
                elif not pending_node_input_available(
                    scoped_graph,
                    state,
                    binding.scope_run,
                    context.frames,
                    node.node_id,
                ):
                    raise SnapshotMismatchError("complete continuation is missing a current node input source")
        if state.status is GraphRunStatus.COMPLETED and not graph_outputs_available(
            scoped_graph,
            binding.scope_run,
            state.superstep,
            context.frames,
        ):
            raise SnapshotMismatchError("complete continuation is missing a completed graph output")
        if binding.parent_activation is not None and state.status is GraphRunStatus.COMPLETED:
            boundary: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
                binding.scope_run,
                scoped_graph.graph_output_descriptor.identity,
            )
            if not context.frames.has_child_boundary(boundary):
                raise SnapshotMismatchError("complete continuation is missing a completed child boundary")


def _validate_context(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
) -> None:
    _validate_frame_index(graph, context)
    if not context.recovered:
        _validate_complete_context(graph, context)


async def _commit_transition(
    scope_run: ScopeRunCoordinate,
    previous_state: GraphRunState | None,
    command: GraphRunCommand,
    result: TaskResult[GraphValueT] | None,
    commit: _GraphCommit[GraphValueT] | None,
) -> GraphRunState:
    candidate = reduce_graph_run(previous_state, command)
    admitted = _commit_result(result) if result is not None else None
    transition = _GraphTransition(
        scope=tuple(scope_run.scope),
        previous_state=previous_state,
        command=command,
        candidate_state=candidate,
        result=admitted,
        _seal=_TRANSITION_SEAL,
    )
    if commit is None:
        return candidate
    confirmed = await commit(transition)
    if type(confirmed) is not GraphRunState or confirmed != candidate:
        raise SnapshotMismatchError("commit must return the exact authoritative reducer successor")
    return confirmed


def _request(
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    context: GraphRunContext[GraphValueT],
    limits: ExecutionLimits,
    projections: tuple[MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild, ...],
) -> StepRequest[GraphValueT]:
    return StepRequest(
        state,
        scope_run,
        context.frames,
        ExecutionRequestAttemptId(str(uuid4())),
        projections,
        limits,
    )


def _ensure_child_boundary(
    graph: CompiledGraph[GraphValueT],
    coordinate: ScopeRunCoordinate,
    context: GraphRunContext[GraphValueT],
) -> None:
    state = context.state_at(coordinate)
    availability: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
        coordinate,
        graph.graph_output_descriptor.identity,
    )
    if context.frames.has_child_boundary(availability):
        return
    view = project_graph_outputs(graph, coordinate, state.superstep, context.frames)
    context.frames = context.frames.add_child_boundary(ConfirmedChildBoundary(availability, view))


def _child_projections(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    context: GraphRunContext[GraphValueT],
) -> tuple[MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild, ...]:
    projections: list[MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild] = []
    for node_id in pending_node_ids(state.frontier):
        if node_id not in graph.nested_graphs:
            continue
        parent = ParentGraphActivation(state.run_id, state.superstep, node_id)
        coordinate = child_scope_run_for_activation(scope_run, parent)
        binding = context.child_state(coordinate)
        if binding is None:
            projections.append(MissingChild(parent))
            continue
        child_state = binding.state
        child_graph = graph.nested_graphs[node_id]
        if child_state.status is GraphRunStatus.RUNNING:
            projections.append(ActiveChild(parent, child_state))
        elif child_state.status is GraphRunStatus.COMPLETED:
            _ensure_child_boundary(child_graph, coordinate, context)
            availability: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
                coordinate,
                child_graph.graph_output_descriptor.identity,
            )
            projections.append(CompletedChild(parent, child_state, context.frames.lookup(availability).frame))
        else:
            projections.append(AbortedChild(parent, child_state))
    return tuple(projections)


async def _consume_session(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    session: GraphExecutionSession[GraphValueT],
    execution_token: GraphExecutionToken,
    context: GraphRunContext[GraphValueT],
    commit: _GraphCommit[GraphValueT] | None,
) -> None:
    state = context.state_at(scope_run)
    async with session:
        while True:
            try:
                completed = await session.next(state)
            except StopAsyncIteration:
                return
            except Exception:
                await session.aclose()
                fenced = await _commit_transition(
                    scope_run,
                    state,
                    FenceGraphExecution(state.revision, execution_token),
                    None,
                    commit,
                )
                context.replace_state(scope_run, fenced)
                raise
            confirmed = await _commit_transition(
                scope_run,
                state,
                completed.command,
                completed.result,
                commit,
            )
            context.replace_state(scope_run, confirmed)
            if isinstance(completed.result, TaskSuccess):
                activation = StableActivation(scope_run, state.superstep, completed.result.task.node_id)
                coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
                    activation,
                    graph.publications[completed.result.task.node_id].descriptor.identity,
                )
                context.frames = context.frames.add_publication(
                    ConfirmedPublication(
                        coordinate,
                        completed.result.output,
                        confirmed.revision,
                        completed.command.execution,
                    )
                )
            state = confirmed


async def _execute_frontier(
    graph: CompiledGraph[GraphValueT],
    executor: GraphExecutor[GraphValueT],
    scope_run: ScopeRunCoordinate,
    prepared: ExecutableFrontier,
    prepared_request: StepRequest[GraphValueT],
    context: GraphRunContext[GraphValueT],
    commit: _GraphCommit[GraphValueT] | None,
) -> None:
    state = context.state_at(scope_run)
    claimed = await _commit_transition(scope_run, state, prepared.claim.command, None, commit)
    context.replace_state(scope_run, claimed)
    request = replace(prepared_request, state=claimed)
    try:
        session = await executor.execute(prepared.claim, request)
    except Exception:
        fenced = await _commit_transition(
            scope_run,
            claimed,
            FenceGraphExecution(claimed.revision, prepared.claim.snapshot.token),
            None,
            commit,
        )
        context.replace_state(scope_run, fenced)
        raise
    await _consume_session(
        graph,
        scope_run,
        session,
        prepared.claim.snapshot.token,
        context,
        commit,
    )


async def _start_missing_children(
    parent_graph: CompiledGraph[GraphValueT],
    parent_scope_run: ScopeRunCoordinate,
    action: StartMissingChildren[GraphValueT],
    context: GraphRunContext[GraphValueT],
    commit: _GraphCommit[GraphValueT] | None,
) -> None:
    parent_state = context.state_at(parent_scope_run)
    for child in action.children:
        coordinate = child_scope_run_for_activation(parent_scope_run, child.parent)
        input_frame = materialize_node_input(
            parent_graph,
            parent_state,
            parent_scope_run,
            context.frames,
            child.parent.node_id,
        )
        child_input = admit_child_graph_input(child.graph, input_frame)
        confirmed = await _commit_transition(coordinate, None, child.command, None, commit)
        activation = StableActivation(
            parent_scope_run,
            child.parent.superstep,
            child.parent.node_id,
        )
        context.replace_child(ChildStateBinding(coordinate, activation, confirmed))
        availability: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
            coordinate,
            child.graph.graph_input_descriptor.identity,
        )
        context.frames = context.frames.add_graph_input(AdmittedGraphInput(availability, child_input))


async def _advance_scope_quantum(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    context: GraphRunContext[GraphValueT],
    executors: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]],
    limits: ExecutionLimits,
    commit: _GraphCommit[GraphValueT] | None,
) -> GraphBoundary | _AdvancedFrontier | None:
    state = context.state_at(scope_run)
    projections = _child_projections(
        graph,
        state,
        scope_run,
        context,
    )
    request = _request(state, scope_run, context, limits, projections)
    disposition = await executors[graph.definition_scope].prepare(request)
    if isinstance(disposition, ReadyToResolve):
        confirmed = await _commit_transition(
            scope_run,
            state,
            disposition.command,
            None,
            commit,
        )
        context.replace_state(scope_run, confirmed)
        return _AdvancedFrontier() if isinstance(disposition.command, AdvanceGraphFrontier) else None
    if isinstance(disposition, ExecutableFrontier):
        await _execute_frontier(
            graph,
            executors[graph.definition_scope],
            scope_run,
            disposition,
            request,
            context,
            commit,
        )
        return None
    if isinstance(disposition, WaitingForChildren):
        awaiting = await _drive_children(
            graph,
            scope_run,
            disposition,
            context,
            executors,
            limits,
            commit,
        )
        if awaiting:
            return AwaitingResume((), ())
        return None
    return disposition


async def _drive_children(
    parent_graph: CompiledGraph[GraphValueT],
    parent_scope_run: ScopeRunCoordinate,
    waiting: WaitingForChildren[GraphValueT],
    context: GraphRunContext[GraphValueT],
    executors: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]],
    limits: ExecutionLimits,
    commit: _GraphCommit[GraphValueT] | None,
) -> bool:
    if isinstance(waiting.action, StartMissingChildren):
        await _start_missing_children(
            parent_graph,
            parent_scope_run,
            waiting.action,
            context,
            commit,
        )
    while True:
        parent_state = context.state_at(parent_scope_run)
        projections = _child_projections(
            parent_graph,
            parent_state,
            parent_scope_run,
            context,
        )
        active = tuple(projection for projection in projections if isinstance(projection, ActiveChild))
        if not active:
            return False
        runnable = tuple(
            projection
            for projection in active
            if frontier_status(projection.child_state.frontier) is not GraphFrontierStatus.AWAITING_RESUME
        )
        if not runnable:
            return True
        for projection in runnable:
            coordinate = child_scope_run_for_activation(parent_scope_run, projection.parent)
            child_graph = parent_graph.nested_graphs[projection.parent.node_id]
            await _advance_scope_quantum(
                child_graph,
                coordinate,
                context,
                executors,
                limits,
                commit,
            )


async def _drive_root(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
    executors: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]],
    limits: ExecutionLimits,
    commit: _GraphCommit[GraphValueT] | None,
) -> GraphBoundary:
    scope_run = root_scope_run(context.root_binding.state.run_id)
    while True:
        disposition = await _advance_scope_quantum(
            graph,
            scope_run,
            context,
            executors,
            limits,
            commit,
        )
        if isinstance(disposition, _AdvancedFrontier):
            continue
        if disposition is not None:
            return disposition


def _scoped_states(
    context: GraphRunContext[GraphValueT],
) -> tuple[tuple[tuple[str, ...], GraphRunState], ...]:
    return (
        ((), context.root_binding.state),
        *((tuple(binding.coordinate.scope), binding.state) for binding in context.child_states),
    )


def _failure_views(context: GraphRunContext[GraphValueT]) -> tuple[GraphFailureView, ...]:
    return tuple(
        GraphFailureView(scope, node.node_id, str(node.settlement.failure))
        for scope, state in _scoped_states(context)
        for node in state.frontier.nodes
        if isinstance(node.settlement, FailedGraphNode)
    )


def _interrupt_views(context: GraphRunContext[GraphValueT]) -> tuple[GraphInterruptView, ...]:
    views: list[GraphInterruptView] = []
    for scope, state in _scoped_states(context):
        for node in state.frontier.nodes:
            settlement = node.settlement
            if not isinstance(settlement, InterruptedGraphNode):
                continue
            identity = settlement.interrupt.identity
            views.append(
                GraphInterruptView(
                    scope,
                    node.node_id,
                    graph_interrupt_id(
                        identity.run_id,
                        identity.superstep,
                        identity.node_id,
                        identity.execution_generation,
                    ),
                    bytes(settlement.interrupt.request_payload),
                )
            )
    return tuple(views)


def project_graph_result(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
    disposition: GraphBoundary,
) -> GraphResult[GraphValueT]:
    state = context.root_binding.state
    continuation = _continuation(context)
    if isinstance(disposition, CompletedGraph):
        view = project_graph_outputs(
            graph,
            root_scope_run(state.run_id),
            state.superstep,
            context.frames,
        )
        return _completed_result(state, continuation, _public_values(view))
    if isinstance(disposition, AbortedGraph):
        if state.abort is None:
            raise SnapshotMismatchError("aborted root state is missing its canonical abort")
        return _aborted_result(state, continuation, GraphAbortView((), state.abort.reason))
    return _awaiting_result(
        state,
        continuation,
        _failure_views(context),
        _interrupt_views(context),
    )


class Graph(Generic[GraphValueT]):
    """Compose and execute one typed graph family through the sole engine."""

    START: ClassVar[str] = START
    END: ClassVar[str] = END
    Values = _GraphValues
    SuccessOutcome = _GraphSuccessOutcome
    FailureOutcome = _GraphFailureOutcome
    InterruptOutcome = _GraphInterruptOutcome
    Outcome = GraphOutcome
    ResumeAction = ResumeNodeRequest
    Commit = _GraphCommit
    Transition = _GraphTransition
    SuccessResult = _GraphSuccessResult
    FailureResult = _GraphFailureResult
    InterruptResult = _GraphInterruptResult
    Continuation = _GraphContinuation
    CompletedResult = _CompletedGraphResult
    AbortedResult = _AbortedGraphResult
    AwaitingResumeResult = _AwaitingResumeGraphResult
    Result = GraphResult
    State = GraphRunState
    Error = ExecutionError
    ValidationError = GraphValidationError
    SnapshotMismatchError = SnapshotMismatchError
    ExecutionLimitError = ExecutionLimitError
    ValueAdmissionError = GraphValueAdmissionError
    ValueUnavailableError = GraphValueUnavailableError
    ValuePublicationError = GraphValuePublicationError
    RoutingError = RoutingError

    __slots__ = ("_builder_state", "_compiled_owner", "_definition_id", "_version")

    def __init__(self, definition_id: str, *, version: int = 1) -> None:
        require_graph_identity(definition_id, kind="graph")
        if type(version) is not int or version < 1:
            raise GraphValidationError("graph version must be an exact positive integer")
        self._definition_id = GraphDefinitionId(definition_id)
        self._version = GraphDefinitionVersion(version)
        self._builder_state: _GraphBuilderState[GraphValueT] = _GraphBuilderState()
        self._compiled_owner: _CompiledOwner[GraphValueT] | None = None

    def _require_mutable(self) -> _GraphBuilderState[GraphValueT]:
        if self._compiled_owner is not None:
            raise GraphValidationError("a graph definition is immutable after its first successful compile")
        return self._builder_state

    def _commit_builder(
        self,
        previous: _GraphBuilderState[GraphValueT],
        replacement: _GraphBuilderState[GraphValueT],
    ) -> None:
        if self._compiled_owner is not None or self._builder_state is not previous:
            raise GraphValidationError("graph builder state changed before its atomic replacement")
        self._builder_state = replacement

    @staticmethod
    def graph_input(name: str, value_type: type[ValueT]) -> GraphInputRef[ValueT]:
        return GraphInputRef(
            canonical_port_name(name, kind="graph input"),
            canonical_nominal_type(value_type),
        )

    @staticmethod
    def node_output(node_id: str, output_name: str) -> NodeOutputRef:
        return NodeOutputRef(
            GraphNodeId(canonical_port_name(node_id, kind="source node")),
            canonical_port_name(output_name, kind="source output"),
        )

    @staticmethod
    @overload
    def values() -> "Graph.Values[Never]": ...

    @staticmethod
    @overload
    def values(**values: FactoryValueT) -> "Graph.Values[FactoryValueT]": ...

    @staticmethod
    def values(**values: FactoryValueT) -> "Graph.Values[FactoryValueT]":
        return _make_graph_values(**values)

    @staticmethod
    def success(
        output: "Graph.Values[FactoryValueT]",
        *,
        route: str | None = None,
    ) -> "Graph.SuccessOutcome[FactoryValueT]":
        return _success(output, route=route)

    @staticmethod
    def failure(reason: str) -> "Graph.FailureOutcome":
        return _failure(reason)

    @staticmethod
    def interrupt(request_payload: bytes) -> "Graph.InterruptOutcome":
        return _interrupt(request_payload)

    @overload
    def add_node(
        self,
        node_id: str,
        operation: NodeCallable[GraphValueT],
        *,
        inputs: Mapping[str, GraphInputRef[GraphValueT] | NodeOutputRef],
        outputs: Mapping[str, type[GraphValueT]],
        resources: tuple[str, ...] = (),
    ) -> Self: ...

    @overload
    def add_node(
        self,
        node_id: str,
        operation: "Graph[GraphValueT]",
        *,
        inputs: Mapping[str, GraphInputRef[GraphValueT] | NodeOutputRef],
    ) -> Self: ...

    def add_node(
        self,
        node_id: str,
        operation: NodeCallable[GraphValueT] | "Graph[GraphValueT]",
        *,
        inputs: Mapping[
            str,
            GraphInputRef[GraphValueT] | NodeOutputRef | type[GraphValueT],
        ],
        outputs: Mapping[
            str,
            type[GraphValueT] | GraphInputRef[GraphValueT] | NodeOutputRef,
        ]
        | None = None,
        resources: tuple[str, ...] = (),
    ) -> Self:
        state = self._require_mutable()
        canonical_id = GraphNodeId(canonical_port_name(node_id, kind="node"))
        bindings = normalize_input_bindings(inputs)
        if isinstance(operation, Graph):
            if outputs is not None or resources:
                raise GraphValidationError("nested graph nodes do not declare parent outputs or resources")
            candidate: NodeCandidate[GraphValueT] = _NestedNodeCandidate(
                canonical_id,
                operation,
                bindings,
            )
            replacement = replace(state, nodes=(*state.nodes, candidate))
        else:
            if not callable(operation):
                raise GraphValidationError("ordinary graph node operation must be callable")
            if outputs is None:
                raise GraphValidationError("callable graph nodes require an explicit outputs mapping")
            declarations = normalize_output_declarations(outputs)
            resource_ids = _canonical_resources(resources)
            candidate = CallableNodeDefinition(
                canonical_id,
                operation,
                bindings,
                declarations,
                resource_ids,
            )
            known = {resource.resource_id for resource in state.resources}
            added = tuple(
                ResourceDefinition(resource_id, len(state.resources) + ordinal)
                for ordinal, resource_id in enumerate(
                    resource_id for resource_id in resource_ids if resource_id not in known
                )
            )
            replacement = replace(
                state,
                nodes=(*state.nodes, candidate),
                resources=(*state.resources, *added),
            )
        self._commit_builder(state, replacement)
        return self

    def set_outputs(
        self,
        outputs: Mapping[
            str,
            GraphInputRef[GraphValueT] | NodeOutputRef | type[GraphValueT],
        ],
    ) -> Self:
        state = self._require_mutable()
        if state.outputs is not None:
            raise GraphValidationError("graph outputs can be declared exactly once")
        declaration = normalize_graph_output_declarations(outputs)
        replacement = replace(state, outputs=declaration)
        self._commit_builder(state, replacement)
        return self

    def add_edge(self, source: str, target: str) -> Self:
        state = self._require_mutable()
        canonical_source = canonical_port_name(source, kind="edge source")
        canonical_target = canonical_port_name(target, kind="edge target")
        if canonical_source == Graph.START:
            if canonical_target in (Graph.START, Graph.END):
                raise GraphValidationError("START must target one concrete node")
            replacement = replace(state, entries=(*state.entries, GraphNodeId(canonical_target)))
        else:
            replacement = replace(
                state,
                edges=(
                    *state.edges,
                    DirectEdge(
                        GraphNodeId(canonical_source),
                        END if canonical_target == Graph.END else GraphNodeId(canonical_target),
                    ),
                ),
            )
        self._commit_builder(state, replacement)
        return self

    def add_conditional_edge(self, source: str, route: str, target: str) -> Self:
        state = self._require_mutable()
        canonical_source = canonical_port_name(source, kind="conditional source")
        canonical_route = canonical_port_name(route, kind="route")
        canonical_target = canonical_port_name(target, kind="conditional target")
        if canonical_source in (Graph.START, Graph.END) or canonical_target == Graph.START:
            raise GraphValidationError("conditional edge has an invalid boundary direction")
        edge = ConditionalEdge(
            GraphNodeId(canonical_source),
            GraphRouteId(canonical_route),
            END if canonical_target == Graph.END else GraphNodeId(canonical_target),
        )
        replacement = replace(state, edges=(*state.edges, edge))
        self._commit_builder(state, replacement)
        return self

    def add_join(self, sources: tuple[str, ...], target: str) -> Self:
        state = self._require_mutable()
        if type(sources) is not tuple:
            raise GraphValidationError("join sources must be a tuple")
        canonical_sources = tuple(GraphNodeId(canonical_port_name(source, kind="join source")) for source in sources)
        canonical_target = canonical_port_name(target, kind="join target")
        if any(source in (Graph.START, Graph.END) for source in canonical_sources) or canonical_target == Graph.START:
            raise GraphValidationError("join edge has an invalid boundary direction")
        edge = JoinEdge(
            canonical_sources,
            END if canonical_target == Graph.END else GraphNodeId(canonical_target),
        )
        replacement = replace(state, edges=(*state.edges, edge))
        self._commit_builder(state, replacement)
        return self

    def set_resume_codec(
        self,
        codec_id: str,
        version: int,
        encoder: Callable[["Graph.Values[GraphValueT]"], bytes],
        decoder: Callable[[bytes], "Graph.Values[GraphValueT]"],
    ) -> Self:
        state = self._require_mutable()
        if state.resume_input is not None:
            raise GraphValidationError("resume input codec can be declared exactly once")
        if not callable(encoder) or not callable(decoder):
            raise GraphValidationError("resume input encoder and decoder must be callable")
        canonical_id = GraphResumeInputCodecId(canonical_port_name(codec_id, kind="resume codec"))
        if type(version) is not int or version < 1:
            raise GraphValidationError("resume codec version must be an exact positive integer")
        codec = _ResumeCodec(encoder, decoder)
        binding = ResumeInputBinding(canonical_id, version, codec, codec)
        replacement = replace(state, resume_input=binding)
        self._commit_builder(state, replacement)
        return self

    def resume_failed(
        self,
        node_id: str,
        *,
        scope: tuple[str, ...] = (),
    ) -> "Graph.ResumeAction[GraphValueT]":
        return ResumeFailedNodeRequest(
            _canonical_scope(scope),
            GraphNodeId(canonical_port_name(node_id, kind="resume node")),
            UseMaterializedInput(),
        )

    def resume_failed_with(
        self,
        node_id: str,
        values: "Graph.Values[GraphValueT]",
        *,
        scope: tuple[str, ...] = (),
    ) -> "Graph.ResumeAction[GraphValueT]":
        return ResumeFailedNodeRequest(
            _canonical_scope(scope),
            GraphNodeId(canonical_port_name(node_id, kind="resume node")),
            OverrideNodeInput(_require_graph_values(values)),
        )

    def resume_interrupted(
        self,
        node_id: str,
        interrupt_id: str,
        values: "Graph.Values[GraphValueT]",
        *,
        scope: tuple[str, ...] = (),
    ) -> "Graph.ResumeAction[GraphValueT]":
        return ResumeInterruptedNodeRequest(
            _canonical_scope(scope),
            GraphNodeId(canonical_port_name(node_id, kind="resume node")),
            GraphInterruptId(canonical_port_name(interrupt_id, kind="interrupt")),
            OverrideNodeInput(_require_graph_values(values)),
        )

    def skip_failed(
        self,
        node_id: str,
        reason: str,
        *,
        route: str | None = None,
        scope: tuple[str, ...] = (),
    ) -> "Graph.ResumeAction[GraphValueT]":
        canonical_reason = canonical_port_name(reason, kind="skip reason")
        canonical_route = canonical_port_name(route, kind="skip route") if route is not None else None
        return SkipFailedNodeRequest(
            _canonical_scope(scope),
            GraphNodeId(canonical_port_name(node_id, kind="resume node")),
            canonical_reason,
            canonical_route,
        )

    def _definition(
        self,
        definitions: dict["Graph[GraphValueT]", GraphDefinition[GraphValueT]],
        visiting: set["Graph[GraphValueT]"],
    ) -> GraphDefinition[GraphValueT]:
        existing = definitions.get(self)
        if existing is not None:
            return existing
        if self in visiting:
            raise GraphValidationError("graph composition recursively contains itself")
        visiting.add(self)
        state = self._builder_state
        if state.outputs is None:
            raise GraphValidationError("graph requires exactly one set_outputs() declaration")
        nodes: list[CallableNodeDefinition[GraphValueT] | NestedGraphNodeDefinition[GraphValueT]] = []
        for candidate in state.nodes:
            if isinstance(candidate, CallableNodeDefinition):
                nodes.append(candidate)
            else:
                child = candidate.graph._definition(definitions, visiting)
                nodes.append(NestedGraphNodeDefinition(candidate.node_id, child, candidate.inputs))
        definition = GraphDefinition(
            self._definition_id,
            self._version,
            tuple(nodes),
            state.edges,
            state.entries,
            state.outputs,
            state.resources,
            state.resume_input,
        )
        definitions[self] = definition
        visiting.remove(self)
        return definition

    def _compile(self) -> _CompiledOwner[GraphValueT]:
        existing = self._compiled_owner
        if existing is not None:
            return existing
        definitions: dict[Graph[GraphValueT], GraphDefinition[GraphValueT]] = {}
        self._definition(definitions, set())
        compiled: dict[Graph[GraphValueT], CompiledGraph[GraphValueT]] = {
            owner: compile_graph(definition) for owner, definition in definitions.items()
        }
        installations = {
            owner: _CompiledOwner(graph, _new_family_identity())
            for owner, graph in compiled.items()
            if owner._compiled_owner is None
        }
        for owner, installation in installations.items():
            owner._compiled_owner = installation
        return installations[self]

    @overload
    async def run(
        self,
        values: "Graph.Values[GraphValueT]",
        /,
        *,
        run_id: str | None = None,
        commit: "Graph.Commit[GraphValueT] | None" = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> "Graph.Result[GraphValueT]": ...

    @overload
    async def run(
        self,
        /,
        *,
        state: "Graph.State",
        continuation: "Graph.Continuation[GraphValueT]",
        resume: tuple["Graph.ResumeAction[GraphValueT]", ...] = (),
        commit: "Graph.Commit[GraphValueT] | None" = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> "Graph.Result[GraphValueT]": ...

    @overload
    async def run(
        self,
        /,
        *,
        state: "Graph.State",
        resume: tuple["Graph.ResumeAction[GraphValueT]", ...] = (),
        commit: "Graph.Commit[GraphValueT] | None" = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> "Graph.Result[GraphValueT]": ...

    async def run(
        self,
        values: "Graph.Values[GraphValueT] | _MissingRunValues" = _MISSING_RUN_VALUES,
        /,
        *,
        run_id: str | None = None,
        state: "Graph.State | None" = None,
        continuation: "Graph.Continuation[GraphValueT] | None" = None,
        resume: tuple["Graph.ResumeAction[GraphValueT]", ...] = (),
        commit: "Graph.Commit[GraphValueT] | None" = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> "Graph.Result[GraphValueT]":
        limits = ExecutionLimits(max_supersteps, max_parallel_tasks)
        invocation: _GraphValues[GraphValueT] | GraphRunState
        if isinstance(values, _GraphValues):
            if state is not None or continuation is not None or resume:
                raise SnapshotMismatchError("new graph run cannot carry state, continuation, or resume actions")
            invocation = _require_graph_values(values)
        elif values is _MISSING_RUN_VALUES and state is not None and run_id is None:
            invocation = state
        else:
            raise SnapshotMismatchError("state runs require state, forbid run_id, and do not accept values")
        owner = self._compile()
        graph = owner.graph
        executors = _executors(graph)
        if isinstance(invocation, _GraphValues):
            effective_run_id = GraphRunId(str(uuid4()) if run_id is None else canonical_port_name(run_id, kind="run"))
            scope_run = root_scope_run(effective_run_id)
            input_candidate = admit_graph_input(graph, invocation)
            command = executors[()].start_command(effective_run_id)
            current = await _commit_transition(scope_run, None, command, None, commit)
            context: GraphRunContext[GraphValueT] = _new_context(
                owner.family_identity,
                current,
                ScopedFrameIndex(),
                recovered=False,
            )
            coordinate: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
                scope_run,
                graph.graph_input_descriptor.identity,
            )
            context.frames = context.frames.add_graph_input(AdmittedGraphInput(coordinate, input_candidate))
        else:
            if continuation is None:
                context = _new_context(
                    owner.family_identity,
                    invocation,
                    ScopedFrameIndex(),
                    recovered=True,
                )
            else:
                context = _context_from_continuation(owner.family_identity, invocation, continuation)
            _validate_context(graph, context)
            lineage = _lineage_states(context)
            planned_states, fences = _plan_fences(graph, lineage, executors)
            planned_states, candidate_frames, planned_resumes, facts = _plan_resumes(
                graph,
                planned_states,
                context.frames,
                resume,
                executors,
            )
            _admit_state_owned_overrides(graph, planned_states, candidate_frames)
            if context.recovered:
                preflight_recovery(
                    graph,
                    _recovery_seed(planned_states, candidate_frames, limits, facts),
                )
            for fence in fences:
                current = context.state_at(fence.scope_run)
                confirmed = await _commit_transition(
                    fence.scope_run,
                    current,
                    fence.command,
                    None,
                    commit,
                )
                context.replace_state(fence.scope_run, confirmed)
            for planned_resume in planned_resumes:
                current = context.state_at(planned_resume.scope_run)
                confirmed = await _commit_transition(
                    planned_resume.scope_run,
                    current,
                    planned_resume.prepared.command,
                    None,
                    commit,
                )
                context.replace_state(planned_resume.scope_run, confirmed)
                for admitted in planned_resume.prepared.inputs:
                    context.frames = context.frames.add_resume_input(admitted)
        disposition = await _drive_root(
            graph,
            context,
            executors,
            limits,
            commit,
        )
        return project_graph_result(graph, context, disposition)


__all__ = ["Graph"]
