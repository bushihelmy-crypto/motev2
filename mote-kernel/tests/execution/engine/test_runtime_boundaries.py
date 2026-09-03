"""Fail-closed boundaries retained by the scoped-frame execution runtime."""

import asyncio
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.driver import step_request
from tests.execution.engine.factories import compiled_graph, running_state

import mote_kernel.execution.family_driver as family_driver_module
from mote_kernel.execution import Graph
from mote_kernel.execution.claim import ExecutionClaimOwner
from mote_kernel.execution.engine.frontier import FrontierPreparation, prepare_frontier
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.resume_input import (
    encode_resume_input,
    materialize_node_input,
    require_resume_input_binding,
)
from mote_kernel.execution.engine.routing import resolve_routing
from mote_kernel.execution.engine.scheduler import TaskRaised, TaskScheduler
from mote_kernel.execution.engine.session import (
    GraphExecutionSession,
    consume_node_origin_cancellation,
    issue_execution_session,
)
from mote_kernel.execution.engine.snapshot_guard import (
    require_scoped_snapshot_matches_graph,
    require_snapshot_matches_graph,
)
from mote_kernel.execution.engine.superstep import ExecutableFrontier, prepare_superstep
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask, TaskId, task_identity
from mote_kernel.execution.errors import (
    InvalidExecutionSnapshotError,
    InvalidRoutingCommandError,
    JoinProgressError,
    NodeExecutionContractError,
    ResultCollectionError,
    SnapshotMismatchError,
)
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition, NodeCallable
from mote_kernel.execution.graph.ports import (
    FrameDescriptorIdentity,
    FrameKind,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    GraphInputFrame,
    GraphOutputView,
    NamedValue,
    NodeInputFrame,
    NodeOutputFrame,
    _make_graph_input_frame,
    _make_graph_output_view,
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
from mote_kernel.execution.invocation import (
    lineage_states,
    plan_fences,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.execution.result import (
    AbortedChild,
    ActiveChild,
    ChildProjection,
    CompletedChild,
    FailedChild,
    FailedGraph,
    MissingChild,
    TaskFailure,
    TaskSuccess,
    WaitingForChildren,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedResumeInput,
    ChildBoundaryAvailabilityCoordinate,
    ChildStateBinding,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
    _CompiledFamilyIdentity,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ActivationReference,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphAbortReason,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphJoinProgress,
    GraphNodeId,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    RoutedActivationCause,
    SelectGraphRoute,
    StartActivationCause,
    SucceededGraphNode,
    UseStepRequestInput,
    child_graph_run_id,
    reduce_graph_run,
)


async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
    return values


DEFAULT_LIMITS = ExecutionLimits()


class TextCodec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


class ConcreteValueTrap:
    def __repr__(self) -> str:
        raise AssertionError("concrete value repr must not be used")

    def __hash__(self) -> int:
        raise AssertionError("concrete value hash must not be used")

    def __eq__(self, _other: object) -> bool:
        raise AssertionError("concrete value equality must not be used")

    def __lt__(self, _other: object) -> bool:
        raise AssertionError("concrete value ordering must not be used")


def string_node(
    node_id: str,
    operation: NodeCallable[str] = echo,
    *,
    resources: tuple[ResourceId, ...] = (),
) -> CallableNodeDefinition[str]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        operation,
        normalize_input_bindings({"value": Graph.graph_input("value", str)}),
        normalize_output_declarations({"value": str}),
        resources,
    )


def request(
    graph: CompiledGraph[str],
    state: GraphRunState,
    projections: tuple[ChildProjection[str], ...] = (),
    *,
    value: str = "input",
    limits: ExecutionLimits = DEFAULT_LIMITS,
) -> StepRequest[str]:
    return step_request(graph, state, value, projections, limits).execution_request()


def prepare_execution_frontier(
    graph: CompiledGraph[str],
    state: GraphRunState,
    projections: tuple[ChildProjection[str], ...] = (),
) -> tuple[ExecutionClaimOwner, ExecutableFrontier[str]]:
    owner = ExecutionClaimOwner()
    execution_request = request(graph, state, projections)
    disposition = prepare_superstep(owner, graph, execution_request)
    assert isinstance(disposition, ExecutableFrontier)
    return owner, disposition


def executable_input(graph: CompiledGraph[str], state: GraphRunState, node_id: str) -> NodeInputFrame[str]:
    execution_request = request(graph, state)
    return materialize_node_input(
        graph,
        state,
        execution_request.scope_run,
        execution_request.frames,
        GraphNodeId(node_id),
    )


def child_output(graph: CompiledGraph[str], value: str) -> GraphOutputView[str]:
    return _make_graph_output_view(
        (NamedValue("value", value),),
        graph.graph_output_descriptor.declarations,
    )


def nested_graph(*, with_consumer: bool = False) -> CompiledGraph[str]:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("boundary.child"),
        version=GraphDefinitionVersion(1),
        nodes=(string_node("child"),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({"value": Graph.node_output("child", "value")}),
    )
    nodes: list[CallableNodeDefinition[str] | NestedGraphNodeDefinition[str]] = [
        NestedGraphNodeDefinition(
            GraphNodeId("nested"),
            child,
            normalize_input_bindings({"value": Graph.graph_input("value", str)}),
        )
    ]
    if with_consumer:
        nodes.append(
            CallableNodeDefinition(
                GraphNodeId("consumer"),
                echo,
                normalize_input_bindings({"value": Graph.node_output("nested", "value")}),
                normalize_output_declarations({"value": str}),
            )
        )
        nodes.append(
            CallableNodeDefinition(
                GraphNodeId("controller"),
                echo,
                normalize_input_bindings({}),
                normalize_output_declarations({}),
            )
        )
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("boundary.parent"),
            version=GraphDefinitionVersion(1),
            nodes=tuple(nodes),
            edges=(
                DirectEdge(GraphNodeId("nested"), GraphNodeId("controller")),
                DirectEdge(GraphNodeId("nested"), GraphNodeId("consumer")),
                ConditionalEdge(GraphNodeId("controller"), GraphRouteId("repeat"), GraphNodeId("nested")),
                ConditionalEdge(GraphNodeId("controller"), GraphRouteId("done"), END),
            )
            if with_consumer
            else (),
            entries=(GraphNodeId("nested"),) if with_consumer else (),
            outputs=normalize_graph_output_declarations({}),
        )
    )


