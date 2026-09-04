from collections.abc import Callable
from dataclasses import replace
from typing import Never, Protocol, cast

import pytest
from tests.execution.engine.factories import join_progress

import mote_kernel.execution.engine.recovery as recovery_module
from mote_kernel.execution import Graph
from mote_kernel.execution.engine.recovery import (
    AdmittedResumeFact,
    ChildControlStateCoordinate,
    ChildRecoveryDisposition,
    RecoveryAvailabilityCoordinates,
    RecoveryInvocationSeed,
    RecoverySettlementKind,
    RecoveryStateBinding,
    RecoveryTransferState,
    ScopeControlStateCoordinate,
    preflight_recovery,
    recovery_traversal_key,
)
from mote_kernel.execution.engine.routing import PublicationHistoryWindow, resolve_routing
from mote_kernel.execution.engine.settlement import require_settlement_execution_token
from mote_kernel.execution.errors import GraphValueUnavailableError, SnapshotMismatchError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    FrameDescriptorIdentity,
    FrameKind,
    GraphOutputDeclarations,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    NodeInputFrame,
    _make_graph_input_frame,
    _make_node_input_frame,
    _make_node_output_frame,
)
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import (
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedResumeInput,
    ChildBoundaryAvailabilityCoordinate,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ActivationReference,
    ClaimGraphExecution,
    ContinueGraphRouting,
    FailedGraphNode,
    FailedGraphNodeOutcome,
    FenceGraphExecution,
    GraphAbortReason,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptId,
    GraphInterruptPayload,
    GraphNodeId,
    GraphNodeInterruptIdentity,
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNodeOutcome,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    RoutedActivationCause,
    SelectGraphRoute,
    SettleGraphNode,
    StartActivationCause,
    SucceededGraphNodeOutcome,
    graph_interrupt_id,
    reduce_graph_run,
)


class _RecoveryWorkItemView(Protocol):
    state: GraphRunState
    availability: RecoveryAvailabilityCoordinates[str]
    live: tuple[GraphNodeId, ...]
    children: tuple[ChildRecoveryDisposition, ...]
    invocation_new_children: tuple[GraphNodeId, ...]


class _RecoveryBoundaryView(Protocol):
    kind: object
    availability: RecoveryAvailabilityCoordinates[str]
    control: ScopeControlStateCoordinate
    state: GraphRunState


class _NestedOutcomeView(Protocol):
    node_id: GraphNodeId
    boundary: _RecoveryBoundaryView


class _NestedCombinationView(Protocol):
    outcomes: tuple[_NestedOutcomeView, ...]
    availability: RecoveryAvailabilityCoordinates[str]


class _RecoveryFamilyView(Protocol):
    bindings: tuple[RecoveryStateBinding, ...]
    limits: ExecutionLimits
    admitted_actions: tuple[AdmittedResumeFact, ...]


class _BoundaryKindView(Protocol):
    EXECUTION_LIMIT: object


class _RecoveryPrivateView(Protocol):
    _boundary: Callable[..., _RecoveryBoundaryView]
    _NestedCombination: Callable[..., _NestedCombinationView]
    _NestedOutcome: Callable[..., _NestedOutcomeView]
    _prove_scope: Callable[..., tuple[_RecoveryBoundaryView, ...]]
    _recovery_cycle_signature: Callable[..., object | None]
    _RecoveryFamily: Callable[..., _RecoveryFamilyView]
    _RecoveryProofBudget: Callable[..., object]
    _RecoveryWorkItem: Callable[..., _RecoveryWorkItemView]
    _ScopeBoundaryKind: _BoundaryKindView
    _settle_nested_outcomes: Callable[..., tuple[GraphRunState, RecoveryAvailabilityCoordinates[str]]]

    @staticmethod
    def boundary(
        module: object,
        kind: object,
        state: GraphRunState,
        scope_run: ScopeRunCoordinate,
        availability: RecoveryAvailabilityCoordinates[str],
    ) -> _RecoveryBoundaryView:
        return cast(_RecoveryPrivateView, module)._boundary(kind, state, scope_run, availability)

    @staticmethod
    def combination(module: object, *args: object) -> _NestedCombinationView:
        return cast(_RecoveryPrivateView, module)._NestedCombination(*args)

    @staticmethod
    def outcome(module: object, *args: object) -> _NestedOutcomeView:
        return cast(_RecoveryPrivateView, module)._NestedOutcome(*args)

    @staticmethod
    def prove_scope(
        module: object,
        graph: CompiledGraph[str],
        state: GraphRunState,
        scope_run: ScopeRunCoordinate,
        availability: RecoveryAvailabilityCoordinates[str],
        family: _RecoveryFamilyView,
    ) -> tuple[_RecoveryBoundaryView, ...]:
        return cast(_RecoveryPrivateView, module)._prove_scope(graph, state, scope_run, availability, family)

    @staticmethod
    def cycle_signature(
        module: object,
        graph: CompiledGraph[str],
        item: _RecoveryWorkItemView,
        scope_run: ScopeRunCoordinate,
        window: PublicationHistoryWindow,
    ) -> object | None:
        return cast(_RecoveryPrivateView, module)._recovery_cycle_signature(graph, item, scope_run, window)

    @staticmethod
    def family(module: object, *args: object) -> _RecoveryFamilyView:
        return cast(_RecoveryPrivateView, module)._RecoveryFamily(*args)

    @staticmethod
    def proof_budget(module: object, *args: object) -> object:
        return cast(_RecoveryPrivateView, module)._RecoveryProofBudget(*args)

    @staticmethod
    def work_item(module: object, *args: object) -> _RecoveryWorkItemView:
        return cast(_RecoveryPrivateView, module)._RecoveryWorkItem(*args)

    @staticmethod
    def boundary_kind(module: object) -> _BoundaryKindView:
        return cast(_RecoveryPrivateView, module)._ScopeBoundaryKind

    @staticmethod
    def settle_nested_outcomes(
        module: object,
        graph: CompiledGraph[str],
        state: GraphRunState,
        scope_run: ScopeRunCoordinate,
        combination: _NestedCombinationView,
    ) -> tuple[GraphRunState, RecoveryAvailabilityCoordinates[str]]:
        return cast(_RecoveryPrivateView, module)._settle_nested_outcomes(graph, state, scope_run, combination)


class _CompiledOwnerView(Protocol):
    graph: CompiledGraph[str]


class _GraphPrivateView(Protocol):
    def _compile(self) -> _CompiledOwnerView: ...

    @staticmethod
    def compile(graph: object) -> _CompiledOwnerView:
        return cast(_GraphPrivateView, graph)._compile()


