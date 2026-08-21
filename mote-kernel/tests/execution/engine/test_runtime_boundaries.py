"""Fail-closed boundaries retained by the scoped-frame execution runtime."""

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.driver import step_request
from tests.execution.engine.factories import compiled_graph, running_state

import mote_kernel.execution.family_driver as family_driver_module
from mote_kernel.execution import Graph
from mote_kernel.execution.claim import ConsumedExecutionClaim, ExecutionClaimOwner
from mote_kernel.execution.engine.claim_stage import prepare_claim, require_claim_tasks
from mote_kernel.execution.engine.frontier import prepare_frontier
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.resume_admission import ScopedResumeCandidate, admit_resume_candidates
from mote_kernel.execution.engine.resume_input import (
    encode_resume_input,
    materialize_node_input,
    require_resume_input_binding,
)
from mote_kernel.execution.engine.routing import resolve_routing
from mote_kernel.execution.engine.scheduler import TaskRaised, TaskScheduler
from mote_kernel.execution.engine.session import issue_execution_session
from mote_kernel.execution.engine.settlement import settle_result
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.superstep import validate_execution_session_request
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask, TaskId, task_identity
from mote_kernel.execution.errors import (
    FrameInstallationInvariantError,
    InvalidExecutionSnapshotError,
    InvalidRoutingCommandError,
    JoinProgressError,
    NodeExecutionContractError,
    ResultCollectionError,
    RoutingDeadlockError,
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
    canonical_nominal_type,
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
    _frame_value,
    _make_graph_input_frame,
    _make_graph_output_view,
    _make_node_input_frame,
    _make_node_output_frame,
)
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import (
    ExecutionRequestAttemptId,
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.execution.invocation import (
    _PlannedResume,  # pyright: ignore[reportPrivateUsage]
    install_confirmed_resume_frames,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.execution.result import (
    AbortedChild,
    ActiveChild,
    ChildProjection,
    CompletedChild,
    MissingChild,
    PreparedNestedRun,
    PreparedResume,
    StartMissingChildren,
    TaskFailure,
    TaskSuccess,
    WaitForActiveChildren,
    WaitingForChildren,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedResumeInput,
    AdmittedSubstitution,
    CandidateFrameAvailability,
    ChildBoundaryAvailabilityCoordinate,
    ChildStateBinding,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
    SkipSubstitutionProvenance,
    _new_context,
    _new_family_identity,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphAbortReason,
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
    GraphSkipReason,
    GraphStateTransitionError,
    OverrideGraphNodeInput,
    ParentGraphActivation,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    ResumeGraphNodes,
    SelectGraphRoute,
    SettleGraphNode,
    SkipFailedNode,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
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
    declarations = tuple((item.name, item.descriptor) for item in graph.graph_output_descriptor.declarations.entries)
    return _make_graph_output_view((NamedValue("value", value),), declarations)


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


def test_claim_task_guard_rejects_forged_rebuild() -> None:
    graph = compiled_graph("a")
    state = running_state()
    tasks = plan_tasks(graph, state, ExecutionLimits())
    claim = prepare_claim(
        ExecutionClaimOwner(),
        state,
        ExecutionRequestAttemptId("request"),
        tasks,
        None,
    )
    assert not claim.consumed
    forged = (replace(tasks[0], task_id=TaskId("forged")),)

    with pytest.raises(ResultCollectionError, match="tasks do not match"):
        require_claim_tasks(claim, forged)


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
    descriptor = canonical_nominal_type(ConcreteValueTrap)
    declarations = (("value", descriptor),)
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
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, 0, GraphNodeId("nested"))
    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        prepare_frontier(graph, request(graph, state))

    missing = prepare_frontier(graph, request(graph, state, (MissingChild(activation),)))
    child_graph = graph.nested_graphs[GraphNodeId("nested")]
    child = reduce_graph_run(None, missing.missing_children[0].command)
    mismatched = replace(child, definition_id=GraphDefinitionId("other.child"))
    with pytest.raises(ResultCollectionError, match="parent activation or definition"):
        prepare_frontier(graph, request(graph, state, (ActiveChild(activation, mismatched),)))

    completed = completed_child(child)
    with pytest.raises(ResultCollectionError, match="running child"):
        prepare_frontier(graph, request(graph, state, (ActiveChild(activation, completed),)))
    with pytest.raises(ResultCollectionError, match="completed child"):
        prepare_frontier(
            graph,
            request(graph, state, (CompletedChild(activation, child, child_output(child_graph, "output")),)),
        )
    with pytest.raises(ResultCollectionError, match="aborted child"):
        prepare_frontier(graph, request(graph, state, (AbortedChild(activation, child),)))


@pytest.mark.parametrize(
    "case",
    ["missing", "duplicate", "extra", "noncanonical", "wrong-run", "wrong-step"],
)
def test_child_projection_requires_exact_canonical_parent_coverage(case: str) -> None:
    graph = parallel_nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    a = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("a"))
    b = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("b"))
    wrong_run = ParentGraphActivation(GraphRunId("other"), state.superstep, GraphNodeId("a"))
    wrong_step = ParentGraphActivation(state.run_id, state.superstep + 1, GraphNodeId("a"))
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
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    prepared = prepare_frontier(graph, request(graph, state, (MissingChild(activation),)))
    child = reduce_graph_run(None, prepared.missing_children[0].command)
    mismatched = {
        "run-id": replace(child, run_id=GraphRunId("forged")),
        "parent-run": replace(child, parent=replace(activation, run_id=GraphRunId("other"))),
        "parent-step": replace(child, parent=replace(activation, superstep=activation.superstep + 1)),
        "parent-node": replace(child, parent=replace(activation, node_id=GraphNodeId("other"))),
        "definition": replace(child, definition_id=GraphDefinitionId("other.child")),
        "version": replace(child, definition_version=GraphDefinitionVersion(2)),
    }[coordinate]
    identity_coordinates = {"run-id", "parent-run", "parent-step", "parent-node"}
    error = GraphStateTransitionError if coordinate in identity_coordinates else ResultCollectionError

    with pytest.raises(error):
        prepare_frontier(graph, request(graph, state, (ActiveChild(activation, mismatched),)))


