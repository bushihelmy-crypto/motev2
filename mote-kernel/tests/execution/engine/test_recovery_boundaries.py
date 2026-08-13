from dataclasses import replace
from typing import cast

import pytest
from tests.execution.driver import ATTEMPT_ID, apply_command
from tests.execution.engine.factories import compiled_graph, running_state

from mote_kernel.execution import (
    AbortedChild,
    ActiveChild,
    AwaitingResume,
    CompletedChild,
    ExecutableFrontier,
    GraphExecutor,
    MissingChild,
    OverrideNodeInput,
    PreparedNestedRun,
    PreparedResourceAdmission,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
    SkipFailedNodeRequest,
    StartMissingChildren,
    StepRequest,
    UseRequestInput,
    WaitForActiveChildren,
    WaitingForChildren,
)
from mote_kernel.execution.claim import ExecutionClaimOwner
from mote_kernel.execution.engine.claim_stage import prepare_claim, require_claim_tasks
from mote_kernel.execution.engine.frontier import prepare_frontier
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.resource_stage import execute_resource_waves, validated_resource_nodes
from mote_kernel.execution.engine.resume_input import (
    effective_node_input,
    encode_resume_input,
    require_resume_input_binding,
)
from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.superstep import execute_claimed_frontier
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask, TaskId
from mote_kernel.execution.errors import (
    InvalidExecutionSnapshotError,
    InvalidRoutingCommandError,
    NodeExecutionContractError,
    ResultCollectionError,
    SnapshotMismatchError,
    UnknownRouteError,
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
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeSuccess,
    ResumeInputBinding,
    SelectGraphRoute,
    compile_graph,
)
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import ResumeNodeRequest
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.execution.result import ChildProjection
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphNodeInterrupt,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRunId,
    GraphRunStatus,
    GraphSkipReason,
    GraphStateTransitionError,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    ParentGraphActivation,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceLock,
    ResourceSnapshot,
    SettleGraphExecution,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    UpdateGraphResources,
    child_graph_run_id,
    derive_graph_node_interrupt_identity,
    graph_interrupt_id,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio
FILE = ResourceId("file")
DATABASE = ResourceId("database")


async def echo(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


class TextCodec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


def resumable_graph(*node_ids: str) -> CompiledGraph[str, str]:
    codec = TextCodec()
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            tuple(NodeDefinition(GraphNodeId(node_id), echo) for node_id in node_ids),
            (),
            tuple(GraphNodeId(node_id) for node_id in node_ids),
            resume_input=ResumeInputBinding(
                GraphResumeInputCodecId("input.v1"),
                1,
                codec,
                codec,
            ),
        )
    )


def nested_graph() -> CompiledGraph[str, str]:
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(GraphNodeId("nested"), child),),
            (DirectEdge(GraphNodeId("nested"), END),),
            (GraphNodeId("nested"),),
        )
    )


async def test_claim_task_guard_rejects_forged_rebuild() -> None:
    graph = compiled_graph("a")
    state = running_state()
    tasks = plan_tasks(graph, state, ExecutionLimits())
    claim = prepare_claim(ExecutionClaimOwner(), state, ATTEMPT_ID, tasks)
    forged = (replace(tasks[0], task_id=TaskId("forged")),)

    with pytest.raises(ResultCollectionError, match="tasks do not match"):
        require_claim_tasks(claim, forged)


async def test_child_projection_coverage_and_variant_guards_fail_closed() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    state = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, 0, GraphNodeId("nested"))
    request = StepRequest(state, "input", ATTEMPT_ID, ())
    with pytest.raises(ResultCollectionError, match="exactly"):
        prepare_frontier(graph, request)

    missing = prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (MissingChild(activation),)))
    child = reduce_graph_run(None, missing.missing_children[0].command)
    wrong_identity = replace(child, run_id=GraphRunId("wrong"))
    with pytest.raises(GraphStateTransitionError, match="child graph run identity"):
        prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (ActiveChild(activation, wrong_identity),)))

    completed_shape = replace(child, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))
    with pytest.raises(ResultCollectionError, match="running child"):
        prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (ActiveChild(activation, completed_shape),)))
    with pytest.raises(ResultCollectionError, match="completed child"):
        prepare_frontier(
            graph,
            StepRequest(
                state,
                "input",
                ATTEMPT_ID,
                (CompletedChild(activation, child, "output", ContinueGraphRouting()),),
            ),
        )
    with pytest.raises(ResultCollectionError, match="aborted child"):
        prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (AbortedChild(activation, child),)))
    with pytest.raises(ResultCollectionError, match="unsupported variant"):
        prepare_frontier(
            graph,
            StepRequest(
                state,
                "input",
                ATTEMPT_ID,
                (cast(ChildProjection[str], object()),),
            ),
        )


