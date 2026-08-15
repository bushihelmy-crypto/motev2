"""Fail-closed boundaries retained by the streaming execution runtime."""

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.engine.factories import compiled_graph, running_state

from mote_kernel.execution import (
    AbortedChild,
    ActiveChild,
    CompletedChild,
    ExecutionRequestAttemptId,
    GraphExecutor,
    MissingChild,
    StartMissingChildren,
    StepRequest,
    WaitForActiveChildren,
    WaitingForChildren,
)
from mote_kernel.execution.claim import ConsumedExecutionClaim, ExecutionClaimOwner
from mote_kernel.execution.engine.claim_stage import prepare_claim, require_claim_tasks
from mote_kernel.execution.engine.frontier import prepare_frontier
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.resume_input import (
    effective_node_input,
    encode_resume_input,
    require_resume_input_binding,
)
from mote_kernel.execution.engine.routing import resolve_routing
from mote_kernel.execution.engine.scheduler import TaskRaised, TaskScheduler
from mote_kernel.execution.engine.session import issue_execution_session
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.superstep import validate_execution_session_request
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask, TaskId, task_identity
from mote_kernel.execution.errors import (
    InvalidExecutionSnapshotError,
    JoinProgressError,
    NodeExecutionContractError,
    ResultCollectionError,
    RoutingDeadlockError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph import (
    END,
    CompiledGraph,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
    JoinEdge,
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeSuccess,
    ResumeInputBinding,
    compile_graph,
)
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.execution.result import ChildProjection, PreparedNestedRun, TaskFailure, TaskSuccess
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphJoinProgress,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    OverrideGraphNodeInput,
    ParentGraphActivation,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    SelectGraphRoute,
    SettleGraphNode,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    UseStepRequestInput,
    child_graph_run_id,
    reduce_graph_run,
)


async def echo(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


class TextCodec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


def nested_graph() -> CompiledGraph[str, str]:
    child = GraphDefinition(
        GraphDefinitionId("boundary.child"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("boundary.parent"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(GraphNodeId("nested"), child),),
            (DirectEdge(GraphNodeId("nested"), END),),
            (GraphNodeId("nested"),),
        )
    )


def parallel_nested_graph() -> CompiledGraph[str, str]:
    child = GraphDefinition(
        GraphDefinitionId("boundary.child"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("boundary.parallel-parent"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("a"), child),
                NestedGraphNodeDefinition(GraphNodeId("b"), child),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
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
        encode_resume_input(graph, "value")
    with pytest.raises(SnapshotMismatchError, match="current pending"):
        effective_node_input(graph, state, GraphNodeId("missing"), "ordinary")

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
        effective_node_input(graph, override, GraphNodeId("a"), "ordinary")

    codec = TextCodec()
    resumable = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), echo),),
            (),
            (GraphNodeId("a"),),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("compiled.v1"), 1, codec, codec),
        )
    )
    mismatched = replace(
        state,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("durable.v1"), 1),
    )
    with pytest.raises(SnapshotMismatchError, match="does not match durable"):
        require_resume_input_binding(resumable, mismatched)


def test_child_projection_coverage_and_variant_guards_fail_closed() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, 0, GraphNodeId("nested"))
    request = StepRequest(state, "input", ExecutionRequestAttemptId("request"), ())
    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        prepare_frontier(graph, request)
    with pytest.raises(ResultCollectionError, match="unsupported variant"):
        prepare_frontier(
            graph,
            StepRequest(
                state,
                "input",
                ExecutionRequestAttemptId("request"),
                (cast(ChildProjection[str], object()),),
            ),
        )

    missing = prepare_frontier(
        graph,
        StepRequest(state, "input", ExecutionRequestAttemptId("request"), (MissingChild(activation),)),
    )
    child = reduce_graph_run(None, missing.missing_children[0].command)
    mismatched = replace(child, definition_id=GraphDefinitionId("other.child"))
    with pytest.raises(ResultCollectionError, match="parent activation or definition"):
        prepare_frontier(
            graph,
            StepRequest(state, "input", ExecutionRequestAttemptId("request"), (ActiveChild(activation, mismatched),)),
        )

    completed = replace(child, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))
    with pytest.raises(ResultCollectionError, match="running child"):
        prepare_frontier(
            graph,
            StepRequest(state, "input", ExecutionRequestAttemptId("request"), (ActiveChild(activation, completed),)),
        )
    with pytest.raises(ResultCollectionError, match="completed child"):
        prepare_frontier(
            graph,
            StepRequest(
                state,
                "input",
                ExecutionRequestAttemptId("request"),
                (CompletedChild(activation, child, "output", ContinueGraphRouting()),),
            ),
        )
    with pytest.raises(ResultCollectionError, match="aborted child"):
        prepare_frontier(
            graph,
            StepRequest(state, "input", ExecutionRequestAttemptId("request"), (AbortedChild(activation, child),)),
        )


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
        prepare_frontier(
            graph,
            StepRequest(state, "input", ExecutionRequestAttemptId("request"), projections),
        )