def test_child_projection_validates_terminal_state_before_projecting_variant() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    prepared = prepare_frontier(graph, request(graph, state, (MissingChild(activation),)))
    child = reduce_graph_run(None, prepared.missing_children[0].command)
    corrupted = replace(child, status=GraphRunStatus.COMPLETED)
    child_graph = graph.nested_graphs[GraphNodeId("nested")]

    with pytest.raises(GraphStateTransitionError, match="canonical empty position"):
        prepare_frontier(
            graph,
            request(
                graph,
                state,
                (CompletedChild(activation, corrupted, child_output(child_graph, "output")),),
            ),
        )


def test_non_nested_frontier_rejects_nonempty_child_projection() -> None:
    graph = compiled_graph("a")
    state = running_state()
    projection = MissingChild(ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("a")))

    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        prepare_frontier(graph, request(graph, state, (projection,)))


def test_running_awaiting_resume_child_remains_active_without_rebuild() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(graph, request(graph, state, (MissingChild(activation),)))
    child = reduce_graph_run(None, missing.missing_children[0].command)
    awaiting = replace(
        child,
        frontier=GraphFrontierState(
            (GraphFrontierNode(GraphNodeId("child"), FailedGraphNode(GraphFailure("failed"))),)
        ),
    )

    prepared = prepare_frontier(graph, request(graph, state, (ActiveChild(activation, awaiting),)))

    assert prepared.missing_children == ()
    assert prepared.active_children == (ActiveChild(activation, awaiting),)


def test_active_child_must_match_its_compiled_resume_codec() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(graph, request(graph, state, (MissingChild(activation),)))
    child = reduce_graph_run(None, missing.missing_children[0].command)
    mismatched = replace(
        child,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("unexpected.input"), 1),
    )

    with pytest.raises(SnapshotMismatchError, match="codec"):
        prepare_frontier(graph, request(graph, state, (ActiveChild(activation, mismatched),)))


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
        GraphExecutor(nested_graph_value).start_command(GraphRunId("run")),
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
        GraphExecutor(invalid_graph).start_command(GraphRunId("run")),
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
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
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