@pytest.mark.parametrize(
    "case",
    ["missing", "duplicate", "extra", "noncanonical", "wrong-run", "wrong-step"],
)
async def test_child_projection_requires_exact_canonical_parent_coverage(case: str) -> None:
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("a"), child),
                NestedGraphNodeDefinition(GraphNodeId("b"), child),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    state = reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run")))
    a = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("a"))
    b = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("b"))
    wrong_run = ParentGraphActivation(GraphRunId("other"), state.superstep, GraphNodeId("a"))
    wrong_step = ParentGraphActivation(state.run_id, state.superstep + 1, GraphNodeId("a"))

    invalid = {
        "missing": (MissingChild(a),),
        "duplicate": (MissingChild(a), MissingChild(a)),
        "extra": (MissingChild(a), MissingChild(b), MissingChild(b)),
        "noncanonical": (MissingChild(b), MissingChild(a)),
        "wrong-run": (MissingChild(wrong_run), MissingChild(b)),
        "wrong-step": (MissingChild(wrong_step), MissingChild(b)),
    }
    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, invalid[case]))

    prepared = prepare_frontier(
        graph,
        StepRequest(state, "input", ATTEMPT_ID, (MissingChild(a), MissingChild(b))),
    )
    assert tuple(item.parent for item in prepared.missing_children) == (a, b)
    assert prepared.missing_children[0].command.run_id != prepared.missing_children[1].command.run_id


@pytest.mark.parametrize(
    "coordinate",
    ["run-id", "parent-run", "parent-step", "parent-node", "definition", "version"],
)
async def test_child_projection_rejects_identity_definition_and_parent_mismatch(coordinate: str) -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    state = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    prepared = prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (MissingChild(activation),)))
    child = reduce_graph_run(None, prepared.missing_children[0].command)
    mismatched = {
        "run-id": replace(child, run_id=GraphRunId("forged")),
        "parent-run": replace(child, parent=replace(activation, run_id=GraphRunId("other"))),
        "parent-step": replace(child, parent=replace(activation, superstep=activation.superstep + 1)),
        "parent-node": replace(child, parent=replace(activation, node_id=GraphNodeId("other"))),
        "definition": replace(child, definition_id=GraphDefinitionId("other.graph")),
        "version": replace(child, definition_version=GraphDefinitionVersion(2)),
    }[coordinate]

    if coordinate in {"run-id", "parent-run", "parent-step", "parent-node"}:
        with pytest.raises(GraphStateTransitionError, match="child graph run identity"):
            prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (ActiveChild(activation, mismatched),)))
    else:
        with pytest.raises(ResultCollectionError, match="parent activation or definition"):
            prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (ActiveChild(activation, mismatched),)))


async def test_child_projection_rejects_invalid_terminal_state_before_variant_projection() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    state = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    prepared = prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (MissingChild(activation),)))
    child = reduce_graph_run(None, prepared.missing_children[0].command)
    corrupted = replace(child, status=GraphRunStatus.COMPLETED)

    with pytest.raises(GraphStateTransitionError, match="canonical empty position"):
        prepare_frontier(
            graph,
            StepRequest(
                state,
                "input",
                ATTEMPT_ID,
                (CompletedChild(activation, corrupted, "output", ContinueGraphRouting()),),
            ),
        )


async def test_non_nested_frontier_rejects_nonempty_child_projection() -> None:
    graph = compiled_graph("a")
    state = running_state()
    extra = MissingChild(ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("a")))

    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        await GraphExecutor(graph).prepare(StepRequest(state, "input", ATTEMPT_ID, (extra,)))