def parallel_nested_graph() -> CompiledGraph[str]:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("boundary.child"),
        version=GraphDefinitionVersion(1),
        nodes=(string_node("child"),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({"value": Graph.node_output("child", "value")}),
    )
    nested_inputs = normalize_input_bindings({"value": Graph.graph_input("value", str)})
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("boundary.parallel-parent"),
            version=GraphDefinitionVersion(1),
            nodes=(
                NestedGraphNodeDefinition(GraphNodeId("a"), child, nested_inputs),
                NestedGraphNodeDefinition(GraphNodeId("b"), child, nested_inputs),
            ),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )


def started_nested_child(
    parent_graph: CompiledGraph[str],
    parent_state: GraphRunState,
    parent_scope: ScopeRunCoordinate,
    node_id: GraphNodeId,
) -> tuple[ScopeRunCoordinate, StableActivation, GraphRunState]:
    parent = GraphActivationIdentity(parent_state.run_id, parent_state.superstep, node_id)
    coordinate = child_scope_run_for_activation(parent_scope, parent)
    child_graph = parent_graph.nested_graphs[node_id]
    command = project_start_graph_command(child_graph, coordinate.graph_run_id, parent)
    activation = StableActivation(parent_scope, parent.superstep, parent.node_id)
    return coordinate, activation, reduce_graph_run(None, command)


def test_waiting_for_children_rejects_an_empty_internal_disposition() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        WaitingForChildren[str]((), ())


def test_node_origin_marker_rejects_a_foreign_session() -> None:
    session = cast(GraphExecutionSession[str], object())

    with pytest.raises(ResultCollectionError, match="not issued"):
        consume_node_origin_cancellation(session, asyncio.CancelledError())


def test_lineage_rejects_a_child_binding_at_the_root_coordinate() -> None:
    state = running_state()
    scope_run = root_scope_run(state.run_id)
    binding = ChildStateBinding(
        scope_run,
        StableActivation(scope_run, state.superstep, GraphNodeId("a")),
        state,
    )

    with pytest.raises(SnapshotMismatchError, match="repeats one scoped graph run"):
        lineage_states(state, (binding,))


def test_fence_planning_rejects_parent_metadata_on_a_root_state() -> None:
    graph = compiled_graph("a")
    parent = GraphActivationIdentity(GraphRunId("outer"), 0, GraphNodeId("parent"))
    state = replace(
        running_state(run_id=child_graph_run_id(parent.run_id, parent.superstep, parent.node_id)),
        parent=parent,
    )

    with pytest.raises(SnapshotMismatchError, match="root graph state cannot carry"):
        plan_fences(graph, lineage_states(state, ()))


def test_fence_planning_rejects_a_child_state_without_its_parent() -> None:
    graph = nested_graph()
    parent = running_state(definition_id=graph.definition_id, frontier=("nested",), run_id="parent")
    parent_scope = root_scope_run(parent.run_id)
    child_scope, activation, child = started_nested_child(
        graph,
        parent,
        parent_scope,
        GraphNodeId("nested"),
    )
    binding = ChildStateBinding(child_scope, activation, replace(child, parent=None))

    with pytest.raises(SnapshotMismatchError, match="nested graph state does not match"):
        plan_fences(graph, lineage_states(parent, (binding,)))


def test_fence_planning_rejects_a_child_from_a_future_parent_frontier() -> None:
    graph = nested_graph()
    parent = running_state(definition_id=graph.definition_id, frontier=("nested",), run_id="parent")
    parent_scope = root_scope_run(parent.run_id)
    future_parent = GraphActivationIdentity(
        parent.run_id,
        parent.superstep + 1,
        GraphNodeId("nested"),
    )
    child_scope = child_scope_run_for_activation(parent_scope, future_parent)
    child = reduce_graph_run(
        None,
        project_start_graph_command(
            graph.nested_graphs[future_parent.node_id],
            child_scope.graph_run_id,
            future_parent,
        ),
    )
    binding = ChildStateBinding(
        child_scope,
        StableActivation(parent_scope, future_parent.superstep, future_parent.node_id),
        child,
    )

    with pytest.raises(SnapshotMismatchError, match="future parent frontier"):
        plan_fences(graph, lineage_states(parent, (binding,)))