def test_child_wait_payloads_require_nonempty_canonical_parents() -> None:
    graph = nested_graph()
    state = running_state(definition_id="boundary.parent", frontier=("nested",))
    activation = ParentGraphActivation(state.run_id, 0, GraphNodeId("nested"))
    compiled_child = graph.nested_graphs[GraphNodeId("nested")]
    command = project_start_graph_command(
        compiled_child,
        child_graph_run_id(state.run_id, state.superstep, activation.node_id),
        activation,
    )
    prepared = PreparedNestedRun(activation, compiled_child, command)
    active = ActiveChild(activation, reduce_graph_run(None, command))

    with pytest.raises(ValueError, match="non-empty"):
        StartMissingChildren[str](())
    with pytest.raises(ValueError, match="non-empty"):
        WaitForActiveChildren(())
    with pytest.raises(ValueError, match="canonical"):
        StartMissingChildren((prepared, prepared))
    with pytest.raises(ValueError, match="canonical"):
        WaitForActiveChildren((active, active))


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
        frozenset({GraphNodeId("a")}),
    )
    state = replace(
        running_state(definition_id="join.graph", frontier=("a", "b")),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(GraphNodeId("a"), FailedGraphNode(GraphFailure("a"))),
                GraphFrontierNode(GraphNodeId("b"), FailedGraphNode(GraphFailure("b"))),
            )
        ),
        join_progress=(progress,),
    )
    with pytest.raises(RoutingDeadlockError):
        resolve_routing(graph, state, root_scope_run(state.run_id), ScopedFrameIndex())
    invalid = replace(
        state,
        join_progress=(GraphJoinProgress((GraphNodeId("a"),), GraphNodeId("joined"), frozenset()),),
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
            (GraphFrontierNode(GraphNodeId("a"), SucceededGraphNode(SelectGraphRoute(GraphRouteId("done")))),)
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
            resources=(ResourceDefinition(resource, 0), ResourceDefinition(database, 1)),
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
            (GraphFrontierNode(GraphNodeId("unknown"), PendingGraphNode(UseStepRequestInput())),)
        ),
    )
    with pytest.raises(InvalidExecutionSnapshotError, match="unknown nodes"):
        require_snapshot_matches_graph(compiled_graph("a"), unknown)


def test_snapshot_guard_rejects_a_parent_activation_not_declared_by_the_executor() -> None:
    parent = ParentGraphActivation(GraphRunId("parent"), 0, GraphNodeId("nested"))
    child = replace(
        running_state(definition_id="boundary.child", frontier=("child",)),
        run_id=child_graph_run_id(parent.run_id, parent.superstep, parent.node_id),
        parent=parent,
    )
    compiled_child = nested_graph().nested_graphs[GraphNodeId("nested")]
    with pytest.raises(InvalidExecutionSnapshotError, match="parent activation"):
        require_snapshot_matches_graph(
            compiled_child,
            child,
            frozenset({((compiled_child.definition_id, compiled_child.version), GraphNodeId("other"))}),
        )


def test_session_request_validation_rejects_a_claim_with_the_wrong_task_scope() -> None:
    graph = compiled_graph("a", "b", entries=("a", "b"))
    state = running_state(frontier=("a", "b"))
    tasks = plan_tasks(graph, state, ExecutionLimits())
    claim = prepare_claim(
        ExecutionClaimOwner(),
        state,
        ExecutionRequestAttemptId("request"),
        tasks[:1],
        None,
    )
    with pytest.raises(ResultCollectionError, match="tasks do not match"):
        validate_execution_session_request(graph, request(graph, state), claim)


def test_session_request_validation_rejects_a_claim_that_still_has_an_active_child() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(graph, request(graph, state, (MissingChild(activation),)))
    child = reduce_graph_run(None, missing.missing_children[0].command)
    tasks = plan_tasks(graph, state, ExecutionLimits())
    claim = prepare_claim(
        ExecutionClaimOwner(),
        state,
        ExecutionRequestAttemptId("request"),
        tasks,
        None,
    )
    with pytest.raises(ResultCollectionError, match="cannot wait for children"):
        validate_execution_session_request(
            graph,
            request(graph, state, (ActiveChild(activation, child),)),
            claim,
        )