_EMPTY_RECOVERY_AVAILABILITY = RecoveryAvailabilityCoordinates[str]()
_EMPTY_PUBLICATION_HISTORY_WINDOW = PublicationHistoryWindow((), 0)


async def empty_node(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values()


class EmptyResumeCodec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        del value
        return b""

    def decode(self, payload: bytes) -> Graph.Values[str]:
        del payload
        return Graph.values()


def empty_graph() -> CompiledGraph[str]:
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.identity"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    GraphNodeId("node"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
        )
    )


def interruptible_graph() -> CompiledGraph[str]:
    codec = EmptyResumeCodec()
    resume_input: ResumeInputBinding[str] = ResumeInputBinding(
        GraphResumeInputCodecId("recovery.empty"),
        1,
        codec,
        codec,
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.interrupt-identity"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    GraphNodeId("node"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
            resume_input=resume_input,
        )
    )


def baseline_transfer() -> RecoveryTransferState[str]:
    graph = empty_graph()
    command = project_start_graph_command(graph, GraphRunId("root-run"))
    state = reduce_graph_run(None, command)
    scope_run = root_scope_run(state.run_id)
    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(scope_run, state),
            (),
            ScopedFrameIndex(),
            ExecutionLimits(4, 2),
        ),
    )
    assert boundaries
    return boundaries[0]


def _loop_position(state: GraphRunState, superstep: int, predecessor_superstep: int) -> GraphRunState:
    node = state.frontier.nodes[0]
    return replace(
        state,
        superstep=superstep,
        frontier=GraphFrontierState(
            (
                replace(
                    node,
                    cause=RoutedActivationCause(
                        (
                            ActivationReference(
                                GraphActivationIdentity(state.run_id, predecessor_superstep, node.node_id)
                            ),
                        )
                    ),
                ),
            )
        ),
    )


def _cycle_signature(
    graph: CompiledGraph[str],
    state: GraphRunState,
    availability: RecoveryAvailabilityCoordinates[str] = _EMPTY_RECOVERY_AVAILABILITY,
    window: PublicationHistoryWindow = _EMPTY_PUBLICATION_HISTORY_WINDOW,
) -> object:
    signature = _RecoveryPrivateView.cycle_signature(
        recovery_module,
        graph,
        _RecoveryPrivateView.work_item(recovery_module, state, availability),
        root_scope_run(state.run_id),
        window,
    )
    assert signature is not None
    return signature


def test_recovery_cycle_signature_normalizes_activation_and_relative_publication_distance() -> None:
    graph = empty_graph()
    initial = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("cycle-signature")))
    first = _loop_position(initial, 1, 0)
    second = _loop_position(initial, 2, 1)
    scope_run = root_scope_run(initial.run_id)
    descriptor = graph.transition.publications[GraphNodeId("node")].identity
    first_availability = RecoveryAvailabilityCoordinates[str](
        publications=(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, 0, GraphNodeId("node")),
                descriptor,
            ),
        )
    )
    second_availability = RecoveryAvailabilityCoordinates[str](
        publications=(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, 1, GraphNodeId("node")),
                descriptor,
            ),
        )
    )
    window = PublicationHistoryWindow((), 1)

    assert _cycle_signature(graph, first, first_availability, window) == _cycle_signature(
        graph, second, second_availability, window
    )
    assert _cycle_signature(graph, _loop_position(initial, 2, 0), second_availability, window) != _cycle_signature(
        graph, second, second_availability, window
    )


def test_recovery_cycle_signature_keeps_each_successor_relevant_availability_fact() -> None:
    graph = empty_graph()
    initial = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("cycle-availability")))
    state = _loop_position(initial, 1, 0)
    scope_run = root_scope_run(state.run_id)
    node_id = GraphNodeId("node")
    output_descriptor = graph.transition.publications[node_id].identity
    input_descriptor = graph.transition.materializations[node_id].descriptor.identity

    absolute_window = PublicationHistoryWindow((0,), 0)
    absolute_baseline = _cycle_signature(graph, state, window=absolute_window)
    publication = RecoveryAvailabilityCoordinates[str](
        publications=(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, 0, node_id),
                output_descriptor,
            ),
        )
    )
    assert _cycle_signature(graph, state, publication, absolute_window) != absolute_baseline

    relative_window = PublicationHistoryWindow((), 1)
    relative_baseline = _cycle_signature(graph, state, window=relative_window)
    assert _cycle_signature(graph, state, publication, relative_window) != relative_baseline

    resume = RecoveryAvailabilityCoordinates[str](
        resume_inputs=(
            ResumeInputAvailabilityCoordinate(
                StableActivation(scope_run, state.superstep, node_id),
                input_descriptor,
            ),
        )
    )
    assert _cycle_signature(graph, state, resume) != _cycle_signature(graph, state)

    progress = join_progress(
        (node_id, "other"),
        "joined",
        (ActivationReference(GraphActivationIdentity(state.run_id, 0, node_id)),),
        target_superstep=state.superstep + 1,
        run_id=state.run_id,
    )
    assert _cycle_signature(graph, replace(state, join_progress=(progress,))) != _cycle_signature(graph, state)


def test_recovery_cycle_signature_never_merges_an_active_resource_state() -> None:
    graph = empty_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("cycle-resource")))
    node_id = GraphNodeId("node")
    resource_id = ResourceId("file")
    snapshot = ResourceSnapshot(
        (ResourceLock(resource_id, node_id),),
        (ResourceAcquisition(node_id, (resource_id,), (resource_id,)),),
    )
    claimed = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("resource-claim"), snapshot),
    )

    assert (
        _RecoveryPrivateView.cycle_signature(
            recovery_module,
            graph,
            _RecoveryPrivateView.work_item(recovery_module, claimed, RecoveryAvailabilityCoordinates()),
            root_scope_run(claimed.run_id),
            PublicationHistoryWindow((), 0),
        )
        is None
    )


def test_recovery_preflight_rejects_a_running_state_with_a_terminal_failed_frontier() -> None:
    graph = empty_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("invalid-failed-run")))
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("node"),
                    FailedGraphNode(GraphFailure("failed")),
                    StartActivationCause(),
                ),
            )
        ),
    )
    scope_run = root_scope_run(state.run_id)

    with pytest.raises(SnapshotMismatchError, match="terminal failed frontier"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(scope_run, state),
                (),
                ScopedFrameIndex(),
                ExecutionLimits(2, 1),
            ),
        )