def test_resume_input_narrow_guards() -> None:
    graph = compiled_graph("a")
    state = running_state()
    with pytest.raises(SnapshotMismatchError, match="does not define"):
        encode_resume_input(graph, Graph.values(value="value"))
    execution_request = request(graph, state)
    with pytest.raises(SnapshotMismatchError, match="current pending"):
        materialize_node_input(
            graph,
            state,
            execution_request.scope_run,
            execution_request.frames,
            GraphNodeId("missing"),
        )

    override = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("a"),
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"opaque"))),
                    StartActivationCause(),
                ),
            )
        ),
    )
    with pytest.raises(SnapshotMismatchError, match="decoder"):
        materialize_node_input(
            graph,
            override,
            execution_request.scope_run,
            execution_request.frames,
            GraphNodeId("a"),
        )

    codec = TextCodec()
    resumable = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("test.graph"),
            version=GraphDefinitionVersion(1),
            nodes=(string_node("a"),),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
            resume_input=ResumeInputBinding(
                GraphResumeInputCodecId("compiled.v1"),
                1,
                codec,
                codec,
            ),
        )
    )
    mismatched = replace(
        state,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("durable.v1"), 1),
    )
    with pytest.raises(SnapshotMismatchError, match="does not match durable"):
        require_resume_input_binding(resumable, mismatched)

    trap = ConcreteValueTrap()
    declarations = normalize_output_declarations({"value": ConcreteValueTrap})
    entries = (NamedValue("value", trap),)
    graph_input_frame: GraphInputFrame[ConcreteValueTrap] = _make_graph_input_frame(
        Graph.values(value=trap),
        declarations,
    )
    node_input_frame: NodeInputFrame[ConcreteValueTrap] = _make_node_input_frame(entries, declarations)
    node_output_frame: NodeOutputFrame[ConcreteValueTrap] = _make_node_output_frame(
        Graph.values(value=trap),
        declarations,
    )
    graph_output_frame: GraphOutputView[ConcreteValueTrap] = _make_graph_output_view(entries, declarations)
    for frame in (graph_input_frame, node_input_frame, node_output_frame, graph_output_frame):
        with pytest.raises(Graph.ValueAdmissionError, match="canonical owner"):
            replace(frame, _seal=1)
    scope_run = root_scope_run(GraphRunId("trap-run"))
    activation = StableActivation(scope_run, 0, GraphNodeId("node"))
    graph_input_coordinate: GraphInputAvailabilityCoordinate[ConcreteValueTrap] = GraphInputAvailabilityCoordinate(
        scope_run,
        FrameDescriptorIdentity("trap.graph", 1, FrameKind.GRAPH_INPUT, 0),
    )
    publication_coordinate: PublicationAvailabilityCoordinate[ConcreteValueTrap] = PublicationAvailabilityCoordinate(
        activation,
        FrameDescriptorIdentity("trap.graph", 1, FrameKind.NODE_OUTPUT, 0),
    )
    resume_coordinate: ResumeInputAvailabilityCoordinate[ConcreteValueTrap] = ResumeInputAvailabilityCoordinate(
        activation,
        FrameDescriptorIdentity("trap.graph", 1, FrameKind.NODE_INPUT, 0),
    )
    boundary_coordinate: ChildBoundaryAvailabilityCoordinate[ConcreteValueTrap] = ChildBoundaryAvailabilityCoordinate(
        scope_run,
        FrameDescriptorIdentity("trap.graph", 1, FrameKind.GRAPH_OUTPUT, 0),
    )
    graph_input_record = AdmittedGraphInput(graph_input_coordinate, graph_input_frame)
    publication_record = ConfirmedPublication(
        publication_coordinate,
        node_output_frame,
        1,
        ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("trap-attempt"))),
    )
    resume_record = AdmittedResumeInput(resume_coordinate, node_input_frame)
    boundary_record = ConfirmedChildBoundary(boundary_coordinate, graph_output_frame)
    frame_index: ScopedFrameIndex[ConcreteValueTrap] = ScopedFrameIndex()
    frame_index = frame_index.add_graph_input(graph_input_record)
    frame_index = frame_index.add_publication(publication_record)
    frame_index = frame_index.add_resume_input(resume_record)
    frame_index = frame_index.add_child_boundary(boundary_record)

    assert frame_index.lookup(graph_input_coordinate) is graph_input_record
    assert frame_index.lookup(publication_coordinate) is publication_record
    assert frame_index.lookup(resume_coordinate) is resume_record
    assert frame_index.lookup(boundary_coordinate) is boundary_record
    assert frame_index.has_graph_input(graph_input_coordinate)
    assert frame_index.has_publication(publication_coordinate)
    assert frame_index.has_resume_input(resume_coordinate)
    assert frame_index.has_child_boundary(boundary_coordinate)
    assert frame_index.publications == (publication_record,)
    other_scope_run = root_scope_run(GraphRunId("trap-run-2"))
    other_graph_input_coordinate: GraphInputAvailabilityCoordinate[ConcreteValueTrap] = (
        GraphInputAvailabilityCoordinate(other_scope_run, graph_input_coordinate.descriptor)
    )
    other_graph_input = AdmittedGraphInput(other_graph_input_coordinate, graph_input_frame)
    expanded_index = frame_index.add_graph_input(other_graph_input)
    assert expanded_index.lookup(other_graph_input_coordinate) is other_graph_input
    assert expanded_index.lookup(graph_input_coordinate) is graph_input_record
    assert len(expanded_index.graph_inputs) == 2
    assert "ConcreteValueTrap" not in repr(frame_index)
    for record in (graph_input_record, publication_record, resume_record, boundary_record):
        assert "ConcreteValueTrap" not in repr(record)
        with pytest.raises(TypeError, match="unhashable"):
            hash(record)
    with pytest.raises(TypeError, match="unhashable"):
        hash(frame_index)
    assert graph_input_record != AdmittedGraphInput(graph_input_coordinate, graph_input_frame)
    assert frame_index != ScopedFrameIndex(
        graph_inputs=frame_index.graph_inputs,
        publications=frame_index.publications,
        resume_inputs=frame_index.resume_inputs,
        child_boundaries=frame_index.child_boundaries,
    )

    with pytest.raises(Graph.ValuePublicationError, match="more than once"):
        frame_index.add_graph_input(AdmittedGraphInput(graph_input_coordinate, graph_input_frame))
    with pytest.raises(Graph.ValuePublicationError, match="more than once"):
        frame_index.add_publication(
            ConfirmedPublication(
                publication_coordinate,
                node_output_frame,
                2,
                ExecutionPublicationProvenance(GraphExecutionToken(2, GraphExecutionAttemptId("other-attempt"))),
            )
        )
    with pytest.raises(Graph.ValuePublicationError, match="more than once"):
        frame_index.add_resume_input(AdmittedResumeInput(resume_coordinate, node_input_frame))
    with pytest.raises(Graph.ValuePublicationError, match="more than once"):
        frame_index.add_child_boundary(ConfirmedChildBoundary(boundary_coordinate, graph_output_frame))

    empty_index: ScopedFrameIndex[ConcreteValueTrap] = ScopedFrameIndex()
    with pytest.raises(SnapshotMismatchError, match="no frame"):
        empty_index.lookup(graph_input_coordinate)
    with pytest.raises(SnapshotMismatchError, match="no frame"):
        empty_index.lookup(publication_coordinate)
    with pytest.raises(SnapshotMismatchError, match="no frame"):
        empty_index.lookup(resume_coordinate)
    with pytest.raises(SnapshotMismatchError, match="no frame"):
        empty_index.lookup(boundary_coordinate)