async def test_missing_child_takes_priority_over_an_active_sibling() -> None:
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("a"), child),
                NestedGraphNodeDefinition(GraphNodeId("b"), child),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    a = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("a"))
    b = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("b"))
    both_missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(a), MissingChild(b))))
    assert isinstance(both_missing, WaitingForChildren)
    assert isinstance(both_missing.action, StartMissingChildren)
    active_b = reduce_graph_run(None, both_missing.action.children[1].command)

    disposition = await executor.prepare(
        StepRequest(
            parent,
            "input",
            ATTEMPT_ID,
            (MissingChild(a), ActiveChild(b, active_b)),
        )
    )

    assert isinstance(disposition, WaitingForChildren)
    assert isinstance(disposition.action, StartMissingChildren)
    assert tuple(child.parent for child in disposition.action.children) == (a,)


async def test_child_projection_accepts_running_awaiting_resume_child_without_rebuilding_it() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    state = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    prepared = prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (MissingChild(activation),)))
    child = reduce_graph_run(None, prepared.missing_children[0].command)
    awaiting = replace(
        child,
        frontier=GraphFrontierState(
            (GraphFrontierNode(GraphNodeId("child"), FailedGraphNode(GraphFailure("failed"))),)
        ),
    )

    active = prepare_frontier(
        graph,
        StepRequest(state, "input", ATTEMPT_ID, (ActiveChild(activation, awaiting),)),
    )

    assert active.missing_children == ()
    assert active.active_children == (ActiveChild(activation, awaiting),)
    assert active.active_children[0].child_state.run_id == child.run_id


async def test_active_child_must_match_its_compiled_resume_codec() -> None:
    codec = TextCodec()
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
        resume_input=ResumeInputBinding(
            GraphResumeInputCodecId("child.input.v1"),
            1,
            codec,
            codec,
        ),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(GraphNodeId("nested"), child),),
            (DirectEdge(GraphNodeId("nested"), END),),
            (GraphNodeId("nested"),),
        )
    )
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    assert isinstance(missing.action, StartMissingChildren)
    child_state = reduce_graph_run(None, missing.action.children[0].command)
    mismatched = replace(
        child_state,
        resume_input_codec=GraphResumeInputCodec(
            GraphResumeInputCodecId("unexpected.input"),
            1,
        ),
    )

    with pytest.raises(SnapshotMismatchError, match="codec"):
        await executor.prepare(
            StepRequest(
                parent,
                "input",
                ATTEMPT_ID,
                (ActiveChild(activation, mismatched),),
            )
        )


async def test_mixed_completed_and_aborted_children_preserve_both_parent_outcomes() -> None:
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("a"), child),
                NestedGraphNodeDefinition(GraphNodeId("b"), child),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    a = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("a"))
    b = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("b"))
    missing = prepare_frontier(
        graph,
        StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(a), MissingChild(b))),
    )
    a_child = reduce_graph_run(None, missing.missing_children[0].command)
    a_claimed = reduce_graph_run(
        a_child,
        ClaimGraphExecution(a_child.revision, GraphExecutionAttemptId("a-child"), (GraphNodeId("child"),)),
    )
    assert a_claimed.execution is not None
    completed = reduce_graph_run(
        a_claimed,
        SettleGraphExecution(
            a_claimed.revision,
            a_claimed.execution.token,
            (SucceededGraphNodeOutcome(GraphNodeId("child"), ContinueGraphRouting()),),
            CompleteGraphFrontier(),
        ),
    )
    b_child = reduce_graph_run(None, missing.missing_children[1].command)
    aborted = reduce_graph_run(b_child, AbortGraphRun(b_child.revision, GraphAbortReason("b aborted")))
    projections = (
        CompletedChild(a, completed, "a-output", ContinueGraphRouting()),
        AbortedChild(b, aborted),
    )
    prepared = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, projections))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = reduce_graph_run(parent, prepared.claim.command)

    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, projections))

    assert tuple(type(item).__name__ for item in result.results) == ("TaskSuccess", "TaskFailure")
    settled = reduce_graph_run(claimed, result.command)
    assert isinstance(settled.frontier.nodes[0].settlement, SucceededGraphNode)
    assert settled.frontier.nodes[1].settlement == FailedGraphNode(GraphFailure("b aborted"))