def test_recovery_control_identity_preserves_an_interrupted_settlement() -> None:
    graph = interruptible_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("interrupted-run")))
    claimed = reduce_graph_run(
        state,
        ClaimGraphExecution(
            state.revision,
            GraphExecutionAttemptId("interrupt-claim"),
            None,
        ),
    )
    execution = claimed.execution
    assert execution is not None
    identity = GraphNodeInterruptIdentity(
        claimed.run_id,
        claimed.superstep,
        GraphNodeId("node"),
        execution.token.generation,
    )
    interrupted = reduce_graph_run(
        claimed,
        SettleGraphNode(
            claimed.revision,
            execution.token,
            InterruptedGraphNodeOutcome(
                GraphNodeId("node"),
                identity,
                GraphInterruptPayload(b"question"),
            ),
        ),
    )

    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope_run(interrupted.run_id), interrupted),
            (),
            ScopedFrameIndex(),
            ExecutionLimits(2, 1),
        ),
    )

    assert boundaries[0].control.frontier[0].interrupt_id == graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )


def test_recovery_identity_keeps_every_availability_and_admitted_action_fact() -> None:
    baseline = baseline_transfer()
    root = baseline.control.scope_run
    child = ScopeRunCoordinate((GraphNodeId("child"),), GraphRunId("child-run"))
    activation = StableActivation(root, 2, GraphNodeId("node"))
    graph_input: GraphInputAvailabilityCoordinate[str] = GraphInputAvailabilityCoordinate(
        root,
        FrameDescriptorIdentity("graph", 1, FrameKind.GRAPH_INPUT, 0),
    )
    publication: PublicationAvailabilityCoordinate[str] = PublicationAvailabilityCoordinate(
        activation,
        FrameDescriptorIdentity("graph", 1, FrameKind.NODE_OUTPUT, 1),
    )
    resume_input: ResumeInputAvailabilityCoordinate[str] = ResumeInputAvailabilityCoordinate(
        activation,
        FrameDescriptorIdentity("graph", 1, FrameKind.NODE_INPUT, 1),
    )
    child_boundary: ChildBoundaryAvailabilityCoordinate[str] = ChildBoundaryAvailabilityCoordinate(
        child,
        FrameDescriptorIdentity("child", 1, FrameKind.GRAPH_OUTPUT, 0),
    )
    availability = RecoveryAvailabilityCoordinates[str]()
    availability = availability.with_graph_input(graph_input)
    availability = availability.with_publication(publication)
    availability = availability.with_child_boundary(child_boundary)
    availability = replace(availability, resume_inputs=(resume_input,))

    assert availability.with_graph_input(graph_input) is availability
    assert availability.with_publication(publication) is availability
    assert availability.with_child_boundary(child_boundary) is availability
    assert availability.has_graph_input(graph_input)
    assert availability.publications == (publication,)
    assert availability.has_publication(publication)
    assert availability.has_resume_input(resume_input)
    assert availability.has_child_boundary(child_boundary)

    parent = GraphActivationIdentity(root.graph_run_id, 2, GraphNodeId("child"))
    control = baseline.control
    child_control = ChildControlStateCoordinate(
        control.definition_id,
        control.definition_version,
        control.status,
        control.superstep,
        control.execution_sequence,
        control.frontier,
        control.join_progress,
        control.resources,
        control.execution,
        GraphResumeInputCodecId("codec"),
        1,
        parent,
        control.revision,
    )
    child_disposition = ChildRecoveryDisposition(
        child,
        child_control,
    )
    resumed = AdmittedResumeFact(
        activation,
        GraphInterruptId("interrupt"),
    )
    rich = replace(
        baseline,
        live=(GraphNodeId("node"),),
        availability=availability,
        children=(child_disposition,),
        admitted_actions=(resumed,),
    )

    assert rich.availability.has_graph_input(graph_input)
    assert rich.availability.publications == (publication,)
    assert rich.availability.has_publication(publication)
    assert rich.availability.has_resume_input(resume_input)
    assert rich.availability.has_child_boundary(child_boundary)
    assert recovery_traversal_key(rich).parts

    missing_child = replace(child_disposition, control=None)
    assert recovery_traversal_key(replace(rich, children=(missing_child,))).parts

    collision = replace(
        rich,
        control=replace(rich.control, definition_id=GraphDefinitionId("different-definition")),
    )
    assert collision != rich
    assert recovery_traversal_key(collision) == recovery_traversal_key(rich)
    assert len({rich, collision}) == 2

    different_sequence = replace(
        rich,
        control=replace(
            rich.control,
            execution_sequence=rich.control.execution_sequence + 1,
        ),
    )
    assert different_sequence != rich
    assert recovery_traversal_key(different_sequence) != recovery_traversal_key(rich)

    new_child = replace(rich, invocation_new_children=(GraphNodeId("child"),))
    assert new_child != rich
    assert recovery_traversal_key(new_child) != recovery_traversal_key(rich)


def test_recovery_valid_domain_equality_uses_availability_and_interrupt_identity() -> None:
    graph = empty_graph()
    baseline = baseline_transfer()
    activation = StableActivation(
        baseline.control.scope_run,
        baseline.control.superstep,
        GraphNodeId("node"),
    )
    exact: ResumeInputAvailabilityCoordinate[str] = ResumeInputAvailabilityCoordinate(
        activation,
        graph.transition.materializations[GraphNodeId("node")].descriptor.identity,
    )
    alternate: ResumeInputAvailabilityCoordinate[str] = ResumeInputAvailabilityCoordinate(
        activation,
        interruptible_graph().transition.materializations[GraphNodeId("node")].descriptor.identity,
    )
    assert alternate != exact
    action = AdmittedResumeFact(
        activation,
        GraphInterruptId("interrupt"),
    )
    exact_state = replace(
        baseline,
        availability=replace(baseline.availability, resume_inputs=(exact,)),
        admitted_actions=(action,),
    )
    alternate_state = replace(
        exact_state,
        availability=replace(exact_state.availability, resume_inputs=(alternate,)),
    )
    assert exact_state != alternate_state
    assert len({exact_state, alternate_state}) == 2

    different_action_state = replace(
        exact_state,
        admitted_actions=(replace(action, interrupt_id=GraphInterruptId("other-interrupt")),),
    )
    assert exact_state != different_action_state
    assert len({exact_state, different_action_state}) == 2


class HostileValue:
    def __hash__(self) -> Never:
        raise AssertionError("recovery must not hash concrete values")

    def __repr__(self) -> Never:
        raise AssertionError("recovery must not render concrete values")

    def __lt__(self, _other: "HostileValue") -> Never:
        raise AssertionError("recovery must not order concrete values")