def test_child_projection_coverage_and_variant_guards_fail_closed() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    activation = GraphActivationIdentity(state.run_id, 0, GraphNodeId("nested"))
    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        prepare_frontier(graph, request(graph, state))

    missing = prepare_frontier(graph, request(graph, state, (MissingChild(activation),)))
    assert isinstance(missing, FrontierPreparation)
    assert missing.missing_children == (MissingChild(activation),)
    assert missing.active_children == ()
    child_graph = graph.nested_graphs[GraphNodeId("nested")]
    active = prepare_frontier(graph, request(graph, state, (ActiveChild(activation),)))
    assert isinstance(active, FrontierPreparation)
    assert active.missing_children == ()
    assert active.active_children == (ActiveChild(activation),)
    completed = prepare_frontier(
        graph,
        request(graph, state, (CompletedChild(activation, child_output(child_graph, "output")),)),
    )
    aborted = prepare_frontier(
        graph,
        request(graph, state, (AbortedChild(activation, GraphAbortReason("aborted")),)),
    )
    assert isinstance(completed, FrontierPreparation)
    assert isinstance(aborted, FrontierPreparation)
    assert isinstance(completed.nested_results[0], TaskSuccess)
    assert isinstance(aborted.nested_results[0], TaskFailure)
    assert not hasattr(active.active_children[0], "child_state")
    assert not hasattr(
        request(graph, state, (CompletedChild(activation, child_output(child_graph, "output")),)).child_projections[0],
        "child_state",
    )
    assert not hasattr(
        request(graph, state, (AbortedChild(activation, GraphAbortReason("aborted")),)).child_projections[0],
        "child_state",
    )


@pytest.mark.parametrize(
    "case",
    ["missing", "duplicate", "extra", "noncanonical", "wrong-run", "wrong-step"],
)
def test_child_projection_requires_exact_canonical_parent_coverage(case: str) -> None:
    graph = parallel_nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    a = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("a"))
    b = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("b"))
    wrong_run = GraphActivationIdentity(GraphRunId("other"), state.superstep, GraphNodeId("a"))
    wrong_step = GraphActivationIdentity(state.run_id, state.superstep + 1, GraphNodeId("a"))
    projections = {
        "missing": (MissingChild(a),),
        "duplicate": (MissingChild(a), MissingChild(a)),
        "extra": (MissingChild(a), MissingChild(b), MissingChild(b)),
        "noncanonical": (MissingChild(b), MissingChild(a)),
        "wrong-run": (MissingChild(wrong_run), MissingChild(b)),
        "wrong-step": (MissingChild(wrong_step), MissingChild(b)),
    }[case]

    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        prepare_frontier(graph, request(graph, state, projections))


@pytest.mark.parametrize(
    "coordinate",
    ["run-id", "parent-run", "parent-step", "parent-node", "definition", "version"],
)
def test_child_projection_rejects_each_state_coordinate_mismatch(coordinate: str) -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    activation = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("nested"))
    child_coordinate, stable_activation, child = started_nested_child(
        graph,
        state,
        root_scope_run(state.run_id),
        GraphNodeId("nested"),
    )
    mismatched = {
        "run-id": replace(child, run_id=GraphRunId("forged")),
        "parent-run": replace(child, parent=replace(activation, run_id=GraphRunId("other"))),
        "parent-step": replace(child, parent=replace(activation, superstep=activation.superstep + 1)),
        "parent-node": replace(child, parent=replace(activation, node_id=GraphNodeId("other"))),
        "definition": replace(child, definition_id=GraphDefinitionId("other.child")),
        "version": replace(child, definition_version=GraphDefinitionVersion(2)),
    }[coordinate]
    binding = ChildStateBinding(child_coordinate, stable_activation, mismatched)

    with pytest.raises((GraphStateTransitionError, InvalidExecutionSnapshotError, SnapshotMismatchError)):
        plan_fences(graph, lineage_states(state, (binding,)))


def test_child_projection_validates_terminal_state_before_projecting_variant() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    child_coordinate, stable_activation, child = started_nested_child(
        graph,
        state,
        root_scope_run(state.run_id),
        GraphNodeId("nested"),
    )
    corrupted = replace(child, status=GraphRunStatus.COMPLETED)
    binding = ChildStateBinding(child_coordinate, stable_activation, corrupted)

    with pytest.raises((GraphStateTransitionError, InvalidExecutionSnapshotError), match="canonical empty position"):
        plan_fences(graph, lineage_states(state, (binding,)))


def test_non_nested_frontier_rejects_nonempty_child_projection() -> None:
    graph = compiled_graph("a")
    state = running_state()
    projection = MissingChild(GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("a")))

    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        prepare_frontier(graph, request(graph, state, (projection,)))


def test_running_awaiting_resume_child_remains_active_without_rebuild() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    activation = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("nested"))
    prepared = prepare_frontier(graph, request(graph, state, (ActiveChild(activation),)))

    assert isinstance(prepared, FrontierPreparation)
    assert prepared.missing_children == ()
    assert prepared.active_children == (ActiveChild(activation),)


def test_active_child_must_match_its_compiled_resume_codec() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    child_coordinate, stable_activation, child = started_nested_child(
        graph,
        state,
        root_scope_run(state.run_id),
        GraphNodeId("nested"),
    )
    mismatched = replace(
        child,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("unexpected.input"), 1),
    )
    binding = ChildStateBinding(child_coordinate, stable_activation, mismatched)

    with pytest.raises(SnapshotMismatchError, match="codec"):
        plan_fences(graph, lineage_states(state, (binding,)))