async def test_completed_child_routing_is_validated_with_the_parent_topology() -> None:
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("nested"), child),
                NodeDefinition(GraphNodeId("left"), echo),
                NodeDefinition(GraphNodeId("right"), echo),
            ),
            (
                ConditionalEdge(GraphNodeId("nested"), GraphRouteId("left"), GraphNodeId("left")),
                ConditionalEdge(GraphNodeId("nested"), GraphRouteId("right"), GraphNodeId("right")),
                DirectEdge(GraphNodeId("left"), END),
                DirectEdge(GraphNodeId("right"), END),
            ),
            (GraphNodeId("nested"),),
        )
    )
    executor = GraphExecutor(graph)
    state = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
    missing = prepare_frontier(graph, StepRequest(state, "input", ATTEMPT_ID, (MissingChild(activation),)))
    child_state = reduce_graph_run(None, missing.missing_children[0].command)
    child_state = replace(child_state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    prepared = await executor.prepare(
        StepRequest(
            state,
            "input",
            ATTEMPT_ID,
            (
                CompletedChild(
                    activation,
                    child_state,
                    "output",
                    SelectGraphRoute(GraphRouteId("right")),
                ),
            ),
        )
    )
    assert isinstance(prepared, ExecutableFrontier)
    assert prepared.claim is not None
    claimed = reduce_graph_run(state, prepared.claim.command)
    result = await executor.execute(
        prepared.claim,
        StepRequest(
            claimed,
            "input",
            ATTEMPT_ID,
            (
                CompletedChild(
                    activation,
                    child_state,
                    "output",
                    SelectGraphRoute(GraphRouteId("right")),
                ),
            ),
        ),
    )
    advanced = reduce_graph_run(claimed, result.command)
    assert tuple(node.node_id for node in advanced.frontier.nodes) == (GraphNodeId("right"),)


async def test_execute_rechecks_child_wait_after_claim() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    state = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(state.run_id, 0, GraphNodeId("nested"))
    waiting_request: StepRequest[str, str] = StepRequest(
        state,
        "input",
        ATTEMPT_ID,
        (MissingChild(activation),),
    )
    frontier = prepare_frontier(graph, waiting_request)
    claim = prepare_claim(ExecutionClaimOwner(), state, ATTEMPT_ID, frontier.tasks)
    claimed = replace(
        state,
        execution_sequence=1,
        execution=GraphExecutionLease(claim.snapshot.token, claim.snapshot.node_ids),
    )
    with pytest.raises(ResultCollectionError, match="cannot wait"):
        await execute_claimed_frontier(
            graph,
            StepRequest(claimed, "input", ATTEMPT_ID, (MissingChild(activation),)),
            claim,
        )


async def test_resume_input_narrow_guards() -> None:
    graph = compiled_graph("a")
    state = running_state()
    with pytest.raises(SnapshotMismatchError, match="does not define"):
        encode_resume_input(graph, "value")
    with pytest.raises(SnapshotMismatchError, match="current pending"):
        effective_node_input(graph, state, GraphNodeId("missing"), "ordinary")

    forged = replace(
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
        effective_node_input(graph, forged, GraphNodeId("a"), "ordinary")

    codec = TextCodec()
    codec_graph = compile_graph(
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
        require_resume_input_binding(codec_graph, mismatched)


async def test_scheduler_rejects_nested_and_unsupported_outcomes_and_empty_batch() -> None:
    graph = nested_graph()
    task = GraphTask(TaskId("task"), GraphRunId("run"), 0, GraphNodeId("nested"))
    with pytest.raises(NodeExecutionContractError, match="nested"):
        await execute_tasks(graph, (ExecutableTask(task, "input"),))
    assert await execute_tasks(compiled_graph("a"), ()) == ()

    async def invalid(node_input: str) -> NodeSuccess[str]:
        return node_input  # type: ignore[return-value]

    invalid_graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), invalid),),
            (),
            (GraphNodeId("a"),),
        )
    )
    invalid_task = plan_tasks(invalid_graph, running_state(), ExecutionLimits())[0]
    with pytest.raises(NodeExecutionContractError, match="unsupported"):
        await execute_tasks(invalid_graph, (ExecutableTask(invalid_task, "input"),))


