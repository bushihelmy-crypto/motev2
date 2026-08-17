from dataclasses import replace
from typing import Never

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.recovery import (
    AdmittedActionKind,
    AdmittedResumeFact,
    ChildControlStateCoordinate,
    ChildRecoveryDisposition,
    RecoveryAvailabilityCoordinates,
    RecoveryInvocationSeed,
    RecoveryStateBinding,
    RecoveryTransferState,
    preflight_recovery,
    recovery_traversal_key,
)
from mote_kernel.execution.engine.routing import plan_routing
from mote_kernel.execution.engine.settlement import require_settlement_execution_token
from mote_kernel.execution.errors import GraphValueUnavailableError, SnapshotMismatchError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    FrameDescriptorIdentity,
    FrameKind,
    GraphOutputDeclarations,
    canonical_nominal_type,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import _make_graph_input_frame, _make_node_output_frame
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import (
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ChildBoundaryAvailabilityCoordinate,
    ConfirmedPublication,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    ContinueGraphRouting,
    FailedGraphNodeOutcome,
    GraphAbortReason,
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
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    InterruptedGraphNodeOutcome,
    ParentGraphActivation,
    PendingGraphNode,
    SelectGraphRoute,
    SettleGraphNode,
    SkippedGraphNode,
    SucceededGraphNodeOutcome,
    UseStepRequestInput,
    derive_graph_node_interrupt_identity,
    graph_interrupt_id,
    reduce_graph_run,
)


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
    identity = derive_graph_node_interrupt_identity(
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

    parent = ParentGraphActivation(root.graph_run_id, 2, GraphNodeId("child"))
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
    resumed: AdmittedResumeFact[str] = AdmittedResumeFact(
        activation,
        AdmittedActionKind.RESUME_INTERRUPTED,
        GraphInterruptId("interrupt"),
        None,
        None,
        resume_input,
    )
    skipped: AdmittedResumeFact[str] = AdmittedResumeFact(
        activation,
        AdmittedActionKind.SKIP_FAILED,
        None,
        "operator skip",
        GraphRouteId("route"),
        None,
    )
    rich = replace(
        baseline,
        live=(GraphNodeId("node"),),
        availability=availability,
        children=(child_disposition,),
        admitted_actions=(resumed, skipped),
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
    descriptor = canonical_nominal_type(HostileValue)
    frame = _make_graph_input_frame(Graph.values(value=HostileValue()), (("value", descriptor),))
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
            ExecutionLimits(3, 1),
        ),
    )

    assert len(boundaries) == 1
    assert boundaries[0].control.status is GraphRunStatus.COMPLETED


def nested_graph(*, child_output: bool = False) -> CompiledGraph[str]:
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
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
        )
    )


@pytest.mark.parametrize("child_status", [GraphRunStatus.COMPLETED, GraphRunStatus.ABORTED])
def test_recovery_preflight_projects_existing_terminal_children(child_status: GraphRunStatus) -> None:
    graph = nested_graph()
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("parent-run")))
    root_scope = root_scope_run(root_state.run_id)
    parent = ParentGraphActivation(root_state.run_id, root_state.superstep, GraphNodeId("child"))
    child_scope = child_scope_run_for_activation(root_scope, parent)
    child_graph = graph.nested_graphs[GraphNodeId("child")]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, parent),
    )
    if child_status is GraphRunStatus.COMPLETED:
        child_state = replace(child_state, status=child_status, frontier=GraphFrontierState(()))
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

    expected = GraphRunStatus.COMPLETED if child_status is GraphRunStatus.COMPLETED else GraphRunStatus.RUNNING
    assert any(boundary.control.status is expected for boundary in boundaries)


def test_recovery_preflight_propagates_an_awaiting_child_boundary() -> None:
    graph = nested_graph()
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("awaiting-parent")))
    root_scope = root_scope_run(root_state.run_id)
    parent = ParentGraphActivation(root_state.run_id, root_state.superstep, GraphNodeId("child"))
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
            FailedGraphNodeOutcome(GraphNodeId("leaf"), GraphFailure("pause child")),
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