@pytest.mark.parametrize(
    "coordinate",
    ["run-id", "parent-run", "parent-step", "parent-node", "definition", "version"],
)
def test_child_projection_rejects_each_state_coordinate_mismatch(coordinate: str) -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    prepared = prepare_frontier(
        graph,
        StepRequest(
            state,
            "input",
            ExecutionRequestAttemptId("request"),
            (MissingChild(activation),),
        ),
    )
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
        prepare_frontier(
            graph,
            StepRequest(
                state,
                "input",
                ExecutionRequestAttemptId("request"),
                (ActiveChild(activation, mismatched),),
            ),
        )


def test_child_projection_validates_terminal_state_before_projecting_variant() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    prepared = prepare_frontier(
        graph,
        StepRequest(state, "input", ExecutionRequestAttemptId("request"), (MissingChild(activation),)),
    )
    child = reduce_graph_run(None, prepared.missing_children[0].command)
    corrupted = replace(child, status=GraphRunStatus.COMPLETED)

    with pytest.raises(GraphStateTransitionError, match="canonical empty position"):
        prepare_frontier(
            graph,
            StepRequest(
                state,
                "input",
                ExecutionRequestAttemptId("request"),
                (CompletedChild(activation, corrupted, "output", ContinueGraphRouting()),),
            ),
        )


def test_non_nested_frontier_rejects_nonempty_child_projection() -> None:
    graph = compiled_graph("a")
    state = running_state()
    projection = MissingChild(ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("a")))

    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        prepare_frontier(
            graph,
            StepRequest(state, "input", ExecutionRequestAttemptId("request"), (projection,)),
        )


def test_running_awaiting_resume_child_remains_active_without_rebuild() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(
        graph,
        StepRequest(state, "input", ExecutionRequestAttemptId("request"), (MissingChild(activation),)),
    )
    child = reduce_graph_run(None, missing.missing_children[0].command)
    awaiting = replace(
        child,
        frontier=GraphFrontierState(
            (GraphFrontierNode(GraphNodeId("child"), FailedGraphNode(GraphFailure("failed"))),)
        ),
    )

    prepared = prepare_frontier(
        graph,
        StepRequest(state, "input", ExecutionRequestAttemptId("request"), (ActiveChild(activation, awaiting),)),
    )

    assert prepared.missing_children == ()
    assert prepared.active_children == (ActiveChild(activation, awaiting),)


def test_active_child_must_match_its_compiled_resume_codec() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(
        graph,
        StepRequest(state, "input", ExecutionRequestAttemptId("request"), (MissingChild(activation),)),
    )
    child = reduce_graph_run(None, missing.missing_children[0].command)
    mismatched = replace(
        child,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("unexpected.input"), 1),
    )

    with pytest.raises(SnapshotMismatchError, match="codec"):
        prepare_frontier(
            graph,
            StepRequest(
                state,
                "input",
                ExecutionRequestAttemptId("request"),
                (ActiveChild(activation, mismatched),),
            ),
        )