async def test_resource_guards_reject_shape_and_stalled_wave() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), echo, (FILE,)),),
            (),
            (GraphNodeId("a"),),
            (ResourceDefinition(FILE, 1),),
        )
    )
    task = plan_tasks(graph, running_state(), ExecutionLimits())[0]
    with pytest.raises(ResultCollectionError, match="resource order"):
        validated_resource_nodes(graph, (task,), ResourceSnapshot(()))
    with pytest.raises(ResultCollectionError, match="exactly cover"):
        validated_resource_nodes(graph, (task,), ResourceSnapshot((ResourceLock(FILE),)))

    requirement_graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), echo, (FILE,)),),
            (),
            (GraphNodeId("a"),),
            (ResourceDefinition(FILE, 0), ResourceDefinition(DATABASE, 1)),
        )
    )
    requirement_task = plan_tasks(requirement_graph, running_state(), ExecutionLimits())[0]
    mismatched_requirements = ResourceSnapshot(
        (ResourceLock(FILE), ResourceLock(DATABASE, GraphNodeId("a"))),
        (
            ResourceAcquisition(
                GraphNodeId("a"),
                (DATABASE,),
                (DATABASE,),
            ),
        ),
    )
    with pytest.raises(ResultCollectionError, match="compiled requirements"):
        validated_resource_nodes(requirement_graph, (requirement_task,), mismatched_requirements)

    waiting = ResourceSnapshot(
        (ResourceLock(FILE, waiters=(GraphNodeId("a"),)),),
        (ResourceAcquisition(GraphNodeId("a"), (FILE,), (), FILE),),
    )
    executable = ExecutableTask(task, "input")
    with pytest.raises(ResultCollectionError, match="cannot advance"):
        await execute_resource_waves(graph, (executable,), waiting, frozenset({GraphNodeId("a")}))


async def test_superstep_rejects_active_execution_and_missing_resource_admission() -> None:
    graph = compiled_graph("a")
    executor = GraphExecutor(graph)
    state = running_state()
    prepared = await executor.prepare(StepRequest(state, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(state, prepared.claim.command)
    with pytest.raises(ResultCollectionError, match="active execution"):
        await executor.prepare(StepRequest(claimed, "input", ATTEMPT_ID, ()))

    resource_graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), echo, (FILE,)),),
            (),
            (GraphNodeId("a"),),
            (ResourceDefinition(FILE, 1),),
        )
    )
    resource_state = running_state()
    tasks = plan_tasks(resource_graph, resource_state, ExecutionLimits())
    claim = prepare_claim(ExecutionClaimOwner(), resource_state, ATTEMPT_ID, tasks)
    claimed_without_admission = replace(
        resource_state,
        execution_sequence=1,
        execution=GraphExecutionLease(claim.snapshot.token, claim.snapshot.node_ids),
    )
    with pytest.raises(ResultCollectionError, match="committed admission"):
        await execute_claimed_frontier(
            resource_graph,
            StepRequest(claimed_without_admission, "input", ATTEMPT_ID, ()),
            claim,
        )


async def test_awaiting_resume_reports_failed_and_interrupted_nodes_separately() -> None:
    graph = resumable_graph("a", "b")
    state = running_state(frontier=("a", "b"))
    state = replace(
        state,
        execution_sequence=1,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(GraphNodeId("a"), FailedGraphNode(GraphFailure("failed"))),
                GraphFrontierNode(
                    GraphNodeId("b"),
                    InterruptedGraphNode(
                        GraphNodeInterrupt(
                            derive_graph_node_interrupt_identity(state.run_id, 0, GraphNodeId("b"), 1),
                            GraphInterruptPayload(b"question"),
                        )
                    ),
                ),
            )
        ),
    )

    disposition = await GraphExecutor(graph).prepare(StepRequest(state, "input", ATTEMPT_ID, ()))

    assert isinstance(disposition, AwaitingResume)
    assert disposition.failed_node_ids == (GraphNodeId("a"),)
    assert disposition.interrupted_node_ids == (GraphNodeId("b"),)