@pytest.mark.asyncio
async def test_consumed_claim_receipt_can_issue_only_one_session() -> None:
    graph = compiled_graph("a")
    state = running_state()
    owner = ExecutionClaimOwner()
    request_id = ExecutionRequestAttemptId("request")
    tasks = plan_tasks(graph, state, ExecutionLimits())
    claim = prepare_claim(owner, state, request_id, tasks, None)
    claimed = reduce_graph_run(state, claim.command)
    receipt = await claim.consume(owner, claimed, request_id)
    execution_request = replace(
        request(graph, claimed),
        request_attempt_id=request_id,
    )
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
        issue_execution_session(graph, replace(execution_request, state=forged), receipt)

    session = issue_execution_session(graph, execution_request, receipt)
    try:
        with pytest.raises(ResultCollectionError, match="already issued"):
            issue_execution_session(graph, execution_request, receipt)
    finally:
        await session.aclose()


def test_consumed_claim_receipt_cannot_be_constructed_directly() -> None:
    graph = compiled_graph("a")
    state = running_state()
    owner = ExecutionClaimOwner()
    request_id = ExecutionRequestAttemptId("request")
    claim = prepare_claim(
        owner,
        state,
        request_id,
        plan_tasks(graph, state, ExecutionLimits()),
        None,
    )

    with pytest.raises(TypeError, match="issued only"):
        cast(Callable[..., object], ConsumedExecutionClaim)(object(), claim.snapshot)


@pytest.mark.asyncio
async def test_claim_receipt_requires_exact_owner_identity() -> None:
    graph = compiled_graph("a")
    state = running_state()
    owner = ExecutionClaimOwner()
    request_id = ExecutionRequestAttemptId("request")
    claim = prepare_claim(owner, state, request_id, plan_tasks(graph, state, ExecutionLimits()), None)
    claimed = reduce_graph_run(state, claim.command)

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await claim.consume(ExecutionClaimOwner(), claimed, request_id)


def completed_child(state: GraphRunState) -> GraphRunState:
    claimed = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("child-attempt"), None),
    )
    assert claimed.execution is not None
    settled = reduce_graph_run(
        claimed,
        SettleGraphNode(
            claimed.revision,
            claimed.execution.token,
            SucceededGraphNodeOutcome(GraphNodeId("child"), ContinueGraphRouting()),
        ),
    )
    return reduce_graph_run(settled, CompleteGraphFrontier(settled.revision))


@pytest.mark.asyncio
async def test_missing_child_takes_priority_over_an_active_sibling() -> None:
    graph = parallel_nested_graph()
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    a = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("a"))
    b = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("b"))
    both_missing = await executor.prepare(request(graph, parent, (MissingChild(a), MissingChild(b))))
    assert isinstance(both_missing, WaitingForChildren)
    assert isinstance(both_missing.action, StartMissingChildren)
    active_b = reduce_graph_run(None, both_missing.action.children[1].command)

    disposition = await executor.prepare(request(graph, parent, (MissingChild(a), ActiveChild(b, active_b))))

    assert isinstance(disposition, WaitingForChildren)
    assert isinstance(disposition.action, StartMissingChildren)
    assert tuple(child.parent for child in disposition.action.children) == (a,)


@pytest.mark.parametrize("variant", ["completed", "aborted"])
def test_terminal_child_projects_its_matching_parent_result_variant(variant: str) -> None:
    graph = nested_graph()
    parent = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(graph, request(graph, parent, (MissingChild(activation),)))
    child = reduce_graph_run(None, missing.missing_children[0].command)
    if variant == "completed":
        terminal = completed_child(child)
        projection: ChildProjection[str] = CompletedChild(
            activation,
            terminal,
            child_output(graph.nested_graphs[GraphNodeId("nested")], "child-output"),
        )
        result_type = TaskSuccess
    else:
        terminal = reduce_graph_run(child, AbortGraphRun(child.revision, GraphAbortReason("child aborted")))
        projection = AbortedChild(activation, terminal)
        result_type = TaskFailure

    prepared = prepare_frontier(graph, request(graph, parent, (projection,)))

    assert len(prepared.nested_results) == 1
    assert isinstance(prepared.nested_results[0], result_type)


