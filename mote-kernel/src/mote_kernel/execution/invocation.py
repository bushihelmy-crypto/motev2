"""Recovery invocation validation and fence-resume planning."""

from bisect import bisect_left
from dataclasses import dataclass, replace
from itertools import groupby, pairwise
from typing import Generic, TypeVar

from mote_kernel.config import Config, ConfigContractError, require_config
from mote_kernel.execution.engine.recovery import (
    AdmittedResumeFact,
    RecoveryInvocationSeed,
)
from mote_kernel.execution.engine.resume_admission import (
    prepare_resume,
)
from mote_kernel.execution.engine.resume_input import (
    materialize_node_input,
    pending_node_input_available,
)
from mote_kernel.execution.engine.routing import graph_outputs_available
from mote_kernel.execution.engine.snapshot_guard import require_scoped_snapshot_matches_graph
from mote_kernel.execution.errors import (
    FrameInstallationInvariantError,
    GraphValueAdmissionError,
    GraphValuePublicationError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.topology import CompiledGraph, _compiled_graph_at_scope
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
    stable_activation,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import (
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
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
    ScopedRunEvidence,
    ScopedStateBinding,
    UncreatedGraphRun,
    require_publication_confirmation,
)
from mote_kernel.state.graph_state import (
    FenceGraphExecution,
    GraphActivationIdentity,
    GraphNodeId,
    GraphRunState,
    GraphRunStatus,
    OverrideGraphNodeInput,
    PendingGraphNode,
    frontier_node,
    pending_node_ids,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")
ValueT = TypeVar("ValueT")


@dataclass(frozen=True, slots=True)
class _PlannedLineage:
    """Immutable lookup index over the canonical planned-state bindings."""

    bindings: tuple[ScopedStateBinding, ...]
    uncreated: tuple[UncreatedGraphRun, ...] = ()

    def _position(self, coordinate: ScopeRunCoordinate) -> int:
        position = bisect_left(self.bindings, coordinate, key=lambda binding: binding.scope_run)
        if position == len(self.bindings) or self.bindings[position].scope_run != coordinate:
            raise SnapshotMismatchError(f"lineage does not contain one state at {coordinate!r}")
        return position

    def binding_at(self, coordinate: ScopeRunCoordinate) -> ScopedStateBinding:
        return self.bindings[self._position(coordinate)]

    def replace(self, replacement: ScopedStateBinding) -> "_PlannedLineage":
        position = self._position(replacement.scope_run)
        return _PlannedLineage(
            (*self.bindings[:position], replacement, *self.bindings[position + 1 :]),
            self.uncreated,
        )


@dataclass(frozen=True, slots=True)
class PlannedFence:
    scope_run: ScopeRunCoordinate
    command: FenceGraphExecution


@dataclass(frozen=True, slots=True)
class PlannedResume(Generic[GraphValueT]):
    scope_run: ScopeRunCoordinate
    successor: GraphRunState
    prepared: PreparedResume[GraphValueT]


def project_resume_frames(
    frames: ScopedFrameIndex[GraphValueT],
    planned: PlannedResume[GraphValueT],
) -> ScopedFrameIndex[GraphValueT]:
    installed = frames
    try:
        for admitted in planned.prepared.inputs:
            installed = installed.add_resume_input(admitted)
    except GraphValuePublicationError as error:
        raise FrameInstallationInvariantError("admitted resume frames failed owner-local projection") from error
    return installed


def is_current_child_activation(
    parent_state: GraphRunState,
    activation: StableActivation,
) -> bool:
    return (
        parent_state.status is GraphRunStatus.RUNNING
        and activation.superstep == parent_state.superstep
        and activation.node_id in pending_node_ids(parent_state.frontier)
    )


def lineage_states(
    root_state: GraphRunState,
    child_runs: tuple[ScopedRunEvidence, ...],
) -> _PlannedLineage:
    if type(child_runs) is not tuple or any(
        type(binding) not in (ScopedStateBinding, UncreatedGraphRun)
        or type(binding.scope_run) is not ScopeRunCoordinate
        for binding in child_runs
    ):
        raise SnapshotMismatchError("child run evidence requires typed immutable records")
    if child_runs != tuple(sorted(child_runs, key=lambda binding: binding.scope_run)):
        raise SnapshotMismatchError("continuation child bindings are not in canonical scoped order")
    child_coordinates = tuple(binding.scope_run for binding in child_runs)
    if len(child_coordinates) != len(set(child_coordinates)):
        raise SnapshotMismatchError("lineage repeats one scoped graph run")
    # A valid parent activation deterministically owns exactly one child
    # coordinate, so coordinate uniqueness also proves activation uniqueness.
    for binding in child_runs:
        replace(binding.scope_run)
        if not binding.scope_run.scope:
            raise SnapshotMismatchError("child state binding requires a nested scope")
        if isinstance(binding, ScopedStateBinding):
            _ = binding.parent_activation
    values = (
        ScopedStateBinding(root_scope_run(root_state.run_id), root_state),
        *(binding for binding in child_runs if isinstance(binding, ScopedStateBinding)),
    )
    canonical = tuple(sorted(values, key=lambda binding: binding.scope_run))
    return _PlannedLineage(
        canonical, tuple(binding for binding in child_runs if isinstance(binding, UncreatedGraphRun))
    )


def plan_fences(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
) -> tuple[_PlannedLineage, tuple[PlannedFence, ...]]:
    planned = lineage
    fences: list[PlannedFence] = []
    for binding in lineage.bindings:
        scoped_graph = _compiled_graph_at_scope(graph, binding.scope_run.scope)
        require_scoped_snapshot_matches_graph(scoped_graph, binding.state, binding.scope_run)
        activation = binding.parent_activation
        if activation is not None:
            parent_state = lineage.binding_at(activation.scope_run).state
            if activation.superstep > parent_state.superstep:
                raise SnapshotMismatchError("child lineage binding is from a future parent frontier")
            if binding.state.status is GraphRunStatus.RUNNING and not is_current_child_activation(
                parent_state,
                activation,
            ):
                raise SnapshotMismatchError("running child lineage is not one current parent activation")
        execution = binding.state.execution
        if execution is None:
            continue
        command = FenceGraphExecution(binding.state.revision, execution.token)
        candidate = reduce_graph_run(binding.state, command)
        fences.append(PlannedFence(binding.scope_run, command))
        planned = planned.replace(
            replace(binding, state=candidate),
        )
    return planned, tuple(fences)


def _resolve_scope_run(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    scope: tuple[GraphNodeId, ...],
) -> ScopeRunCoordinate:
    root = lineage.bindings[0]
    coordinate = root.scope_run
    scoped_graph = graph
    for segment in scope:
        state = lineage.binding_at(coordinate).state
        nested = scoped_graph.nested_graphs.get(segment)
        node = frontier_node(state.frontier, segment)
        if nested is None or node is None or not isinstance(node.settlement, PendingGraphNode):
            raise SnapshotMismatchError(f"resume scope segment {segment!r} is not one current nested activation")
        parent = GraphActivationIdentity(state.run_id, state.superstep, segment)
        coordinate = child_scope_run_for_activation(coordinate, parent)
        lineage.binding_at(coordinate)
        scoped_graph = nested
    return coordinate


def plan_resumes(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    frames: ScopedFrameIndex[GraphValueT],
    resume: tuple[ResumeNodeRequest[GraphValueT], ...],
) -> tuple[
    _PlannedLineage,
    ScopedFrameIndex[GraphValueT],
    tuple[PlannedResume[GraphValueT], ...],
    tuple[AdmittedResumeFact, ...],
]:
    if not resume:
        return lineage, frames, (), ()
    if type(resume) is not tuple:
        raise SnapshotMismatchError("resume actions must be supplied as a tuple")
    canonical = tuple(sorted(resume, key=lambda action: (action.scope, action.node_id)))
    if canonical != resume:
        raise SnapshotMismatchError("resume actions must be supplied in canonical scope/node order")
    action_counts: dict[tuple[tuple[str, ...], GraphNodeId], int] = {}
    duplicate_coordinates: list[tuple[tuple[str, ...], GraphNodeId]] = []
    for action in canonical:
        coordinate = (action.scope, action.node_id)
        count = action_counts.get(coordinate, 0) + 1
        action_counts[coordinate] = count
        if count == 2:
            duplicate_coordinates.append(coordinate)
    if duplicate_coordinates:
        raise SnapshotMismatchError(f"resume action nodes {tuple(duplicate_coordinates)!r} are duplicated")
    planned_lineage = lineage
    candidate_frames = frames
    plans: list[PlannedResume[GraphValueT]] = []
    facts: list[AdmittedResumeFact] = []
    for scope, grouped_actions in groupby(canonical, key=lambda action: action.scope):
        actions = tuple(grouped_actions)
        scope_run = _resolve_scope_run(graph, planned_lineage, scope)
        binding = planned_lineage.binding_at(scope_run)
        scoped_graph = _compiled_graph_at_scope(graph, scope_run.scope)
        prepared = prepare_resume(scoped_graph, ResumeRequest(binding.state, scope_run, candidate_frames, actions))
        candidate = reduce_graph_run(binding.state, prepared.command)
        action_facts = tuple(
            AdmittedResumeFact(
                stable_activation(
                    scope_run,
                    GraphActivationIdentity(binding.state.run_id, binding.state.superstep, action.node_id),
                ),
                action.interrupt_id,
            )
            for action in actions
        )
        for admitted in prepared.inputs:
            candidate_frames = candidate_frames.add_resume_input(admitted)
        plans.append(PlannedResume(scope_run, candidate, prepared))
        facts.extend(action_facts)
        planned_lineage = planned_lineage.replace(
            replace(binding, state=candidate),
        )
    return planned_lineage, candidate_frames, tuple(plans), tuple(facts)


def recovery_seed(
    lineage: _PlannedLineage,
    frames: ScopedFrameIndex[GraphValueT],
    limits: ExecutionLimits,
    facts: tuple[AdmittedResumeFact, ...],
) -> RecoveryInvocationSeed[GraphValueT]:
    root = lineage.bindings[0]
    children: tuple[ScopedRunEvidence, ...] = tuple(
        sorted(
            (*(binding for binding in lineage.bindings if binding.scope_run.scope), *lineage.uncreated),
            key=lambda binding: binding.scope_run,
        )
    )
    return RecoveryInvocationSeed(
        root,
        children,
        frames,
        limits,
        facts,
    )


def admit_state_owned_overrides(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    frames: ScopedFrameIndex[GraphValueT],
) -> None:
    for binding in lineage.bindings:
        if binding.state.status is not GraphRunStatus.RUNNING:
            continue
        scoped_graph = _compiled_graph_at_scope(graph, binding.scope_run.scope)
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


def _validate_frame_config(state: GraphRunState, config: Config | None) -> None:
    if config is None:
        return
    try:
        state.config_cursor.admit_history(require_config(config).config_cursor)
    except (ConfigContractError, TypeError, ValueError) as error:
        raise SnapshotMismatchError("frame Config must be exact history of its authoritative state") from error


def _validate_graph_input_records(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    records: tuple[AdmittedGraphInput[GraphValueT], ...],
) -> None:
    for record in records:
        coordinate = record.coordinate
        binding = lineage.binding_at(coordinate.scope_run)
        scoped_graph = _compiled_graph_at_scope(graph, coordinate.scope_run.scope)
        if coordinate.descriptor != scoped_graph.graph_input_descriptor.identity:
            raise SnapshotMismatchError("continuation graph input descriptor does not match its scope")
        try:
            _admit_graph_input_frame(record.frame, scoped_graph.graph_input_descriptor.declarations)
        except GraphValueAdmissionError as error:
            raise SnapshotMismatchError("continuation graph input frame does not match its descriptor") from error
        _validate_frame_config(binding.state, record.frame.activation_config)


def _validate_publication_records(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    records: tuple[ConfirmedPublication[GraphValueT], ...],
) -> None:
    for record in records:
        coordinate = record.coordinate
        binding = lineage.binding_at(coordinate.activation.scope_run)
        scoped_graph = _compiled_graph_at_scope(graph, coordinate.activation.scope_run.scope)
        publication = scoped_graph.transition.publications.get(coordinate.activation.node_id)
        if (
            publication is None
            or coordinate.descriptor != publication.identity
            or coordinate.activation.superstep > binding.state.superstep
        ):
            raise SnapshotMismatchError("continuation publication has inconsistent coordinates")
        token = require_publication_confirmation(record.acknowledged_revision, record.provenance)
        if record.acknowledged_revision > binding.state.revision:
            raise SnapshotMismatchError("continuation publication has inconsistent coordinates")
        if token.generation > binding.state.execution_sequence:
            raise SnapshotMismatchError("continuation publication has inconsistent execution provenance")
        try:
            _admit_node_output_frame(record.frame, publication.declarations)
        except GraphValueAdmissionError as error:
            raise SnapshotMismatchError("continuation publication frame does not match its descriptor") from error
        _validate_frame_config(binding.state, record.frame.activation_config)


def _validate_resume_input_records(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    records: tuple[AdmittedResumeInput[GraphValueT], ...],
) -> None:
    for record in records:
        coordinate = record.coordinate
        binding = lineage.binding_at(coordinate.activation.scope_run)
        scoped_graph = _compiled_graph_at_scope(graph, coordinate.activation.scope_run.scope)
        materialization = scoped_graph.transition.materializations.get(coordinate.activation.node_id)
        if (
            materialization is None
            or coordinate.descriptor != materialization.descriptor.identity
            or coordinate.activation.superstep > binding.state.superstep
        ):
            raise SnapshotMismatchError("continuation resume input has inconsistent coordinates")
        try:
            _admit_node_input_frame(record.frame, materialization.descriptor.declarations)
        except GraphValueAdmissionError as error:
            raise SnapshotMismatchError("continuation resume input frame does not match its descriptor") from error


def _validate_child_boundary_records(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    records: tuple[ConfirmedChildBoundary[GraphValueT], ...],
) -> None:
    for record in records:
        coordinate = record.coordinate
        binding = lineage.binding_at(coordinate.child_scope_run)
        scoped_graph = _compiled_graph_at_scope(graph, coordinate.child_scope_run.scope)
        if (
            coordinate.descriptor != scoped_graph.graph_output_descriptor.identity
            or binding.state.status is not GraphRunStatus.COMPLETED
        ):
            raise SnapshotMismatchError("continuation child boundary has inconsistent coordinates")
        try:
            _admit_graph_output_view(record.frame, scoped_graph.graph_output_descriptor.declarations)
        except GraphValueAdmissionError as error:
            raise SnapshotMismatchError("continuation child boundary frame does not match its descriptor") from error


def _validate_frame_index(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    frames: ScopedFrameIndex[GraphValueT],
) -> None:
    if any(
        type(record) is not AdmittedGraphInput or type(record.coordinate) is not GraphInputAvailabilityCoordinate
        for record in frames.graph_inputs
    ):
        raise SnapshotMismatchError("continuation graph input segment contains a malformed record")
    if any(
        type(record) is not ConfirmedPublication or type(record.coordinate) is not PublicationAvailabilityCoordinate
        for record in frames.publications
    ):
        raise SnapshotMismatchError("continuation publication segment contains a malformed record")
    if any(
        type(record) is not AdmittedResumeInput or type(record.coordinate) is not ResumeInputAvailabilityCoordinate
        for record in frames.resume_inputs
    ):
        raise SnapshotMismatchError("continuation resume input segment contains a malformed record")
    if any(
        type(record) is not ConfirmedChildBoundary or type(record.coordinate) is not ChildBoundaryAvailabilityCoordinate
        for record in frames.child_boundaries
    ):
        raise SnapshotMismatchError("continuation child boundary segment contains a malformed record")
    if any(previous.coordinate >= current.coordinate for previous, current in pairwise(frames.graph_inputs)):
        raise SnapshotMismatchError("continuation graph input coordinates are not unique and canonical")
    if any(previous.coordinate >= current.coordinate for previous, current in pairwise(frames.publications)):
        raise SnapshotMismatchError("continuation publication coordinates are not unique and canonical")
    if any(previous.coordinate >= current.coordinate for previous, current in pairwise(frames.resume_inputs)):
        raise SnapshotMismatchError("continuation resume input coordinates are not unique and canonical")
    if any(previous.coordinate >= current.coordinate for previous, current in pairwise(frames.child_boundaries)):
        raise SnapshotMismatchError("continuation child boundary coordinates are not unique and canonical")
    _validate_graph_input_records(graph, lineage, frames.graph_inputs)
    _validate_publication_records(graph, lineage, frames.publications)
    _validate_resume_input_records(graph, lineage, frames.resume_inputs)
    _validate_child_boundary_records(graph, lineage, frames.child_boundaries)


def _validate_child_run_evidence(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    *,
    complete: bool,
) -> None:
    required: set[ScopeRunCoordinate] = set()
    uncreated_allowed: set[ScopeRunCoordinate] = set()
    for binding in lineage.bindings:
        scoped_graph = _compiled_graph_at_scope(graph, binding.scope_run.scope)
        state = binding.state
        for reference in state.settled_activations:
            if reference.activation.node_id in scoped_graph.nested_graphs:
                required.add(child_scope_run_for_activation(binding.scope_run, reference.activation))
        for node in state.frontier.nodes:
            if node.node_id not in scoped_graph.nested_graphs:
                continue
            parent = GraphActivationIdentity(state.run_id, state.superstep, node.node_id)
            coordinate = child_scope_run_for_activation(binding.scope_run, parent)
            required.add(coordinate)
            if isinstance(node.settlement, PendingGraphNode):
                uncreated_allowed.add(coordinate)
    existing = {binding.scope_run for binding in lineage.bindings if binding.scope_run.scope}
    uncreated = {record.scope_run for record in lineage.uncreated}
    if not uncreated <= uncreated_allowed or existing & uncreated or not existing <= required:
        raise SnapshotMismatchError("child run evidence does not match an authoritative parent activation")
    if complete and existing | uncreated != required:
        raise SnapshotMismatchError("complete evidence requires each child snapshot or explicit uncreated proof")


def _validate_complete_context(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    frames: ScopedFrameIndex[GraphValueT],
) -> None:
    admitted_inputs = frozenset(record.coordinate.scope_run for record in frames.graph_inputs)
    if admitted_inputs != frozenset(binding.scope_run for binding in lineage.bindings):
        raise SnapshotMismatchError("complete continuation must retain every scoped graph input")
    expected_publications = {
        stable_activation(binding.scope_run, reference.activation)
        for binding in lineage.bindings
        for reference in binding.state.settled_activations
    }
    if expected_publications != {record.coordinate.activation for record in frames.publications}:
        raise SnapshotMismatchError("complete publications must exactly match the committed settlement ledger")
    for binding in lineage.bindings:
        scoped_graph = _compiled_graph_at_scope(graph, binding.scope_run.scope)
        state = binding.state
        for node in state.frontier.nodes:
            if (
                isinstance(node.settlement, PendingGraphNode)
                and node.node_id not in scoped_graph.nested_graphs
                and not pending_node_input_available(
                    scoped_graph,
                    state,
                    binding.scope_run,
                    frames,
                    node.node_id,
                )
            ):
                raise SnapshotMismatchError("complete continuation is missing a current node input source")
        if state.status is GraphRunStatus.COMPLETED and not graph_outputs_available(
            scoped_graph,
            binding.scope_run,
            state.superstep,
            frames,
        ):
            raise SnapshotMismatchError("complete continuation is missing a completed graph output")
        if binding.scope_run.scope and state.status is GraphRunStatus.COMPLETED:
            boundary: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
                binding.scope_run,
                scoped_graph.graph_output_descriptor.identity,
            )
            if not frames.has_child_boundary(boundary):
                raise SnapshotMismatchError("complete continuation is missing a completed child boundary")


def validate_context(
    graph: CompiledGraph[GraphValueT],
    lineage: _PlannedLineage,
    frames: ScopedFrameIndex[GraphValueT],
    *,
    recovered: bool,
) -> None:
    _validate_child_run_evidence(graph, lineage, complete=not recovered)
    _validate_frame_index(graph, lineage, frames)
    if not recovered:
        _validate_complete_context(graph, lineage, frames)


__all__: list[str] = []