async def test_awaiting_resume_rejects_invalid_retained_routing_before_disposition() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), echo),
                NodeDefinition(GraphNodeId("b"), echo),
                NodeDefinition(GraphNodeId("next"), echo),
            ),
            (
                ConditionalEdge(
                    GraphNodeId("a"),
                    GraphRouteId("go"),
                    GraphNodeId("next"),
                ),
            ),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    state = running_state(frontier=("a", "b"))
    recovered = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("a"),
                    SucceededGraphNode(ContinueGraphRouting()),
                ),
                GraphFrontierNode(
                    GraphNodeId("b"),
                    FailedGraphNode(GraphFailure("failed")),
                ),
            )
        ),
    )

    with pytest.raises(InvalidRoutingCommandError, match="must select"):
        await GraphExecutor(graph).prepare(StepRequest(recovered, "input", ATTEMPT_ID, ()))


async def test_pending_nodes_claim_without_reexecuting_failed_or_interrupted_siblings() -> None:
    graph = resumable_graph("a", "b", "c")
    state = running_state(frontier=("a", "b", "c"))
    state = replace(
        state,
        execution_sequence=1,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1),
        frontier=GraphFrontierState(
            (
                state.frontier.nodes[0],
                GraphFrontierNode(GraphNodeId("b"), FailedGraphNode(GraphFailure("failed"))),
                GraphFrontierNode(
                    GraphNodeId("c"),
                    InterruptedGraphNode(
                        GraphNodeInterrupt(
                            derive_graph_node_interrupt_identity(state.run_id, 0, GraphNodeId("c"), 1),
                            GraphInterruptPayload(b"question"),
                        )
                    ),
                ),
            )
        ),
    )

    disposition = await GraphExecutor(graph).prepare(StepRequest(state, "input", ATTEMPT_ID, ()))

    assert isinstance(disposition, ExecutableFrontier) and disposition.claim is not None
    assert disposition.claim.command.node_ids == (GraphNodeId("a"),)


@pytest.mark.parametrize("route", ["missing", "unknown"])
async def test_conditional_skip_rejects_missing_or_unknown_route(route: str) -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), echo),
                NodeDefinition(GraphNodeId("next"), echo),
            ),
            (
                ConditionalEdge(
                    GraphNodeId("a"),
                    GraphRouteId("go"),
                    GraphNodeId("next"),
                ),
            ),
            (GraphNodeId("a"),),
        )
    )
    failed = replace(
        running_state(),
        frontier=GraphFrontierState((GraphFrontierNode(GraphNodeId("a"), FailedGraphNode(GraphFailure("failed"))),)),
    )
    routing = ContinueGraphRouting() if route == "missing" else SelectGraphRoute(GraphRouteId("unknown"))
    expected_error = InvalidRoutingCommandError if route == "missing" else UnknownRouteError

    with pytest.raises(expected_error):
        GraphExecutor(graph).resume(
            ResumeRequest(
                failed,
                (
                    SkipFailedNodeRequest(
                        GraphNodeId("a"),
                        GraphSkipReason("operator"),
                        routing,
                    ),
                ),
            )
        )


async def test_conditional_skip_projects_atomic_route_advance_without_mutating_state() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), echo),
                NodeDefinition(GraphNodeId("next"), echo),
            ),
            (
                ConditionalEdge(
                    GraphNodeId("a"),
                    GraphRouteId("go"),
                    GraphNodeId("next"),
                ),
            ),
            (GraphNodeId("a"),),
        )
    )
    failed = replace(
        running_state(),
        frontier=GraphFrontierState((GraphFrontierNode(GraphNodeId("a"), FailedGraphNode(GraphFailure("failed"))),)),
    )

    command = GraphExecutor(graph).resume(
        ResumeRequest(
            failed,
            (
                SkipFailedNodeRequest(
                    GraphNodeId("a"),
                    GraphSkipReason("operator"),
                    SelectGraphRoute(GraphRouteId("go")),
                ),
            ),
        )
    )

    assert command.resolution == AdvanceGraphFrontier((GraphNodeId("next"),), ())
    assert isinstance(failed.frontier.nodes[0].settlement, FailedGraphNode)
    advanced = reduce_graph_run(failed, command)
    assert tuple(node.node_id for node in advanced.frontier.nodes) == (GraphNodeId("next"),)