async def consume_hostile(_values: Graph.Values[HostileValue]) -> Graph.Values[HostileValue]:
    return Graph.values()


def test_recovery_preflight_never_hashes_orders_or_renders_concrete_frame_values() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.hostile-value"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    GraphNodeId("node"),
                    consume_hostile,
                    normalize_input_bindings({"value": Graph.graph_input("value", HostileValue)}),
                    normalize_output_declarations({}),
                ),
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("hostile-run")))
    scope_run = root_scope_run(state.run_id)
    frame = _make_graph_input_frame(
        Graph.values(value=HostileValue()),
        normalize_output_declarations({"value": HostileValue}),
    )
    frames = ScopedFrameIndex(
        graph_inputs=(
            AdmittedGraphInput(
                GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
                frame,
            ),
        )
    )

    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(scope_run, state),
            (),
            frames,
            ExecutionLimits(2, 1),
        ),
    )

    assert boundaries


def test_recovery_preflight_rejects_invalid_binding_sets_and_unfenced_execution() -> None:
    graph = empty_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("seed-run")))
    scope_run = root_scope_run(state.run_id)
    limits = ExecutionLimits(2, 1)

    invalid_root = RecoveryInvocationSeed(
        RecoveryStateBinding(ScopeRunCoordinate((GraphNodeId("nested"),), state.run_id), state),
        (),
        ScopedFrameIndex(),
        limits,
    )
    with pytest.raises(SnapshotMismatchError, match="root binding"):
        preflight_recovery(graph, invalid_root)

    duplicate = RecoveryStateBinding(scope_run, state)
    duplicate_bindings = RecoveryInvocationSeed(
        duplicate,
        (duplicate,),
        ScopedFrameIndex(),
        limits,
    )
    with pytest.raises(SnapshotMismatchError, match="unique and canonical"):
        preflight_recovery(graph, duplicate_bindings)

    duplicate_action = AdmittedResumeFact(
        StableActivation(scope_run, state.superstep, GraphNodeId("node")),
        GraphInterruptId("interrupt"),
    )
    with pytest.raises(SnapshotMismatchError, match="actions must be unique"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(scope_run, state),
                (),
                ScopedFrameIndex(),
                limits,
                (duplicate_action, duplicate_action),
            ),
        )

    foreign_action = replace(
        duplicate_action,
        target=StableActivation(root_scope_run(GraphRunId("foreign")), state.superstep, GraphNodeId("node")),
    )
    with pytest.raises(SnapshotMismatchError, match="simulated scoped successor"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(scope_run, state),
                (),
                ScopedFrameIndex(),
                limits,
                (foreign_action,),
            ),
        )

    missing_action = replace(
        duplicate_action,
        target=StableActivation(scope_run, state.superstep, GraphNodeId("missing")),
    )
    with pytest.raises(SnapshotMismatchError, match="target is absent"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(scope_run, state),
                (),
                ScopedFrameIndex(),
                limits,
                (missing_action,),
            ),
        )

    claimed_for_settlement = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("settled-seed"), None),
    )
    assert claimed_for_settlement.execution is not None
    settled_state = reduce_graph_run(
        claimed_for_settlement,
        SettleGraphNode(
            claimed_for_settlement.revision,
            claimed_for_settlement.execution.token,
            SucceededGraphNodeOutcome(GraphNodeId("node"), ContinueGraphRouting()),
        ),
    )
    with pytest.raises(SnapshotMismatchError, match="resume action does not match"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(scope_run, settled_state),
                (),
                ScopedFrameIndex(),
                limits,
                (duplicate_action,),
            ),
        )

    active = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("active-seed"), None),
    )
    with pytest.raises(SnapshotMismatchError, match="no legal live task"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(scope_run, active),
                (),
                ScopedFrameIndex(),
                limits,
            ),
        )

    completed = replace(
        state,
        status=GraphRunStatus.COMPLETED,
        frontier=GraphFrontierState(()),
        execution_sequence=7,
    )
    terminal = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(scope_run, completed),
            (),
            ScopedFrameIndex(),
            limits,
        ),
    )
    assert len(terminal) == 1
    assert terminal[0].control.status is GraphRunStatus.COMPLETED
    assert terminal[0].control.execution_sequence == completed.execution_sequence


def test_recovery_preflight_requires_exact_resume_input_availability_for_each_interrupt_action() -> None:
    graph = empty_graph()
    node_id = GraphNodeId("node")
    root_state = reduce_graph_run(
        None,
        project_start_graph_command(graph, GraphRunId("resume-invariant-root")),
    )
    root_scope = root_scope_run(root_state.run_id)
    activation = StableActivation(root_scope, root_state.superstep, node_id)
    action = AdmittedResumeFact(
        activation,
        GraphInterruptId("interrupt"),
    )
    plan = graph.transition.materializations[node_id]
    input_frame: NodeInputFrame[str] = _make_node_input_frame((), plan.descriptor.declarations)
    exact_record: AdmittedResumeInput[str] = AdmittedResumeInput(
        ResumeInputAvailabilityCoordinate(activation, plan.descriptor.identity),
        input_frame,
    )
    limits = ExecutionLimits(2, 1)
    base_seed: RecoveryInvocationSeed[str] = RecoveryInvocationSeed(
        RecoveryStateBinding(root_scope, root_state),
        (),
        ScopedFrameIndex(resume_inputs=(exact_record,)),
        limits,
        (action,),
    )

    exact_boundaries = preflight_recovery(graph, base_seed)
    assert exact_boundaries
    assert all(boundary.availability.has_resume_input(exact_record.coordinate) for boundary in exact_boundaries)

    with pytest.raises(SnapshotMismatchError) as missing_error:
        preflight_recovery(graph, replace(base_seed, frames=ScopedFrameIndex()))
    assert str(missing_error.value) == ("recovery admitted resume action lacks its exact resume-input availability")

    wrong_descriptor = interruptible_graph().transition.materializations[node_id].descriptor.identity
    assert wrong_descriptor != plan.descriptor.identity
    wrong_record: AdmittedResumeInput[str] = AdmittedResumeInput(
        ResumeInputAvailabilityCoordinate(activation, wrong_descriptor),
        input_frame,
    )
    with pytest.raises(SnapshotMismatchError) as wrong_descriptor_error:
        preflight_recovery(
            graph,
            replace(base_seed, frames=ScopedFrameIndex(resume_inputs=(wrong_record,))),
        )
    assert str(wrong_descriptor_error.value) == (
        "recovery admitted resume action lacks its exact resume-input availability"
    )

    unknown_state = reduce_graph_run(
        None,
        project_start_graph_command(graph, GraphRunId("resume-invariant-unknown")),
    )
    unknown_scope = ScopeRunCoordinate((GraphNodeId("unknown"),), unknown_state.run_id)
    unknown_action = replace(
        action,
        target=StableActivation(unknown_scope, unknown_state.superstep, node_id),
    )
    unknown_seed: RecoveryInvocationSeed[str] = RecoveryInvocationSeed(
        RecoveryStateBinding(root_scope, root_state),
        (RecoveryStateBinding(unknown_scope, unknown_state),),
        ScopedFrameIndex(),
        limits,
        (unknown_action,),
    )
    with pytest.raises(SnapshotMismatchError) as unknown_scope_error:
        preflight_recovery(graph, unknown_seed)
    assert str(unknown_scope_error.value) == "scope references unknown nested node 'unknown'"

    missing_materializations = replace(
        graph.transition.materializations,
        entries=tuple(
            (candidate, candidate_plan)
            for candidate, candidate_plan in graph.transition.materializations.entries
            if candidate != node_id
        ),
    )
    forged_graph = replace(
        graph,
        transition=replace(graph.transition, materializations=missing_materializations),
    )
    with pytest.raises(SnapshotMismatchError) as unknown_materialization_error:
        preflight_recovery(
            forged_graph,
            replace(base_seed, frames=ScopedFrameIndex()),
        )
    assert str(unknown_materialization_error.value) == ("node input references an unknown compiled materialization")

    publication = graph.transition.publications[node_id]
    publication_record: ConfirmedPublication[str] = ConfirmedPublication(
        PublicationAvailabilityCoordinate(activation, publication.identity),
        _make_node_output_frame(Graph.values(), publication.declarations),
        root_state.revision,
        ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("duplicate-publication"))),
    )
    duplicate_frames: ScopedFrameIndex[str] = ScopedFrameIndex(publications=(publication_record, publication_record))
    with pytest.raises(SnapshotMismatchError) as duplicate_publication_error:
        preflight_recovery(graph, replace(unknown_seed, frames=duplicate_frames))
    assert str(duplicate_publication_error.value) == ("recovery publication availability coordinates must be unique")