@pytest.mark.asyncio
async def test_scheduler_rejects_empty_duplicate_nested_and_invalid_outcomes() -> None:
    graph = compiled_graph("a")
    scheduler = TaskScheduler(graph)
    with pytest.raises(NodeExecutionContractError, match="no live"):
        await scheduler.next_completion()

    executable = ExecutableTask(
        GraphTask(
            task_identity(GraphRunId("run"), 0, GraphNodeId("a")),
            GraphRunId("run"),
            0,
            GraphNodeId("a"),
        ),
        executable_input(graph, running_state(), "a"),
    )
    scheduler.submit((executable,))
    with pytest.raises(NodeExecutionContractError, match="submitted more than once"):
        scheduler.submit((executable,))
    await scheduler.aclose()

    nested_graph_value = nested_graph()
    nested = TaskScheduler(nested_graph_value)
    nested_state = reduce_graph_run(
        None,
        project_start_graph_command(nested_graph_value, GraphRunId("run")),
    )
    nested.submit(
        (
            ExecutableTask(
                GraphTask(TaskId("nested"), GraphRunId("run"), 0, GraphNodeId("nested")),
                executable_input(nested_graph_value, nested_state, "nested"),
            ),
        )
    )
    nested_event = await nested.next_completion()
    assert isinstance(nested_event, TaskRaised)
    assert isinstance(nested_event.error, NodeExecutionContractError)
    await nested.aclose()

    async def invalid(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(values, route="unexpected")

    invalid_graph = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("invalid.graph"),
            version=GraphDefinitionVersion(1),
            nodes=(string_node("a", invalid),),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    invalid_state = reduce_graph_run(
        None,
        project_start_graph_command(invalid_graph, GraphRunId("run")),
    )
    invalid_scheduler = TaskScheduler(invalid_graph)
    invalid_scheduler.submit(
        (
            ExecutableTask(
                GraphTask(
                    task_identity(GraphRunId("run"), 0, GraphNodeId("a")),
                    GraphRunId("run"),
                    0,
                    GraphNodeId("a"),
                ),
                executable_input(invalid_graph, invalid_state, "a"),
            ),
        )
    )
    invalid_event = await invalid_scheduler.next_completion()
    assert isinstance(invalid_event, TaskRaised)
    assert isinstance(invalid_event.error, InvalidRoutingCommandError)
    await invalid_scheduler.aclose()


@pytest.mark.asyncio
async def test_scheduler_rejects_empty_duplicate_nested_and_untyped_work() -> None:
    graph = compiled_graph("a")
    state = running_state()
    executable = ExecutableTask(
        GraphTask(
            task_identity(state.run_id, state.superstep, GraphNodeId("a")),
            state.run_id,
            state.superstep,
            GraphNodeId("a"),
        ),
        executable_input(graph, state, "a"),
    )
    scheduler = TaskScheduler(graph)

    with pytest.raises(NodeExecutionContractError, match="submitted more than once"):
        scheduler.submit((executable, executable))

    assert scheduler.live_count == 0
    await scheduler.aclose()


@pytest.mark.asyncio
async def test_scheduler_rejects_an_unsupported_callable_return_without_settlement() -> None:
    async def unsupported(_values: Graph.Values[str]) -> bytes:
        return b"unsupported"

    graph = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("unsupported.graph"),
            version=GraphDefinitionVersion(1),
            nodes=(replace(string_node("a"), operation=unsupported),),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    scheduler = TaskScheduler(graph)
    scheduler.submit(
        (
            ExecutableTask(
                GraphTask(TaskId("unsupported"), state.run_id, state.superstep, GraphNodeId("a")),
                executable_input(graph, state, "a"),
            ),
        )
    )

    event = await scheduler.next_completion()

    assert isinstance(event, TaskRaised)
    assert isinstance(event.error, NodeExecutionContractError)
    assert "unsupported outcome" in str(event.error)
    await scheduler.aclose()


@pytest.mark.asyncio
async def test_scheduler_yields_each_canonically_buffered_completion() -> None:
    graph = compiled_graph("a", "b")
    state = running_state(frontier=("a", "b"))
    scheduler = TaskScheduler(graph)
    scheduler.submit(
        tuple(
            ExecutableTask(
                GraphTask(TaskId(node_id), state.run_id, state.superstep, GraphNodeId(node_id)),
                executable_input(graph, state, node_id),
            )
            for node_id in ("a", "b")
        )
    )

    first = await scheduler.next_completion()
    second = await scheduler.next_completion()

    assert isinstance(first, TaskSuccess)
    assert isinstance(second, TaskSuccess)
    assert (first.task.node_id, second.task.node_id) == (GraphNodeId("a"), GraphNodeId("b"))
    await scheduler.aclose()


def test_routing_rejects_invalid_progress_and_partial_join_deadlock() -> None:
    graph = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("join.graph"),
            version=GraphDefinitionVersion(1),
            nodes=(string_node("a"), string_node("b"), string_node("joined")),
            edges=(JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("joined")),),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("joined"),
        (ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, GraphNodeId("a"))),),
    )
    state = replace(
        running_state(definition_id="join.graph", frontier=("a", "b")),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(GraphNodeId("a"), FailedGraphNode(GraphFailure("a")), StartActivationCause()),
                GraphFrontierNode(GraphNodeId("b"), FailedGraphNode(GraphFailure("b")), StartActivationCause()),
            )
        ),
        join_progress=(progress,),
    )
    # A failed frontier is terminal and must never enter the routing phase;
    # join diagnostics are therefore not consulted here.
    with pytest.raises(InvalidRoutingCommandError, match="settled frontier"):
        resolve_routing(graph, state, root_scope_run(state.run_id), ScopedFrameIndex())
    invalid = replace(
        replace(
            state,
            frontier=GraphFrontierState(
                (
                    GraphFrontierNode(
                        GraphNodeId("a"), SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()
                    ),
                    GraphFrontierNode(
                        GraphNodeId("b"), SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()
                    ),
                )
            ),
            status=GraphRunStatus.RUNNING,
        ),
        join_progress=(GraphJoinProgress((GraphNodeId("a"),), GraphNodeId("joined"), ()),),
    )
    with pytest.raises(JoinProgressError):
        resolve_routing(graph, invalid, root_scope_run(state.run_id), ScopedFrameIndex())


