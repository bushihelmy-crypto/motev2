"""Recovery invocation validation and fence-resume planning."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.recovery import (
    AdmittedActionKind,
    AdmittedResumeFact,
    RecoveryInvocationSeed,
    RecoveryStateBinding,
)
from mote_kernel.execution.engine.resume_input import (
    materialize_node_input,
    pending_node_input_available,
)
from mote_kernel.execution.engine.routing import graph_outputs_available
from mote_kernel.execution.errors import (
    GraphValueAdmissionError,
    SnapshotMismatchError,
)
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    _admit_graph_input_frame,
    _admit_graph_output_view,
    _admit_node_input_frame,
    _admit_node_output_frame,
)
from mote_kernel.execution.identity import (
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
)
from mote_kernel.execution.result import (
    PreparedResume,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedResumeInput,
    ChildBoundaryAvailabilityCoordinate,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    GraphInputAvailabilityCoordinate,
    GraphRunContext,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    FenceGraphExecution,
    GraphNodeId,
    GraphRouteId,
    GraphRunState,
    GraphRunStatus,
    OverrideGraphNodeInput,
    ParentGraphActivation,
    PendingGraphNode,
    SucceededGraphNode,
    frontier_node,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")
ValueT = TypeVar("ValueT")


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


def executors_for(root: CompiledGraph[GraphValueT]) -> dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]]:
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


def lineage_states(context: GraphRunContext[GraphValueT]) -> tuple[_PlannedState, ...]:
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


def plan_fences(
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


def plan_resumes(
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


def recovery_seed(
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


def admit_state_owned_overrides(
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
    bindings = lineage_states(context)
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
    states = lineage_states(context)
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


def validate_context(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
) -> None:
    _validate_frame_index(graph, context)
    if not context.recovered:
        _validate_complete_context(graph, context)


__all__: list[str] = []