def test_recovery_preflight_has_a_bounded_transfer_state_budget() -> None:
    node_ids = tuple(GraphNodeId(f"decision-{index:02d}") for index in range(13))
    nodes = tuple(
        CallableNodeDefinition(
            node_id,
            empty_node,
            normalize_input_bindings({}),
            normalize_output_declarations({}),
        )
        for node_id in node_ids
    )
    edges = tuple(
        ConditionalEdge(node_id, GraphRouteId(route), END) for node_id in node_ids for route in ("left", "right")
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.bounded-proof"),
            GraphDefinitionVersion(1),
            nodes,
            edges,
            (),
            normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("bounded-proof-run")))

    with pytest.raises(Graph.ExecutionLimitError, match="bounded transfer-state budget"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(root_scope_run(state.run_id), state),
                (),
                ScopedFrameIndex(),
                ExecutionLimits(2, len(node_ids)),
            ),
        )


def test_recovery_preflight_closes_a_conditional_cycle_at_its_availability_fixpoint() -> None:
    node_id = GraphNodeId("decision")
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.cycle-fixpoint"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    node_id,
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
            ),
            (
                ConditionalEdge(node_id, GraphRouteId("again"), node_id),
                ConditionalEdge(node_id, GraphRouteId("done"), END),
            ),
            (node_id,),
            normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("cycle-fixpoint-run")))

    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope_run(state.run_id), state),
            (),
            ScopedFrameIndex(),
            ExecutionLimits(100_000, 1),
        ),
    )

    assert {boundary.control.status for boundary in boundaries} == {
        GraphRunStatus.RUNNING,
        GraphRunStatus.COMPLETED,
    }


def test_recovery_preflight_uses_one_canonical_completion_order_for_plain_nodes() -> None:
    nodes = tuple(
        CallableNodeDefinition(
            GraphNodeId(f"node-{index:02d}"),
            empty_node,
            normalize_input_bindings({}),
            normalize_output_declarations({}),
        )
        for index in range(32)
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.canonical-completions"),
            GraphDefinitionVersion(1),
            nodes,
            (),
            (),
            normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("canonical-completions-run")))

    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope_run(state.run_id), state),
            (),
            ScopedFrameIndex(),
            ExecutionLimits(2, len(nodes)),
        ),
    )

    assert len(boundaries) == 1
    assert boundaries[0].control.status is GraphRunStatus.COMPLETED


def test_recovery_preflight_deduplicates_routes_with_the_same_successor_state() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.converging-routes"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    GraphNodeId("decision"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
                CallableNodeDefinition(
                    GraphNodeId("target"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
                CallableNodeDefinition(
                    GraphNodeId("final"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
            ),
            (
                ConditionalEdge(
                    GraphNodeId("decision"),
                    GraphRouteId("first"),
                    GraphNodeId("target"),
                ),
                ConditionalEdge(
                    GraphNodeId("decision"),
                    GraphRouteId("second"),
                    GraphNodeId("target"),
                ),
                DirectEdge(GraphNodeId("target"), GraphNodeId("final")),
            ),
            (),
            normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("converging-routes-run")))

    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope_run(state.run_id), state),
            (),
            ScopedFrameIndex(),
            ExecutionLimits(4, 1),
        ),
    )

    assert len(boundaries) == 1
    assert boundaries[0].control.status is GraphRunStatus.COMPLETED


def test_recovery_worklist_skips_a_duplicate_transfer_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = empty_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("duplicate-transfer-run")))
    scope_run = root_scope_run(state.run_id)
    family = _RecoveryPrivateView.family(
        recovery_module,
        (),
        ExecutionLimits(4, 1),
        (),
        _RecoveryPrivateView.proof_budget(recovery_module),
    )

    def duplicate(
        _graph: CompiledGraph[str],
        item: _RecoveryWorkItemView,
        _scope: ScopeRunCoordinate,
        _family: _RecoveryFamilyView,
    ) -> tuple[_RecoveryWorkItemView, ...]:
        return (item, item)

    monkeypatch.setattr("mote_kernel.execution.engine.recovery._expand_quiescent_executable", duplicate)

    assert (
        _RecoveryPrivateView.prove_scope(
            recovery_module,
            graph,
            state,
            scope_run,
            _EMPTY_RECOVERY_AVAILABILITY,
            family,
        )
        == ()
    )