def test_conditional_route_to_end_returns_standalone_completion_command() -> None:
    graph = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("conditional.end"),
            version=GraphDefinitionVersion(1),
            nodes=(string_node("a"),),
            edges=(ConditionalEdge(GraphNodeId("a"), GraphRouteId("done"), END),),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    state = replace(
        running_state(revision=7, definition_id="conditional.end"),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("a"), SucceededGraphNode(SelectGraphRoute(GraphRouteId("done"))), StartActivationCause()
                ),
            )
        ),
    )
    command = resolve_routing(
        graph,
        state,
        root_scope_run(state.run_id),
        ScopedFrameIndex(),
    )
    assert command.expected_revision == 7


def test_snapshot_guard_rejects_unknown_and_mismatched_resource_participants() -> None:
    resource = ResourceId("file")
    database = ResourceId("database")
    graph = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("resource.graph"),
            version=GraphDefinitionVersion(1),
            nodes=(string_node("a", resources=(resource,)),),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
            resources=(ResourceDefinition(resource), ResourceDefinition(database)),
        )
    )
    state = running_state(definition_id="resource.graph")
    token = GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))
    active = replace(state, execution_sequence=1, execution=GraphExecutionLease(token))
    with pytest.raises(InvalidExecutionSnapshotError, match="compiled resource"):
        require_snapshot_matches_graph(graph, active)

    mismatched = ResourceSnapshot(
        (ResourceLock(resource), ResourceLock(database, GraphNodeId("a"))),
        (ResourceAcquisition(GraphNodeId("a"), (database,), (database,)),),
    )
    with pytest.raises(InvalidExecutionSnapshotError, match="exactly match"):
        require_snapshot_matches_graph(graph, replace(active, resources=mismatched))

    free_graph = compiled_graph("a")
    with pytest.raises(InvalidExecutionSnapshotError, match="resource-free"):
        require_snapshot_matches_graph(
            free_graph,
            replace(active, definition_id=free_graph.definition_id, resources=mismatched),
        )

    unknown = replace(
        running_state(),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("unknown"), PendingGraphNode(UseStepRequestInput()), StartActivationCause()
                ),
            )
        ),
    )
    with pytest.raises(InvalidExecutionSnapshotError, match="unknown nodes"):
        require_snapshot_matches_graph(compiled_graph("a"), unknown)


def test_snapshot_guard_admits_only_compiled_activation_gates() -> None:
    graph = compiled_graph(
        "a",
        "b",
        entries=("a",),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")),),
    )
    base = running_state(superstep=1, frontier=("b",))

    valid = replace(
        base,
        settled_activations=(
            ActivationReference(
                GraphActivationIdentity(base.run_id, 0, GraphNodeId("a")),
            ),
        ),
        frontier=GraphFrontierState(
            (
                replace(
                    base.frontier.nodes[0],
                    cause=RoutedActivationCause(
                        (
                            ActivationReference(
                                GraphActivationIdentity(base.run_id, 0, GraphNodeId("a")),
                            ),
                        )
                    ),
                ),
            )
        ),
    )
    require_snapshot_matches_graph(graph, valid)

    forged_source = replace(
        valid,
        settled_activations=(
            *valid.settled_activations,
            ActivationReference(
                GraphActivationIdentity(valid.run_id, 0, GraphNodeId("ghost")),
            ),
        ),
        frontier=GraphFrontierState(
            (
                replace(
                    valid.frontier.nodes[0],
                    cause=RoutedActivationCause(
                        (
                            ActivationReference(
                                GraphActivationIdentity(valid.run_id, 0, GraphNodeId("ghost")),
                            ),
                        )
                    ),
                ),
            )
        ),
    )
    forged_route = replace(
        valid,
        settled_activations=(
            ActivationReference(
                GraphActivationIdentity(valid.run_id, 0, GraphNodeId("a")),
                GraphRouteId("bogus"),
            ),
        ),
        frontier=GraphFrontierState(
            (
                replace(
                    valid.frontier.nodes[0],
                    cause=RoutedActivationCause(
                        (
                            ActivationReference(
                                GraphActivationIdentity(valid.run_id, 0, GraphNodeId("a")),
                                GraphRouteId("bogus"),
                            ),
                        )
                    ),
                ),
            )
        ),
    )

    for forged in (forged_source, forged_route):
        with pytest.raises(
            InvalidExecutionSnapshotError,
            match=r"unknown node|selected route|activation gate|settlement evidence",
        ):
            require_snapshot_matches_graph(graph, forged)


def test_snapshot_guard_rejects_a_ghost_settled_activation_even_when_the_frontier_is_valid() -> None:
    graph = compiled_graph(
        "a",
        "b",
        entries=("a",),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")),),
    )
    base = running_state(superstep=1, frontier=("b",))
    predecessor = ActivationReference(
        GraphActivationIdentity(base.run_id, 0, GraphNodeId("a")),
    )
    ghost = ActivationReference(
        GraphActivationIdentity(base.run_id, 0, GraphNodeId("ghost")),
    )
    state = replace(
        base,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("b"),
                    PendingGraphNode(UseStepRequestInput()),
                    RoutedActivationCause((predecessor,)),
                ),
            )
        ),
        settled_activations=tuple(sorted((predecessor, ghost), key=ActivationReference.canonical_key)),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match="settled activation references unknown node 'ghost'"):
        require_snapshot_matches_graph(graph, state)


def test_snapshot_guard_rejects_a_settled_route_not_declared_by_the_compiled_node() -> None:
    graph = compiled_graph(
        "a",
        "b",
        entries=("a",),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")),),
    )
    base = running_state(superstep=1, frontier=("b",))
    bogus = ActivationReference(
        GraphActivationIdentity(base.run_id, 0, GraphNodeId("a")),
        GraphRouteId("bogus"),
    )
    state = replace(
        base,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("b"),
                    SucceededGraphNode(ContinueGraphRouting()),
                    RoutedActivationCause((bogus,)),
                ),
            )
        ),
        settled_activations=(bogus,),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match="selected route"):
        require_snapshot_matches_graph(graph, state)