@pytest.mark.asyncio
async def test_scheduler_rejects_empty_duplicate_nested_and_untyped_work() -> None:
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
        "input",
    )
    scheduler.submit((executable,))
    with pytest.raises(NodeExecutionContractError, match="submitted more than once"):
        scheduler.submit((executable,))
    await scheduler.aclose()

    nested = TaskScheduler(nested_graph())
    nested.submit(
        (
            ExecutableTask(
                GraphTask(TaskId("nested"), GraphRunId("run"), 0, GraphNodeId("nested")),
                "input",
            ),
        )
    )
    nested_event = await nested.next_completion()
    assert isinstance(nested_event, TaskRaised)
    assert isinstance(nested_event.error, NodeExecutionContractError)
    await nested.aclose()

    async def invalid(node_input: str) -> NodeSuccess[str]:
        return cast(NodeSuccess[str], node_input)

    invalid_graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("invalid.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), invalid),),
            (),
            (GraphNodeId("a"),),
        )
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
                "input",
            ),
        )
    )
    invalid_event = await invalid_scheduler.next_completion()
    assert isinstance(invalid_event, TaskRaised)
    assert isinstance(invalid_event.error, NodeExecutionContractError)


def test_child_wait_payloads_require_nonempty_canonical_parents() -> None:
    graph = nested_graph()
    state = running_state(definition_id="boundary.parent", frontier=("nested",))
    activation = ParentGraphActivation(state.run_id, 0, GraphNodeId("nested"))
    child_graph = graph.nodes[GraphNodeId("nested")]
    assert isinstance(child_graph, NestedGraphNodeDefinition)
    compiled_child = compile_graph(child_graph.graph)
    command = project_start_graph_command(
        compiled_child,
        child_graph_run_id(state.run_id, state.superstep, activation.node_id),
        activation,
    )
    prepared = PreparedNestedRun(activation, compiled_child, command)
    active = ActiveChild(activation, reduce_graph_run(None, command))

    with pytest.raises(ValueError, match="non-empty"):
        StartMissingChildren[str, str](())
    with pytest.raises(ValueError, match="non-empty"):
        WaitForActiveChildren(())
    with pytest.raises(ValueError, match="canonical"):
        StartMissingChildren((prepared, prepared))
    with pytest.raises(ValueError, match="canonical"):
        WaitForActiveChildren((active, active))


def test_routing_rejects_invalid_progress_and_partial_join_deadlock() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("join.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), echo),
                NodeDefinition(GraphNodeId("b"), echo),
                NodeDefinition(GraphNodeId("joined"), echo),
            ),
            (JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("joined")),),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("joined"),
        frozenset({GraphNodeId("a")}),
    )
    with pytest.raises(RoutingDeadlockError):
        resolve_routing(graph, (), (progress,))
    with pytest.raises(JoinProgressError):
        resolve_routing(
            graph,
            (),
            (GraphJoinProgress((GraphNodeId("a"),), GraphNodeId("joined"), frozenset()),),
        )


def test_conditional_route_to_end_returns_standalone_completion_command() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("conditional.end"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), echo),),
            (ConditionalEdge(GraphNodeId("a"), GraphRouteId("done"), END),),
            (GraphNodeId("a"),),
        )
    )
    command = resolve_routing(
        graph,
        ((GraphNodeId("a"), SelectGraphRoute(GraphRouteId("done"))),),
        (),
        expected_revision=7,
    )
    assert command.expected_revision == 7


def test_snapshot_guard_rejects_unknown_and_mismatched_resource_participants() -> None:
    resource = ResourceId("file")
    database = ResourceId("database")
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), echo, (resource,)),),
            (),
            (GraphNodeId("a"),),
            (ResourceDefinition(resource, 0), ResourceDefinition(database, 1)),
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
            free_graph, replace(active, definition_id=free_graph.definition_id, resources=mismatched)
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
    graph = nested_graph().nodes[GraphNodeId("nested")]
    assert isinstance(graph, NestedGraphNodeDefinition)
    compiled_child = compile_graph(graph.graph)
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
        validate_execution_session_request(
            graph,
            StepRequest(state, "input", ExecutionRequestAttemptId("request"), ()),
            claim,
        )