def test_terminal_aborted_child_remains_unchanged_while_parent_boundary_substitution_is_admitted() -> None:
    graph = nested_graph(with_consumer=True)
    parent = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(graph, request(graph, parent, (MissingChild(activation),)))
    child = reduce_graph_run(None, missing.missing_children[0].command)
    aborted = reduce_graph_run(child, AbortGraphRun(child.revision, GraphAbortReason("child aborted")))
    prepared = prepare_frontier(graph, request(graph, parent, (AbortedChild(activation, aborted),)))
    claimed = reduce_graph_run(
        parent,
        ClaimGraphExecution(parent.revision, GraphExecutionAttemptId("parent-attempt"), None),
    )
    assert claimed.execution is not None
    failed = reduce_graph_run(claimed, settle_result(graph, claimed, prepared.nested_results[0]))
    failed = replace(failed, execution=None)
    scope_run = root_scope_run(parent.run_id)
    publication = graph.publications[GraphNodeId("nested")]
    declarations = tuple(
        (declaration.name, declaration.descriptor) for declaration in publication.descriptor.declarations.entries
    )
    command = ResumeGraphNodes(
        failed.revision,
        (
            SkipFailedNode(
                GraphNodeId("nested"),
                GraphSkipReason("boundary replacement"),
                ContinueGraphRouting(),
            ),
        ),
    )
    successor = reduce_graph_run(failed, command)
    substitution = AdmittedSubstitution(
        PublicationAvailabilityCoordinate(
            StableActivation(scope_run, failed.superstep, GraphNodeId("nested")),
            publication.descriptor.identity,
        ),
        _make_node_output_frame(Graph.values(value="replacement"), declarations),
        SkipSubstitutionProvenance(),
        successor.revision,
    )

    availability = admit_resume_candidates(
        (
            ScopedResumeCandidate(
                graph,
                scope_run,
                failed,
                successor,
                (substitution,),
                command,
            ),
        ),
        request(graph, parent).frames,
    )

    assert aborted.status is GraphRunStatus.ABORTED
    with pytest.raises(FrameInstallationInvariantError, match="admitted successor"):
        install_confirmed_resume_frames(
            request(graph, parent).frames,
            _PlannedResume(
                scope_run,
                successor,
                PreparedResume(command, (), ()),
                (substitution,),
            ),
            failed,
        )
    assert aborted.abort is not None and aborted.abort.reason == "child aborted"
    assert availability.has_publication(substitution.coordinate)
    installed = install_confirmed_resume_frames(
        request(graph, parent).frames,
        _PlannedResume(
            scope_run,
            successor,
            PreparedResume(command, (), ()),
            (substitution,),
        ),
        successor,
    )
    confirmed = installed.lookup(substitution.coordinate)
    assert confirmed.coordinate == substitution.coordinate
    assert confirmed.frame is substitution.frame
    assert confirmed.provenance is substitution.provenance
    assert confirmed.acknowledged_revision == substitution.expected_revision == successor.revision
    assert _frame_value(confirmed.frame, "value") == "replacement"
    routed = reduce_graph_run(successor, resolve_routing(graph, successor, scope_run, installed))
    materialized = materialize_node_input(graph, routed, scope_run, installed, GraphNodeId("consumer"))
    assert _frame_value(materialized, "value") == "replacement"
    assert substitution.coordinate.activation.scope_run == scope_run
    assert substitution.coordinate.activation.node_id == GraphNodeId("nested")
    assert substitution.coordinate.activation.node_id != GraphNodeId("child")
    assert aborted.status is GraphRunStatus.ABORTED