@pytest.mark.parametrize(
    ("route", "message"),
    [(None, "lacks its selected route"), ("bogus", "selected an unknown route")],
)
def test_snapshot_guard_rejects_invalid_conditional_settled_routes(
    route: str | None,
    message: str,
) -> None:
    graph = compiled_graph(
        "a",
        "b",
        entries=("a",),
        edges=(ConditionalEdge(GraphNodeId("a"), GraphRouteId("ok"), GraphNodeId("b")),),
    )
    base = running_state(superstep=1, frontier=("b",))
    reference = ActivationReference(
        GraphActivationIdentity(base.run_id, 0, GraphNodeId("a")),
        GraphRouteId(route) if route is not None else None,
    )
    state = replace(
        base,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("b"),
                    PendingGraphNode(UseStepRequestInput()),
                    RoutedActivationCause((reference,)),
                ),
            )
        ),
        settled_activations=(reference,),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match=message):
        require_snapshot_matches_graph(graph, state)


def test_snapshot_guard_rejects_a_ghost_ledger_on_a_terminal_state() -> None:
    graph = compiled_graph(
        "a",
        "b",
        entries=("a",),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")),),
    )
    state = running_state(superstep=1, frontier=("b",))
    ghost = ActivationReference(
        GraphActivationIdentity(state.run_id, 0, GraphNodeId("ghost")),
    )
    terminal = replace(
        state,
        status=GraphRunStatus.FAILED,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("b"),
                    FailedGraphNode(GraphFailure("failed")),
                    RoutedActivationCause((ghost,)),
                ),
            )
        ),
        settled_activations=(ghost,),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match="unknown node 'ghost'"):
        require_snapshot_matches_graph(graph, terminal)


@pytest.mark.asyncio
async def test_public_state_recovery_rejects_a_ghost_ledger_before_any_callable() -> None:
    calls = {"source": 0, "target": 0}

    async def source(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["source"] += 1
        return Graph.values()

    async def target(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["target"] += 1
        return Graph.values()

    graph = Graph[str]("public.ghost-ledger")
    graph.add_node("source", source, inputs={}, outputs={})
    graph.add_node("target", target, inputs={}, outputs={})
    graph.add_edge("source", "target")
    graph.set_outputs({})

    state = running_state(
        definition_id="public.ghost-ledger",
        superstep=1,
        frontier=("target",),
    )
    ghost = ActivationReference(
        GraphActivationIdentity(state.run_id, 0, GraphNodeId("ghost")),
    )
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("target"),
                    PendingGraphNode(UseStepRequestInput()),
                    RoutedActivationCause((ghost,)),
                ),
            )
        ),
        settled_activations=(ghost,),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match="unknown node 'ghost'"):
        await graph.run(state=state)
    assert calls == {"source": 0, "target": 0}


def test_snapshot_guard_rejects_a_start_activation_outside_compiled_entries() -> None:
    graph = compiled_graph(
        "a",
        "b",
        entries=("a",),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")),),
    )
    state = running_state(frontier=("b",))

    with pytest.raises(InvalidExecutionSnapshotError, match="initial frontier"):
        require_snapshot_matches_graph(graph, state)


def test_compiler_emits_canonical_activation_gates_for_join_and_conditional_routes() -> None:
    first = compile_graph(
        GraphDefinition(
            GraphDefinitionId("gate.graph"),
            GraphDefinitionVersion(1),
            (string_node("a"), string_node("b"), string_node("c"), string_node("d")),
            (
                ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("d")),
                JoinEdge((GraphNodeId("b"), GraphNodeId("a")), GraphNodeId("c")),
            ),
            (),
            normalize_graph_output_declarations({}),
        )
    )
    second = compile_graph(
        GraphDefinition(
            GraphDefinitionId("gate.graph"),
            GraphDefinitionVersion(1),
            (string_node("d"), string_node("c"), string_node("b"), string_node("a")),
            (
                JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("c")),
                ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("d")),
            ),
            (),
            normalize_graph_output_declarations({}),
        )
    )

    assert dict(first.transition.activation_gates) == dict(second.transition.activation_gates)
    assert first.transition.activation_gates[GraphNodeId("c")] == (
        ((GraphNodeId("a"), frozenset({GraphRouteId("right")})), (GraphNodeId("b"), frozenset({None}))),
    )
    assert first.transition.activation_gates[GraphNodeId("d")] == (
        ((GraphNodeId("a"), frozenset({GraphRouteId("right")})),),
    )


def test_scoped_snapshot_guard_rejects_a_parent_activation_outside_the_compiled_scope() -> None:
    parent = GraphActivationIdentity(GraphRunId("parent"), 0, GraphNodeId("other"))
    child = replace(
        running_state(definition_id="boundary.child", frontier=("child",)),
        run_id=child_graph_run_id(parent.run_id, parent.superstep, parent.node_id),
        parent=parent,
    )
    compiled_child = nested_graph().nested_graphs[GraphNodeId("nested")]
    with pytest.raises(SnapshotMismatchError, match="compiled definition scope"):
        require_scoped_snapshot_matches_graph(
            compiled_child,
            child,
            ScopeRunCoordinate(compiled_child.definition_scope, child.run_id),
        )


def test_session_request_validation_rejects_a_claim_with_the_wrong_task_scope() -> None:
    graph = compiled_graph("a", "b", entries=("a", "b"))
    state = running_state(frontier=("a", "b"))
    executor = GraphExecutor(graph)
    execution_request = request(graph, state)
    prepared = executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    forged = replace(claimed, revision=claimed.revision + 1)

    for _ in range(2):
        with pytest.raises(ResultCollectionError, match="committed graph state"):
            executor.issue_session(prepared.claim, forged)


def test_session_request_validation_rejects_a_claim_that_still_has_an_active_child() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    activation = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("nested"))
    disposition = GraphExecutor(graph).prepare(request(graph, state, (ActiveChild(activation),)))

    assert isinstance(disposition, WaitingForChildren)
    assert disposition.active == (ActiveChild(activation),)