async def test_executor_rejects_unknown_graph_and_invalid_resume_targets() -> None:
    executor = GraphExecutor(compiled_graph("a"))
    foreign = running_state(definition_id="foreign.graph")
    with pytest.raises(SnapshotMismatchError, match="not owned"):
        await executor.prepare(StepRequest[str, str](foreign, "input", ATTEMPT_ID, ()))

    pending = running_state()
    with pytest.raises(SnapshotMismatchError, match="requires a failed node"):
        executor.resume(
            ResumeRequest(
                pending,
                (ResumeFailedNodeRequest(GraphNodeId("a"), UseRequestInput()),),
            )
        )
    with pytest.raises(SnapshotMismatchError, match="skip requires a failed node"):
        executor.resume(
            ResumeRequest(
                pending,
                (
                    SkipFailedNodeRequest(
                        GraphNodeId("a"),
                        GraphSkipReason("operator"),
                        ContinueGraphRouting(),
                    ),
                ),
            )
        )

    failed = replace(
        pending,
        frontier=GraphFrontierState((GraphFrontierNode(GraphNodeId("a"), FailedGraphNode(GraphFailure("failed"))),)),
    )
    with pytest.raises(SnapshotMismatchError, match="unknown frontier node"):
        executor.resume(
            ResumeRequest(
                failed,
                (ResumeFailedNodeRequest(GraphNodeId("missing"), UseRequestInput()),),
            )
        )
    with pytest.raises(SnapshotMismatchError, match="failure resume input"):
        executor.resume(
            ResumeRequest(
                failed,
                (
                    ResumeFailedNodeRequest(
                        GraphNodeId("a"),
                        cast(UseRequestInput | OverrideNodeInput[str], object()),
                    ),
                ),
            )
        )
    codec = TextCodec()
    codec_graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), echo),),
            (),
            (GraphNodeId("a"),),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    interrupted = replace(
        failed,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1),
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("a"),
                    InterruptedGraphNode(
                        GraphNodeInterrupt(
                            derive_graph_node_interrupt_identity(failed.run_id, failed.superstep, GraphNodeId("a"), 1),
                            GraphInterruptPayload(b"question"),
                        )
                    ),
                ),
            )
        ),
        execution_sequence=1,
    )
    identity = interrupted.frontier.nodes[0].settlement
    assert isinstance(identity, InterruptedGraphNode)
    interrupt_id = graph_interrupt_id(
        identity.interrupt.identity.run_id,
        identity.interrupt.identity.superstep,
        identity.interrupt.identity.node_id,
        identity.interrupt.identity.execution_generation,
    )
    with pytest.raises(SnapshotMismatchError, match="interrupt resume input"):
        GraphExecutor(codec_graph).resume(
            ResumeRequest(
                interrupted,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        interrupt_id,
                        cast(OverrideNodeInput[str], object()),
                    ),
                ),
            )
        )
    with pytest.raises(SnapshotMismatchError, match="unsupported action variant"):
        executor.resume(ResumeRequest(failed, (cast(ResumeNodeRequest[str], object()),)))


async def test_executor_indexes_a_shared_nested_definition_once() -> None:
    shared = GraphDefinition(
        GraphDefinitionId("shared.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("shared"), echo),),
        (),
        (GraphNodeId("shared"),),
    )
    middle = GraphDefinition(
        GraphDefinitionId("middle.graph"),
        GraphDefinitionVersion(1),
        (NestedGraphNodeDefinition(GraphNodeId("shared"), shared),),
        (),
        (GraphNodeId("shared"),),
    )
    root = compile_graph(
        GraphDefinition(
            GraphDefinitionId("root.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("a_middle"), middle),
                NestedGraphNodeDefinition(GraphNodeId("z_shared"), shared),
            ),
            (),
            (GraphNodeId("a_middle"), GraphNodeId("z_shared")),
        )
    )

    assert GraphExecutor(root).start_command(GraphRunId("run")).definition_id == root.definition_id


async def test_child_state_parent_must_name_a_compiled_parent_node() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("parent")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    assert isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)

    wrong_parent = replace(activation, node_id=GraphNodeId("not-a-parent-node"))
    wrong_parent_node = replace(
        child,
        run_id=child_graph_run_id(wrong_parent.run_id, wrong_parent.superstep, wrong_parent.node_id),
        parent=wrong_parent,
    )
    with pytest.raises(InvalidExecutionSnapshotError, match="compiled parent node"):
        await executor.prepare(StepRequest(wrong_parent_node, "input", ATTEMPT_ID, ()))