def test_recovery_preflight_linearizes_completed_and_aborted_child_possibilities() -> None:
    source = GraphNodeId("source")
    decision = GraphNodeId("decision")
    consumer = GraphNodeId("consumer")
    child_definition = GraphDefinition(
        GraphDefinitionId("recovery.linear-child"),
        GraphDefinitionVersion(1),
        (
            CallableNodeDefinition(
                source,
                empty_node,
                normalize_input_bindings({}),
                normalize_output_declarations({"value": str}),
            ),
            CallableNodeDefinition(
                decision,
                empty_node,
                normalize_input_bindings({}),
                normalize_output_declarations({}),
            ),
            CallableNodeDefinition(
                consumer,
                empty_node,
                normalize_input_bindings({"value": Graph.node_output("source", "value")}),
                normalize_output_declarations({}),
            ),
        ),
        (
            ConditionalEdge(source, GraphRouteId("activate"), decision),
            ConditionalEdge(source, GraphRouteId("stop"), END),
            ConditionalEdge(decision, GraphRouteId("consume"), consumer),
            ConditionalEdge(decision, GraphRouteId("finish"), END),
        ),
        (),
        normalize_graph_output_declarations({}),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.linear-parent"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(
                    GraphNodeId("child"),
                    child_definition,
                    normalize_input_bindings({}),
                ),
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
        )
    )
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("linear-parent-run")))
    root_scope = root_scope_run(root_state.run_id)
    parent = ParentGraphActivation(root_state.run_id, root_state.superstep, GraphNodeId("child"))
    child_scope = child_scope_run_for_activation(root_scope, parent)
    child_graph = graph.nested_graphs[GraphNodeId("child")]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, parent),
    )
    child_state = replace(
        child_state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(decision, PendingGraphNode(UseStepRequestInput())),
                GraphFrontierNode(
                    source,
                    SkippedGraphNode(
                        GraphFailure("source skipped"),
                        GraphSkipReason("operator skip"),
                        SelectGraphRoute(GraphRouteId("stop")),
                    ),
                ),
            )
        ),
    )

    boundaries = preflight_recovery(
        graph,
        RecoveryInvocationSeed(
            RecoveryStateBinding(root_scope, root_state),
            (RecoveryStateBinding(child_scope, child_state),),
            ScopedFrameIndex(),
            ExecutionLimits(3, 1),
        ),
    )

    assert len(boundaries) == 2
    assert {boundary.control.status for boundary in boundaries} == {
        GraphRunStatus.RUNNING,
        GraphRunStatus.COMPLETED,
    }


def test_recovery_preflight_rejects_completed_child_without_output_history() -> None:
    graph = nested_graph(child_output=True)
    root_state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("parent-output-run")))
    root_scope = root_scope_run(root_state.run_id)
    parent = ParentGraphActivation(root_state.run_id, root_state.superstep, GraphNodeId("child"))
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
    expected_parent = ParentGraphActivation(root_state.run_id, root_state.superstep, GraphNodeId("child"))
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
        foreign_parent = ParentGraphActivation(
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
        tuple((item.name, item.descriptor) for item in graph.graph_inputs.entries),
    )
    publication = graph.publications[GraphNodeId("available")]
    output = _make_node_output_frame(
        Graph.values(value="present"),
        tuple((item.name, item.descriptor) for item in publication.descriptor.declarations.entries),
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
                    publication.descriptor.identity,
                ),
                output,
                1,
                GraphExecutionToken(1, GraphExecutionAttemptId("availability")),
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
    state = reduce_graph_run(state, plan_routing(graph, state, scope_run, frames).command)
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
    state = reduce_graph_run(state, plan_routing(graph, state, scope_run, frames).command)
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