@pytest.mark.asyncio
async def test_consumed_claim_receipt_can_issue_only_one_session() -> None:
    graph = compiled_graph("a")
    state = running_state()
    owner, prepared = prepare_execution_frontier(graph, state)
    claimed = reduce_graph_run(state, prepared.claim.command)
    assert claimed.execution is not None
    forged = replace(
        claimed,
        execution=GraphExecutionLease(
            GraphExecutionToken(
                claimed.execution.token.generation,
                GraphExecutionAttemptId("forged"),
            )
        ),
    )
    with pytest.raises(ResultCollectionError, match="committed graph state"):
        prepared.claim.consume(owner, forged)

    receipt = prepared.claim.consume(owner, claimed)
    session = issue_execution_session(graph, receipt)
    try:
        with pytest.raises(ResultCollectionError, match="already issued"):
            issue_execution_session(graph, receipt)
    finally:
        await session.aclose()


def test_claim_receipt_requires_exact_owner_identity() -> None:
    graph = compiled_graph("a")
    state = running_state()
    _owner, prepared = prepare_execution_frontier(graph, state)
    claimed = reduce_graph_run(state, prepared.claim.command)

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        prepared.claim.consume(ExecutionClaimOwner(), claimed)


def test_missing_child_takes_priority_over_an_active_sibling() -> None:
    graph = parallel_nested_graph()
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    a = GraphActivationIdentity(parent.run_id, parent.superstep, GraphNodeId("a"))
    b = GraphActivationIdentity(parent.run_id, parent.superstep, GraphNodeId("b"))
    both_missing = executor.prepare(request(graph, parent, (MissingChild(a), MissingChild(b))))
    assert isinstance(both_missing, WaitingForChildren)
    assert both_missing.missing == (MissingChild(a), MissingChild(b))

    disposition = executor.prepare(request(graph, parent, (MissingChild(a), ActiveChild(b))))

    assert isinstance(disposition, WaitingForChildren)
    assert disposition.missing == (MissingChild(a),)
    assert disposition.active == (ActiveChild(b),)

    reverse = executor.prepare(request(graph, parent, (ActiveChild(a), MissingChild(b))))

    assert isinstance(reverse, WaitingForChildren)
    assert reverse.missing == (MissingChild(b),)
    assert reverse.active == (ActiveChild(a),)


@pytest.mark.parametrize("variant", ["completed", "failed", "aborted"])
def test_terminal_child_projects_its_matching_parent_result_variant(variant: str) -> None:
    graph = nested_graph()
    parent = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    activation = GraphActivationIdentity(parent.run_id, parent.superstep, GraphNodeId("nested"))
    if variant == "completed":
        projection: ChildProjection[str] = CompletedChild(
            activation,
            child_output(graph.nested_graphs[GraphNodeId("nested")], "child-output"),
        )
        result_type = TaskSuccess
    elif variant == "failed":
        projection = FailedChild(activation, "child failed")
        result_type = TaskFailure
    else:
        projection = AbortedChild(activation, GraphAbortReason("child aborted"))
        result_type = TaskFailure

    prepared = prepare_frontier(graph, request(graph, parent, (projection,)))

    assert isinstance(prepared, FrontierPreparation)
    assert len(prepared.nested_results) == 1
    assert isinstance(prepared.nested_results[0], result_type)


def test_mixed_completed_and_aborted_children_keep_canonical_parent_order() -> None:
    graph = parallel_nested_graph()
    parent = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    a = GraphActivationIdentity(parent.run_id, parent.superstep, GraphNodeId("a"))
    b = GraphActivationIdentity(parent.run_id, parent.superstep, GraphNodeId("b"))
    child_graph = graph.nested_graphs[GraphNodeId("a")]

    prepared = prepare_frontier(
        graph,
        request(
            graph,
            parent,
            (
                CompletedChild(a, child_output(child_graph, "a-output")),
                AbortedChild(b, GraphAbortReason("child aborted")),
            ),
        ),
    )

    assert isinstance(prepared, FrontierPreparation)
    assert tuple(type(result) for result in prepared.nested_results) == (TaskSuccess, TaskFailure)
    assert tuple(result.task.node_id for result in prepared.nested_results) == (
        GraphNodeId("a"),
        GraphNodeId("b"),
    )


def test_planner_claims_only_pending_nodes_from_a_mixed_frontier() -> None:
    graph = compiled_graph("a", "b", "c", entries=("a", "b", "c"))
    state = running_state(frontier=("a", "b", "c"))
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(GraphNodeId("a"), SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()),
                GraphFrontierNode(GraphNodeId("b"), FailedGraphNode(GraphFailure("failed")), StartActivationCause()),
                state.frontier.nodes[2],
            )
        ),
    )

    tasks = plan_tasks(graph, state, ExecutionLimits())

    assert tuple(task.node_id for task in tasks) == (GraphNodeId("c"),)


@pytest.mark.asyncio
async def test_family_driver_projects_an_acknowledged_aborted_child() -> None:
    graph = nested_graph()
    parent = running_state(definition_id=graph.definition_id, frontier=("nested",), run_id="parent-run")
    scope_run = root_scope_run(parent.run_id)
    parent_activation = GraphActivationIdentity(parent.run_id, parent.superstep, GraphNodeId("nested"))
    child_coordinate = child_scope_run_for_activation(scope_run, parent_activation)
    child_graph = graph.nested_graphs[GraphNodeId("nested")]
    child = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_coordinate.graph_run_id, parent_activation),
    )
    aborted = reduce_graph_run(child, AbortGraphRun(child.revision, GraphAbortReason("child aborted")))
    binding = ChildStateBinding(
        child_coordinate,
        StableActivation(scope_run, 0, GraphNodeId("nested")),
        aborted,
    )
    root, _evidence_reader = await family_driver_module.admit_continued_root(
        graph,
        parent,
        (binding,),
        ScopedFrameIndex(),
        ExecutionLimits(),
        None,
        (),
        (),
        _CompiledFamilyIdentity(),
        recovered=True,
    )

    disposition = await root.drive_quantum()

    assert isinstance(disposition, FailedGraph)
    assert root.state.status is GraphRunStatus.FAILED
    settlement = root.state.frontier.nodes[0].settlement
    assert isinstance(settlement, FailedGraphNode)
    assert settlement.failure == "child aborted"
    await root.release()