async def test_executor_distinguishes_root_and_nested_graph_authority() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("parent")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    assert isinstance(missing.action, StartMissingChildren)
    child_command = missing.action.children[0].command
    child = reduce_graph_run(None, child_command)

    root_with_parent = replace(
        parent,
        run_id=child_graph_run_id(activation.run_id, activation.superstep, activation.node_id),
        parent=activation,
    )
    with pytest.raises(SnapshotMismatchError, match="root graph state"):
        await executor.prepare(StepRequest(root_with_parent, "input", ATTEMPT_ID, ()))

    child_without_parent = replace(child, run_id=GraphRunId("standalone-child"), parent=None)
    with pytest.raises(SnapshotMismatchError, match="nested graph state"):
        await executor.prepare(StepRequest(child_without_parent, "input", ATTEMPT_ID, ()))

    nested_definition = graph.nodes[GraphNodeId("nested")]
    assert isinstance(nested_definition, NestedGraphNodeDefinition)
    child_executor = GraphExecutor(compile_graph(nested_definition.graph))
    standalone = reduce_graph_run(None, child_executor.start_command(GraphRunId("standalone-child")))
    disposition = await child_executor.prepare(StepRequest(standalone, "input", ATTEMPT_ID, ()))
    assert isinstance(disposition, ExecutableFrontier)


async def test_aborted_retained_frontier_still_requires_compiled_node_membership() -> None:
    graph = compiled_graph("a")
    executor = GraphExecutor(graph)
    running = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    aborted = reduce_graph_run(running, AbortGraphRun(running.revision, GraphAbortReason("stop")))
    unknown = replace(
        aborted,
        frontier=GraphFrontierState((GraphFrontierNode(GraphNodeId("unknown"), aborted.frontier.nodes[0].settlement),)),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match="unknown nodes"):
        await executor.prepare(StepRequest(unknown, "input", ATTEMPT_ID, ()))


async def test_prepare_disposition_payloads_reject_ambiguous_or_empty_shapes() -> None:
    graph = nested_graph()
    parent = running_state(frontier=("nested",))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    nested_definition = graph.nodes[GraphNodeId("nested")]
    assert isinstance(nested_definition, NestedGraphNodeDefinition)
    child_graph = compile_graph(nested_definition.graph)
    command = project_start_graph_command(
        child_graph,
        child_graph_run_id(activation.run_id, activation.superstep, activation.node_id),
        activation,
    )
    prepared_child = PreparedNestedRun(activation, child_graph, command)
    active_child = ActiveChild(activation, reduce_graph_run(None, command))

    with pytest.raises(ValueError, match="non-empty"):
        StartMissingChildren[str, str](())
    with pytest.raises(ValueError, match="non-empty"):
        WaitForActiveChildren(())
    with pytest.raises(ValueError, match="canonical"):
        StartMissingChildren((prepared_child, prepared_child))
    with pytest.raises(ValueError, match="canonical"):
        WaitForActiveChildren((active_child, active_child))

    admission = PreparedResourceAdmission(
        (),
        (),
        UpdateGraphResources(parent.revision, ResourceSnapshot(())),
    )
    claim = prepare_claim(ExecutionClaimOwner(), parent, ATTEMPT_ID, plan_tasks(graph, parent, ExecutionLimits()))
    with pytest.raises(ValueError, match="exactly one"):
        ExecutableFrontier()
    with pytest.raises(ValueError, match="exactly one"):
        ExecutableFrontier(admission, claim)