def nested_graph(*, child_output: bool = False, ordinary_sibling: bool = False) -> CompiledGraph[str]:
    child_outputs: GraphOutputDeclarations[str] = (
        normalize_graph_output_declarations({"value": Graph.node_output("leaf", "value")})
        if child_output
        else normalize_graph_output_declarations({})
    )
    child = GraphDefinition(
        GraphDefinitionId("recovery.identity.child"),
        GraphDefinitionVersion(1),
        (
            CallableNodeDefinition(
                GraphNodeId("leaf"),
                empty_node,
                normalize_input_bindings({}),
                normalize_output_declarations({"value": str} if child_output else {}),
            ),
        ),
        (),
        (),
        child_outputs,
        resume_input=ResumeInputBinding(
            GraphResumeInputCodecId("recovery.child-input"),
            1,
            EmptyResumeCodec(),
            EmptyResumeCodec(),
        ),
    )
    ordinary_nodes = (
        (
            CallableNodeDefinition(
                GraphNodeId("ordinary"),
                empty_node,
                normalize_input_bindings({}),
                normalize_output_declarations({}),
            ),
        )
        if ordinary_sibling
        else ()
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.identity.parent"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(
                    GraphNodeId("child"),
                    child,
                    normalize_input_bindings({}),
                ),
                *ordinary_nodes,
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
        )
    )


def test_recovery_cycle_signature_keeps_a_current_child_boundary() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("cycle-child-boundary")))
    scope_run = root_scope_run(state.run_id)
    parent = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("child"))
    child_scope = child_scope_run_for_activation(scope_run, parent)
    child = graph.nested_graphs[GraphNodeId("child")]
    availability = RecoveryAvailabilityCoordinates[str](
        child_boundaries=(
            ChildBoundaryAvailabilityCoordinate(
                child_scope,
                child.graph_output_descriptor.identity,
            ),
        )
    )

    assert _cycle_signature(graph, state, availability) != _cycle_signature(graph, state)


def test_nested_settlement_rejects_a_nonterminal_child_outcome() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("invalid-child-outcome")))
    scope_run = root_scope_run(state.run_id)
    availability = RecoveryAvailabilityCoordinates[str]()
    boundary = _RecoveryPrivateView.boundary(
        recovery_module,
        _RecoveryPrivateView.boundary_kind(recovery_module).EXECUTION_LIMIT,
        state,
        scope_run,
        availability,
    )
    combination = _RecoveryPrivateView.combination(
        recovery_module,
        (_RecoveryPrivateView.outcome(recovery_module, GraphNodeId("child"), boundary),),
        availability,
    )

    with pytest.raises(SnapshotMismatchError, match="non-terminal child outcome"):
        _RecoveryPrivateView.settle_nested_outcomes(recovery_module, graph, state, scope_run, combination)


@pytest.mark.parametrize(
    "child_status",
    [GraphRunStatus.COMPLETED, GraphRunStatus.FAILED, GraphRunStatus.ABORTED],
)
def test_recovery_preflight_projects_existing_terminal_children(child_status: GraphRunStatus) -> None:
    graph = nested_graph()
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("parent-run")))
    root_scope = root_scope_run(root_state.run_id)
    parent = GraphActivationIdentity(root_state.run_id, root_state.superstep, GraphNodeId("child"))
    child_scope = child_scope_run_for_activation(root_scope, parent)
    child_graph = graph.nested_graphs[GraphNodeId("child")]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, parent),
    )
    if child_status is GraphRunStatus.COMPLETED:
        child_state = replace(child_state, status=child_status, frontier=GraphFrontierState(()))
    elif child_status is GraphRunStatus.FAILED:
        child_state = reduce_graph_run(
            child_state,
            ClaimGraphExecution(child_state.revision, GraphExecutionAttemptId("failed-child-claim"), None),
        )
        assert child_state.execution is not None
        child_state = reduce_graph_run(
            child_state,
            SettleGraphNode(
                child_state.revision,
                child_state.execution.token,
                FailedGraphNodeOutcome(GraphNodeId("leaf"), GraphFailure("child failed")),
            ),
        )
    else:
        child_state = reduce_graph_run(
            child_state,
            AbortGraphRun(child_state.revision, GraphAbortReason("child aborted")),
        )
    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope, root_state),
            (RecoveryStateBinding(child_scope, child_state),),
            ScopedFrameIndex(),
            ExecutionLimits(2, 1),
        ),
    )

    expected = GraphRunStatus.COMPLETED if child_status is GraphRunStatus.COMPLETED else GraphRunStatus.FAILED
    assert any(boundary.control.status is expected for boundary in boundaries)


def test_recovery_preflight_propagates_an_awaiting_child_boundary() -> None:
    graph = nested_graph(ordinary_sibling=True)
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("awaiting-parent")))
    root_scope = root_scope_run(root_state.run_id)
    parent = GraphActivationIdentity(root_state.run_id, root_state.superstep, GraphNodeId("child"))
    child_scope = child_scope_run_for_activation(root_scope, parent)
    child_graph = graph.nested_graphs[GraphNodeId("child")]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, parent),
    )
    claimed = reduce_graph_run(
        child_state,
        ClaimGraphExecution(
            child_state.revision,
            GraphExecutionAttemptId("awaiting-child-claim"),
            None,
        ),
    )
    execution = claimed.execution
    assert execution is not None
    awaiting = reduce_graph_run(
        claimed,
        SettleGraphNode(
            claimed.revision,
            execution.token,
            InterruptedGraphNodeOutcome(
                GraphNodeId("leaf"),
                GraphNodeInterruptIdentity(
                    claimed.run_id,
                    claimed.superstep,
                    GraphNodeId("leaf"),
                    execution.token.generation,
                ),
                GraphInterruptPayload(b"question"),
            ),
        ),
    )

    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope, root_state),
            (RecoveryStateBinding(child_scope, awaiting),),
            ScopedFrameIndex(),
            ExecutionLimits(2, 1),
        ),
    )

    assert len(boundaries) == 1
    assert boundaries[0].control.scope_run == root_scope
    assert boundaries[0].control.status is GraphRunStatus.RUNNING
    settlements = {node.node_id: node.settlement for node in boundaries[0].control.frontier}
    assert settlements == {
        GraphNodeId("child"): RecoverySettlementKind.PENDING_MATERIALIZED,
        GraphNodeId("ordinary"): RecoverySettlementKind.SUCCEEDED_CONTINUE,
    }