def test_session_request_validation_rejects_a_claim_that_still_has_an_active_child() -> None:
    graph = nested_graph()
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(
        graph,
        StepRequest(state, "input", ExecutionRequestAttemptId("request"), (MissingChild(activation),)),
    )
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
            StepRequest(state, "input", ExecutionRequestAttemptId("request"), (ActiveChild(activation, child),)),
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
    request = StepRequest(claimed, "input", request_id, ())
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
        issue_execution_session(
            graph,
            StepRequest(forged, "input", request_id, ()),
            receipt,
        )

    session = issue_execution_session(graph, request, receipt)
    try:
        with pytest.raises(ResultCollectionError, match="already issued"):
            issue_execution_session(graph, request, receipt)
    finally:
        await session.aclose()


def test_consumed_claim_receipt_cannot_be_constructed_directly() -> None:
    graph = compiled_graph("a")
    state = running_state()
    owner = ExecutionClaimOwner()
    request_id = ExecutionRequestAttemptId("request")
    claim = prepare_claim(owner, state, request_id, plan_tasks(graph, state, ExecutionLimits()), None)

    with pytest.raises(TypeError, match=r"issued only by PreparedExecutionClaim\.consume"):
        cast(Callable[..., object], ConsumedExecutionClaim)(object(), claim.snapshot)


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
    both_missing = await executor.prepare(
        StepRequest(
            parent,
            "input",
            ExecutionRequestAttemptId("request"),
            (MissingChild(a), MissingChild(b)),
        )
    )
    assert isinstance(both_missing, WaitingForChildren)
    assert isinstance(both_missing.action, StartMissingChildren)
    active_b = reduce_graph_run(None, both_missing.action.children[1].command)

    disposition = await executor.prepare(
        StepRequest(
            parent,
            "input",
            ExecutionRequestAttemptId("request"),
            (MissingChild(a), ActiveChild(b, active_b)),
        )
    )

    assert isinstance(disposition, WaitingForChildren)
    assert isinstance(disposition.action, StartMissingChildren)
    assert tuple(child.parent for child in disposition.action.children) == (a,)


@pytest.mark.parametrize("variant", ["completed", "aborted"])
def test_terminal_child_projects_its_matching_parent_result_variant(variant: str) -> None:
    graph = nested_graph()
    parent = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(
        graph,
        StepRequest(parent, "input", ExecutionRequestAttemptId("request"), (MissingChild(activation),)),
    )
    child = reduce_graph_run(None, missing.missing_children[0].command)
    if variant == "completed":
        terminal = completed_child(child)
        projection: ChildProjection[str] = CompletedChild(activation, terminal, "child-output", ContinueGraphRouting())
        result_type: type[object] = TaskSuccess
    else:
        terminal = reduce_graph_run(child, AbortGraphRun(child.revision, GraphAbortReason("child aborted")))
        projection = AbortedChild(activation, terminal)
        result_type = TaskFailure

    prepared = prepare_frontier(
        graph,
        StepRequest(parent, "input", ExecutionRequestAttemptId("request"), (projection,)),
    )

    assert len(prepared.nested_results) == 1
    assert isinstance(prepared.nested_results[0], result_type)


def test_mixed_completed_and_aborted_children_keep_canonical_parent_order() -> None:
    graph = parallel_nested_graph()
    parent = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    a = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("a"))
    b = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("b"))
    missing = prepare_frontier(
        graph,
        StepRequest(
            parent,
            "input",
            ExecutionRequestAttemptId("request"),
            (MissingChild(a), MissingChild(b)),
        ),
    )
    completed = completed_child(reduce_graph_run(None, missing.missing_children[0].command))
    aborted_child = reduce_graph_run(None, missing.missing_children[1].command)
    aborted = reduce_graph_run(
        aborted_child,
        AbortGraphRun(aborted_child.revision, GraphAbortReason("child aborted")),
    )

    prepared = prepare_frontier(
        graph,
        StepRequest(
            parent,
            "input",
            ExecutionRequestAttemptId("request"),
            (
                CompletedChild(a, completed, "a-output", ContinueGraphRouting()),
                AbortedChild(b, aborted),
            ),
        ),
    )

    assert tuple(type(result) for result in prepared.nested_results) == (TaskSuccess, TaskFailure)
    assert tuple(result.task.node_id for result in prepared.nested_results) == (GraphNodeId("a"), GraphNodeId("b"))


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