def test_repeated_child_activations_isolate_parent_boundary_substitutions() -> None:
    graph = nested_graph(with_consumer=True)
    scope_run = root_scope_run(GraphRunId("repeated-parent"))
    publication = graph.publications[GraphNodeId("nested")]
    declarations = tuple(
        (declaration.name, declaration.descriptor) for declaration in publication.descriptor.declarations.entries
    )
    candidates: list[ScopedResumeCandidate[str]] = []
    substitutions: list[AdmittedSubstitution[str]] = []
    child_runs: list[ScopeRunCoordinate] = []
    for superstep, value in ((2, "first"), (5, "second")):
        state = running_state(
            definition_id=graph.definition_id,
            frontier=("nested",),
            run_id=scope_run.graph_run_id,
            superstep=superstep,
            revision=superstep + 1,
        )
        state = replace(
            state,
            frontier=GraphFrontierState(
                (GraphFrontierNode(GraphNodeId("nested"), FailedGraphNode(GraphFailure("child aborted"))),)
            ),
        )
        action = SkipFailedNode(
            GraphNodeId("nested"),
            GraphSkipReason("boundary replacement"),
            ContinueGraphRouting(),
        )
        command = ResumeGraphNodes(state.revision, (action,))
        successor = reduce_graph_run(state, command)
        substitution = AdmittedSubstitution(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, superstep, GraphNodeId("nested")),
                publication.descriptor.identity,
            ),
            _make_node_output_frame(Graph.values(value=value), declarations),
            SkipSubstitutionProvenance(),
            successor.revision,
        )
        substitutions.append(substitution)
        candidates.append(ScopedResumeCandidate(graph, scope_run, state, successor, (substitution,), command))
        parent = ParentGraphActivation(state.run_id, superstep, GraphNodeId("nested"))
        child_runs.append(child_scope_run_for_activation(scope_run, parent))

    availability = CandidateFrameAvailability(ScopedFrameIndex(), ())
    installed = ScopedFrameIndex()
    materialized_values: list[str] = []
    for candidate, substitution in zip(candidates, substitutions, strict=True):
        availability = admit_resume_candidates(
            (candidate,),
            installed,
        )
        installed = install_confirmed_resume_frames(
            installed,
            _PlannedResume(
                scope_run,
                candidate.successor,
                PreparedResume(candidate.command, (), ()),
                (substitution,),
            ),
            candidate.successor,
        )
        routed = reduce_graph_run(
            candidate.successor,
            resolve_routing(graph, candidate.successor, scope_run, installed),
        )
        frame = materialize_node_input(graph, routed, scope_run, installed, GraphNodeId("consumer"))
        materialized_values.append(_frame_value(frame, "value"))

    assert child_runs[0] != child_runs[1]
    assert substitutions[0].coordinate != substitutions[1].coordinate
    assert installed.has_publication(substitutions[0].coordinate)
    assert availability.has_publication(substitutions[1].coordinate)
    assert materialized_values == ["first", "second"]


def test_mixed_completed_and_aborted_children_keep_canonical_parent_order() -> None:
    graph = parallel_nested_graph()
    parent = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    a = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("a"))
    b = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("b"))
    missing = prepare_frontier(
        graph,
        request(graph, parent, (MissingChild(a), MissingChild(b))),
    )
    completed = completed_child(reduce_graph_run(None, missing.missing_children[0].command))
    aborted_child = reduce_graph_run(None, missing.missing_children[1].command)
    aborted = reduce_graph_run(
        aborted_child,
        AbortGraphRun(aborted_child.revision, GraphAbortReason("child aborted")),
    )
    child_graph = graph.nested_graphs[GraphNodeId("a")]

    prepared = prepare_frontier(
        graph,
        request(
            graph,
            parent,
            (
                CompletedChild(a, completed, child_output(child_graph, "a-output")),
                AbortedChild(b, aborted),
            ),
        ),
    )

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
                GraphFrontierNode(GraphNodeId("a"), SucceededGraphNode(ContinueGraphRouting())),
                GraphFrontierNode(GraphNodeId("b"), FailedGraphNode(GraphFailure("failed"))),
                state.frontier.nodes[2],
            )
        ),
    )

    tasks = plan_tasks(graph, state, ExecutionLimits())

    assert tuple(task.node_id for task in tasks) == (GraphNodeId("c"),)


def test_family_driver_projects_an_acknowledged_aborted_child() -> None:
    graph = nested_graph()
    parent = running_state(definition_id=graph.definition_id, frontier=("nested",), run_id="parent-run")
    scope_run = root_scope_run(parent.run_id)
    parent_activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    child_coordinate = child_scope_run_for_activation(scope_run, parent_activation)
    child_graph = graph.nested_graphs[GraphNodeId("nested")]
    child = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_coordinate.graph_run_id, parent_activation),
    )
    aborted = reduce_graph_run(child, AbortGraphRun(child.revision, GraphAbortReason("child aborted")))
    context = _new_context(_new_family_identity(), parent, ScopedFrameIndex(), recovered=False)
    context.replace_child(
        ChildStateBinding(
            child_coordinate,
            StableActivation(scope_run, 0, GraphNodeId("nested")),
            aborted,
        )
    )

    projections = family_driver_module._child_projections(graph, parent, scope_run, context)  # pyright: ignore[reportPrivateUsage]

    assert len(projections) == 1
    assert isinstance(projections[0], AbortedChild)
    assert projections[0].child_state == aborted