def test_recovery_preflight_settles_pending_siblings_before_failed_child_cleanup() -> None:
    codec = EmptyResumeCodec()

    def child_definition(definition_id: str) -> GraphDefinition[str]:
        return GraphDefinition(
            GraphDefinitionId(definition_id),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    GraphNodeId("leaf"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
            resume_input=ResumeInputBinding(
                GraphResumeInputCodecId("recovery.child-priority"),
                1,
                codec,
                codec,
            ),
        )

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.child-priority.parent"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(
                    GraphNodeId("failed"),
                    child_definition("recovery.child-priority.failed"),
                    normalize_input_bindings({}),
                ),
                NestedGraphNodeDefinition(
                    GraphNodeId("waiting"),
                    child_definition("recovery.child-priority.waiting"),
                    normalize_input_bindings({}),
                ),
                CallableNodeDefinition(
                    GraphNodeId("ordinary"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
                CallableNodeDefinition(
                    GraphNodeId("resource"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                    (ResourceId("file"),),
                ),
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
            resources=(ResourceDefinition(ResourceId("file")),),
        )
    )
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("child-priority-parent")))
    root_scope = root_scope_run(root_state.run_id)
    child_bindings: list[RecoveryStateBinding] = []
    for node_id in (GraphNodeId("failed"), GraphNodeId("waiting")):
        parent = GraphActivationIdentity(root_state.run_id, root_state.superstep, node_id)
        child_scope = child_scope_run_for_activation(root_scope, parent)
        child = graph.nested_graphs[node_id]
        child_state = reduce_graph_run(
            None,
            project_start_graph_command(child, child_scope.graph_run_id, parent),
        )
        claimed = reduce_graph_run(
            child_state,
            ClaimGraphExecution(
                child_state.revision,
                GraphExecutionAttemptId(f"{node_id}-claim"),
                None,
            ),
        )
        execution = claimed.execution
        assert execution is not None
        outcome = (
            FailedGraphNodeOutcome(GraphNodeId("leaf"), GraphFailure("child failed"))
            if node_id == "failed"
            else InterruptedGraphNodeOutcome(
                GraphNodeId("leaf"),
                GraphNodeInterruptIdentity(
                    claimed.run_id,
                    claimed.superstep,
                    GraphNodeId("leaf"),
                    execution.token.generation,
                ),
                GraphInterruptPayload(b"question"),
            )
        )
        settled = reduce_graph_run(
            claimed,
            SettleGraphNode(claimed.revision, execution.token, outcome),
        )
        child_bindings.append(RecoveryStateBinding(child_scope, settled))

    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope, root_state),
            tuple(child_bindings),
            ScopedFrameIndex(),
            ExecutionLimits(2, 1),
        ),
    )

    assert len(boundaries) == 1
    assert boundaries[0].control.status is GraphRunStatus.FAILED
    settlements = {node.node_id: node.settlement for node in boundaries[0].control.frontier}
    assert settlements == {
        GraphNodeId("failed"): RecoverySettlementKind.FAILED,
        GraphNodeId("ordinary"): RecoverySettlementKind.SUCCEEDED_CONTINUE,
        GraphNodeId("resource"): RecoverySettlementKind.SUCCEEDED_CONTINUE,
        GraphNodeId("waiting"): RecoverySettlementKind.FAILED,
    }


def test_recovery_preflight_cleans_up_awaiting_child_after_ordinary_failure() -> None:
    child = Graph[str]("recovery.ordinary-failure.child")
    child.set_resume_codec("empty", 1, EmptyResumeCodec().encode, EmptyResumeCodec().decode)
    child.add_node("leaf", empty_node, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("recovery.ordinary-failure.parent")
    parent.add_node("ordinary", empty_node, inputs={}, outputs={})
    parent.add_node("waiting", child, inputs={})
    parent.set_outputs({})
    graph = _GraphPrivateView.compile(parent).graph
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("ordinary-failure-parent")))
    root_scope = root_scope_run(root_state.run_id)

    claimed_root = reduce_graph_run(
        root_state,
        ClaimGraphExecution(root_state.revision, GraphExecutionAttemptId("ordinary-failure-claim"), None),
    )
    root_execution = claimed_root.execution
    assert root_execution is not None
    root_state = reduce_graph_run(
        claimed_root,
        SettleGraphNode(
            claimed_root.revision,
            root_execution.token,
            FailedGraphNodeOutcome(GraphNodeId("ordinary"), GraphFailure("ordinary failed")),
        ),
    )
    root_state = reduce_graph_run(
        root_state,
        FenceGraphExecution(root_state.revision, root_execution.token),
    )

    child_parent = GraphActivationIdentity(root_state.run_id, root_state.superstep, GraphNodeId("waiting"))
    child_scope = child_scope_run_for_activation(root_scope, child_parent)
    child_graph = graph.nested_graphs[GraphNodeId("waiting")]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, child_parent),
    )
    claimed_child = reduce_graph_run(
        child_state,
        ClaimGraphExecution(child_state.revision, GraphExecutionAttemptId("awaiting-child-claim"), None),
    )
    child_execution = claimed_child.execution
    assert child_execution is not None
    child_state = reduce_graph_run(
        claimed_child,
        SettleGraphNode(
            claimed_child.revision,
            child_execution.token,
            InterruptedGraphNodeOutcome(
                GraphNodeId("leaf"),
                GraphNodeInterruptIdentity(
                    claimed_child.run_id,
                    claimed_child.superstep,
                    GraphNodeId("leaf"),
                    child_execution.token.generation,
                ),
                GraphInterruptPayload(b"question"),
            ),
        ),
    )
    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope, root_state),
            (RecoveryStateBinding(child_scope, child_state),),
            ScopedFrameIndex(),
            ExecutionLimits(2, 1),
        ),
    )

    assert len(boundaries) == 1
    assert boundaries[0].control.status is GraphRunStatus.FAILED
    settlements = {node.node_id: node.settlement for node in boundaries[0].control.frontier}
    assert settlements == {
        GraphNodeId("ordinary"): RecoverySettlementKind.FAILED,
        GraphNodeId("waiting"): RecoverySettlementKind.FAILED,
    }


def test_recovery_preflight_rejects_completed_child_without_output_history() -> None:
    graph = nested_graph(child_output=True)
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("parent-output-run")))
    root_scope = root_scope_run(root_state.run_id)
    parent = GraphActivationIdentity(root_state.run_id, root_state.superstep, GraphNodeId("child"))
    child_scope = child_scope_run_for_activation(root_scope, parent)
    child_graph = graph.nested_graphs[GraphNodeId("child")]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, parent),
    )
    child_state = replace(child_state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    with pytest.raises(GraphValueUnavailableError, match="child output history"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(root_scope, root_state),
                (
                    RecoveryStateBinding(
                        child_scope,
                        child_state,
                    ),
                ),
                ScopedFrameIndex(),
                ExecutionLimits(2, 1),
            ),
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("run", "scope-run identity"),
        ("missing-parent", "missing its parent activation"),
        ("foreign-parent", "does not match its parent activation"),
    ],
)
def test_recovery_preflight_rejects_each_malformed_child_control_binding(
    case: str,
    message: str,
) -> None:
    graph = nested_graph()
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("invalid-child-parent")))
    root_scope = root_scope_run(root_state.run_id)
    expected_parent = GraphActivationIdentity(root_state.run_id, root_state.superstep, GraphNodeId("child"))
    child_scope = child_scope_run_for_activation(root_scope, expected_parent)
    child_graph = graph.nested_graphs[GraphNodeId("child")]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, expected_parent),
    )
    if case == "run":
        child_state = replace(child_state, run_id=GraphRunId("foreign-child-run"))
    elif case == "missing-parent":
        child_state = replace(child_state, parent=None)
    else:
        foreign_parent = GraphActivationIdentity(
            root_state.run_id,
            root_state.superstep + 1,
            GraphNodeId("child"),
        )
        child_state = replace(child_state, parent=foreign_parent)

    with pytest.raises(SnapshotMismatchError, match=message):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(root_scope, root_state),
                (
                    RecoveryStateBinding(
                        child_scope,
                        child_state,
                    ),
                ),
                ScopedFrameIndex(),
                ExecutionLimits(2, 1),
            ),
        )


def test_recovery_settlement_requires_its_simulated_claim_token() -> None:
    with pytest.raises(Graph.Error, match="requires a committed execution lease"):
        require_settlement_execution_token(
            reduce_graph_run(None, project_start_graph_command(empty_graph(), GraphRunId("unclaimed")))
        )


async def publish_node(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(value="published")


def _publication_node(node_id: str) -> CallableNodeDefinition[str]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        publish_node,
        normalize_input_bindings({}),
        normalize_output_declarations({"value": str}),
    )


def _settle_successes(
    state: GraphRunState,
    outcomes: tuple[tuple[str, str | None], ...],
) -> GraphRunState:
    claimed = reduce_graph_run(
        state,
        ClaimGraphExecution(
            state.revision,
            GraphExecutionAttemptId(f"claim-{state.revision}"),
            None,
        ),
    )
    execution = claimed.execution
    assert execution is not None
    for node_id, route in outcomes:
        routing = ContinueGraphRouting() if route is None else SelectGraphRoute(GraphRouteId(route))
        claimed = reduce_graph_run(
            claimed,
            SettleGraphNode(
                claimed.revision,
                execution.token,
                SucceededGraphNodeOutcome(GraphNodeId(node_id), routing),
            ),
        )
    return claimed


def _partial_history_frames(
    graph: CompiledGraph[str],
    scope_run: ScopeRunCoordinate,
) -> ScopedFrameIndex[str]:
    graph_input = _make_graph_input_frame(
        Graph.values(input="present"),
        graph.graph_input_descriptor.declarations,
    )
    publication = graph.transition.publications[GraphNodeId("available")]
    output = _make_node_output_frame(
        Graph.values(value="present"),
        publication.declarations,
    )
    return ScopedFrameIndex(
        graph_inputs=(
            AdmittedGraphInput(
                GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
                graph_input,
            ),
        ),
        publications=(
            ConfirmedPublication(
                PublicationAvailabilityCoordinate(
                    StableActivation(scope_run, 0, GraphNodeId("available")),
                    publication.identity,
                ),
                output,
                1,
                ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("availability"))),
            ),
        ),
    )


def test_recovery_historical_target_scan_retains_present_inputs_before_the_gap() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.target-history-scan"),
            GraphDefinitionVersion(1),
            (
                _publication_node("available"),
                _publication_node("missing"),
                CallableNodeDefinition(
                    GraphNodeId("decision"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
                CallableNodeDefinition(
                    GraphNodeId("consumer"),
                    empty_node,
                    normalize_input_bindings(
                        {
                            "a_input": Graph.graph_input("input", str),
                            "b_available": Graph.node_output("available", "value"),
                            "c_missing": Graph.node_output("missing", "value"),
                        }
                    ),
                    normalize_output_declarations({}),
                ),
            ),
            (
                JoinEdge(
                    (GraphNodeId("available"), GraphNodeId("missing")),
                    GraphNodeId("decision"),
                ),
                ConditionalEdge(
                    GraphNodeId("decision"),
                    GraphRouteId("consume"),
                    GraphNodeId("consumer"),
                ),
            ),
            (),
            normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("target-history-run")))
    scope_run = root_scope_run(state.run_id)
    frames = _partial_history_frames(graph, scope_run)
    state = _settle_successes(state, (("available", None), ("missing", None)))
    state = reduce_graph_run(state, resolve_routing(graph, state, scope_run, frames))
    state = _settle_successes(state, (("decision", "consume"),))

    with pytest.raises(GraphValueUnavailableError, match="historical"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(scope_run, state),
                (),
                frames,
                ExecutionLimits(4, 2),
            ),
        )


def test_recovery_historical_output_scan_retains_present_outputs_before_the_gap() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.output-history-scan"),
            GraphDefinitionVersion(1),
            (
                _publication_node("available"),
                _publication_node("missing"),
                CallableNodeDefinition(
                    GraphNodeId("final"),
                    empty_node,
                    normalize_input_bindings({}),
                    normalize_output_declarations({}),
                ),
            ),
            (
                JoinEdge(
                    (GraphNodeId("available"), GraphNodeId("missing")),
                    GraphNodeId("final"),
                ),
            ),
            (),
            normalize_graph_output_declarations(
                {
                    "a_input": Graph.graph_input("input", str),
                    "b_available": Graph.node_output("available", "value"),
                    "c_missing": Graph.node_output("missing", "value"),
                }
            ),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("output-history-run")))
    scope_run = root_scope_run(state.run_id)
    frames = _partial_history_frames(graph, scope_run)
    state = _settle_successes(state, (("available", None), ("missing", None)))
    state = reduce_graph_run(state, resolve_routing(graph, state, scope_run, frames))
    state = _settle_successes(state, (("final", None),))

    with pytest.raises(GraphValueUnavailableError, match="historical"):
        preflight_recovery(
            graph,
            RecoveryInvocationSeed(
                RecoveryStateBinding(scope_run, state),
                (),
                frames,
                ExecutionLimits(4, 2),
            ),
        )
