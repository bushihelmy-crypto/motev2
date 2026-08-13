import asyncio
from contextvars import ContextVar
from dataclasses import FrozenInstanceError, dataclass, replace
from typing import cast

import pytest
from tests.execution.driver import ATTEMPT_ID, ClaimedStep, apply_claimed, apply_command, execute_step, step_request

from mote_kernel.execution import (
    AbortedChild,
    AbortedGraph,
    ActiveChild,
    AwaitingResume,
    CompletedChild,
    CompletedGraph,
    ExecutableFrontier,
    GraphExecutor,
    MissingChild,
    OverrideNodeInput,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
    StartMissingChildren,
    StepRequest,
    TaskFailure,
    TaskSuccess,
    WaitForActiveChildren,
    WaitingForChildren,
)
from mote_kernel.execution.errors import ExecutionLimitError, NodeExecutionContractError, ResultCollectionError
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
    NodeFailure,
    NodeInterrupt,
    NodeSuccess,
    ResumeInputBinding,
    SelectGraphRoute,
    compile_graph,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNodeOutcome,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphInterruptPayload,
    GraphJoinProgress,
    GraphResumeInputCodecId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    ParentGraphActivation,
    PendingGraphNode,
    SettleGraphExecution,
    SucceededGraphNodeOutcome,
    child_graph_run_id,
    graph_interrupt_id,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


async def echo(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def graph_with_nodes(
    *nodes: NodeDefinition[str, str],
    edges: tuple[DirectEdge, ...] = (),
    entries: tuple[str, ...] = ("a",),
) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            nodes,
            edges,
            tuple(GraphNodeId(node_id) for node_id in entries),
        )
    )


def started(executor: GraphExecutor[str, str], run_id: str = "run") -> GraphRunState:
    return reduce_graph_run(None, executor.start_command(GraphRunId(run_id)))


def complete_child(child: GraphRunState, attempt: str = "child") -> GraphRunState:
    node_ids = tuple(node.node_id for node in child.frontier.nodes)
    claimed = reduce_graph_run(
        child,
        ClaimGraphExecution(child.revision, GraphExecutionAttemptId(attempt), node_ids),
    )
    assert claimed.execution is not None
    return reduce_graph_run(
        claimed,
        SettleGraphExecution(
            claimed.revision,
            claimed.execution.token,
            tuple(SucceededGraphNodeOutcome(node_id, ContinueGraphRouting()) for node_id in node_ids),
            CompleteGraphFrontier(),
        ),
    )


async def test_start_prepare_execute_and_reduce_advance_without_mutating_prior_state() -> None:
    calls: list[str] = []

    async def execute(node_input: str) -> NodeSuccess[str]:
        calls.append(node_input)
        return NodeSuccess(node_input.upper())

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), execute),
        NodeDefinition(GraphNodeId("b"), execute),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")), DirectEdge(GraphNodeId("b"), END)),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)

    step = await execute_step(step_request(graph, initial, "input"))

    assert isinstance(step, ClaimedStep)
    assert calls == ["input"]
    assert isinstance(step.result.results[0], TaskSuccess)
    assert step.result.results[0].output == "INPUT"
    advanced = apply_claimed(step)
    assert advanced.superstep == 1
    assert tuple(node.node_id for node in advanced.frontier.nodes) == (GraphNodeId("b"),)
    assert initial.superstep == 0
    with pytest.raises(FrozenInstanceError):
        step.result.command.expected_revision = 99  # type: ignore[misc]


async def test_batch_executes_concurrently_and_results_are_canonical() -> None:
    barrier = asyncio.Barrier(2)
    completed: list[str] = []

    def node(name: str) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            await asyncio.wait_for(barrier.wait(), timeout=2)
            completed.append(name)
            return NodeSuccess(f"{name}:{node_input}")

        return NodeDefinition(GraphNodeId(name), execute)

    graph = graph_with_nodes(
        node("b"),
        node("a"),
        edges=(DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
        entries=("a", "b"),
    )
    step = await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))

    assert isinstance(step, ClaimedStep)
    assert sorted(completed) == ["a", "b"]
    assert tuple(result.task.node_id for result in step.result.results) == (GraphNodeId("a"), GraphNodeId("b"))
    assert apply_claimed(step).status is GraphRunStatus.COMPLETED


async def test_typed_failure_returns_awaiting_resume_and_does_not_retry() -> None:
    calls = 0

    async def fail(node_input: str) -> NodeFailure:
        nonlocal calls
        calls += 1
        return NodeFailure(GraphFailure(f"failed:{node_input}"))

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), fail))
    step = await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))
    assert isinstance(step, ClaimedStep)
    assert isinstance(step.result.command, SettleGraphExecution)
    assert step.result.command.outcomes == (FailedGraphNodeOutcome(GraphNodeId("a"), GraphFailure("failed:input")),)
    failed = apply_claimed(step)
    disposition = await GraphExecutor(graph).prepare(StepRequest(failed, "input", ATTEMPT_ID, ()))
    assert disposition == AwaitingResume((GraphNodeId("a"),), ())
    assert calls == 1


async def test_exception_keeps_exact_lease_for_explicit_fence() -> None:
    async def explode(node_input: str) -> NodeSuccess[str]:
        raise RuntimeError(node_input)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), explode))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "boom", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)

    with pytest.raises(RuntimeError, match="boom"):
        await executor.execute(prepared.claim, StepRequest(claimed, "boom", ATTEMPT_ID, ()))

    assert claimed.execution is not None
    assert prepared.claim.consumed


async def test_claim_is_one_shot_and_bound_to_owner_request_and_reducer_state() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claim = prepared.claim
    with pytest.raises(ResultCollectionError, match="committed"):
        await executor.execute(claim, StepRequest(initial, "input", ATTEMPT_ID, ()))
    claimed = apply_command(initial, claim.command)
    await executor.execute(claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    with pytest.raises(ResultCollectionError, match="already"):
        await executor.execute(claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))


async def test_parallel_limit_fails_before_invocation() -> None:
    calls = 0

    async def counted(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), counted),
        NodeDefinition(GraphNodeId("b"), counted),
        entries=("a", "b"),
    )
    with pytest.raises(ExecutionLimitError, match="parallel"):
        await GraphExecutor(graph).prepare(
            StepRequest(
                started(GraphExecutor(graph)),
                "input",
                ATTEMPT_ID,
                (),
                ExecutionLimits(max_parallel_tasks=1),
            )
        )
    assert calls == 0


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
            (
                NodeDefinition(GraphNodeId("ordinary"), echo),
                NestedGraphNodeDefinition(GraphNodeId("nested"), child),
            ),
            (DirectEdge(GraphNodeId("nested"), END), DirectEdge(GraphNodeId("ordinary"), END)),
            (GraphNodeId("nested"), GraphNodeId("ordinary")),
        )
    )


async def test_missing_then_active_child_blocks_ordinary_sibling_without_partial_claim() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))

    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    assert isinstance(missing.action, StartMissingChildren)
    child_command = missing.action.children[0].command
    assert child_command.run_id == child_graph_run_id(parent.run_id, parent.superstep, GraphNodeId("nested"))
    child = reduce_graph_run(None, child_command)

    active = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (ActiveChild(activation, child),)))
    assert isinstance(active, WaitingForChildren)
    assert isinstance(active.action, WaitForActiveChildren)


async def test_interrupted_child_resumes_same_run_before_parent_sibling_can_execute() -> None:
    calls = {"child": 0, "ordinary": 0}

    class Codec:
        def encode(self, value: str) -> bytes:
            return value.encode()

        def decode(self, payload: bytes) -> str:
            return payload.decode()

    codec = Codec()

    async def child_node(node_input: str):
        calls["child"] += 1
        if calls["child"] == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        return NodeSuccess(node_input)

    async def ordinary(node_input: str) -> NodeSuccess[str]:
        calls["ordinary"] += 1
        return NodeSuccess(node_input)

    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), child_node),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
        resume_input=ResumeInputBinding(GraphResumeInputCodecId("utf8.v1"), 1, codec, codec),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("parent.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("nested"), child),
                NodeDefinition(GraphNodeId("ordinary"), ordinary),
            ),
            (DirectEdge(GraphNodeId("nested"), END), DirectEdge(GraphNodeId("ordinary"), END)),
            (GraphNodeId("nested"), GraphNodeId("ordinary")),
        )
    )
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child_state = reduce_graph_run(None, missing.action.children[0].command)
    child_prepared = await executor.prepare(StepRequest(child_state, "initial", ATTEMPT_ID, ()))
    assert isinstance(child_prepared, ExecutableFrontier) and child_prepared.claim is not None
    child_claimed = apply_command(child_state, child_prepared.claim.command)
    child_result = await executor.execute(
        child_prepared.claim,
        StepRequest(child_claimed, "initial", ATTEMPT_ID, ()),
    )
    interrupted_child = apply_command(child_claimed, child_result.command)

    waiting = await executor.prepare(
        StepRequest(parent, "input", ATTEMPT_ID, (ActiveChild(activation, interrupted_child),))
    )
    assert isinstance(waiting, WaitingForChildren)
    assert calls == {"child": 1, "ordinary": 0}
    interrupted_node = interrupted_child.frontier.nodes[0].settlement
    assert isinstance(interrupted_node, InterruptedGraphNode)
    identity = interrupted_node.interrupt.identity
    resumed_child = apply_command(
        interrupted_child,
        executor.resume(
            ResumeRequest(
                interrupted_child,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("child"),
                        graph_interrupt_id(
                            identity.run_id,
                            identity.superstep,
                            identity.node_id,
                            identity.execution_generation,
                        ),
                        OverrideNodeInput("answer"),
                    ),
                ),
            )
        ),
    )
    assert resumed_child.run_id == child_state.run_id
    resumed_prepared = await executor.prepare(StepRequest(resumed_child, "ignored", ATTEMPT_ID, ()))
    assert isinstance(resumed_prepared, ExecutableFrontier) and resumed_prepared.claim is not None
    resumed_claimed = apply_command(resumed_child, resumed_prepared.claim.command)
    resumed_result = await executor.execute(
        resumed_prepared.claim,
        StepRequest(resumed_claimed, "ignored", ATTEMPT_ID, ()),
    )
    completed_child = apply_command(resumed_claimed, resumed_result.command)

    parent_step = await execute_step(
        step_request(
            graph,
            parent,
            "parent-input",
            (CompletedChild(activation, completed_child, "child-output", ContinueGraphRouting()),),
        )
    )
    assert isinstance(parent_step, ClaimedStep)
    assert calls == {"child": 2, "ordinary": 1}
    assert apply_claimed(parent_step).status is GraphRunStatus.COMPLETED


async def test_completed_and_aborted_children_project_parent_typed_outcomes() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)
    child_claim = reduce_graph_run(
        child,
        ClaimGraphExecution(
            child.revision,
            GraphExecutionAttemptId("child"),
            (GraphNodeId("child"),),
        ),
    )
    assert child_claim.execution is not None
    completed_child = reduce_graph_run(
        child_claim,
        SettleGraphExecution(
            child_claim.revision,
            child_claim.execution.token,
            (SucceededGraphNodeOutcome(GraphNodeId("child"), ContinueGraphRouting()),),
            CompleteGraphFrontier(),
        ),
    )
    completed = await execute_step(
        step_request(
            graph,
            parent,
            "input",
            (CompletedChild(activation, completed_child, "child-output", ContinueGraphRouting()),),
        )
    )
    assert isinstance(completed, ClaimedStep)
    assert any(
        isinstance(result, TaskSuccess) and result.output == "child-output" for result in completed.result.results
    )

    aborted_child = reduce_graph_run(child, AbortGraphRun(child.revision, GraphAbortReason("child aborted")))
    aborted = await execute_step(step_request(graph, parent, "input", (AbortedChild(activation, aborted_child),)))
    assert isinstance(aborted, ClaimedStep)
    assert any(
        isinstance(result, TaskFailure) and result.failure == GraphFailure("child aborted")
        for result in aborted.result.results
    )


async def test_completed_child_preserves_parent_conditional_routing() -> None:
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
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child_state = complete_child(reduce_graph_run(None, missing.action.children[0].command))
    projection = CompletedChild(
        activation,
        child_state,
        "child-output",
        SelectGraphRoute(GraphRouteId("right")),
    )

    step = await execute_step(step_request(graph, parent, "input", (projection,)))

    assert isinstance(step, ClaimedStep)
    advanced = apply_claimed(step)
    assert tuple(node.node_id for node in advanced.frontier.nodes) == (GraphNodeId("right"),)


async def test_completed_and_aborted_child_projections_require_terminal_state() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    active_child = reduce_graph_run(None, missing.action.children[0].command)

    with pytest.raises(ResultCollectionError, match="completed child"):
        await executor.prepare(
            StepRequest(
                parent,
                "input",
                ATTEMPT_ID,
                (CompletedChild(activation, active_child, "forged", ContinueGraphRouting()),),
            )
        )
    with pytest.raises(ResultCollectionError, match="aborted child"):
        await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (AbortedChild(activation, active_child),)))


async def test_partial_sibling_child_states_require_a_complete_projection_closure() -> None:
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
    parent = started(executor)
    a = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("a"))
    b = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("b"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(a), MissingChild(b))))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    first_child = complete_child(reduce_graph_run(None, missing.action.children[0].command), "child-a")

    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        await executor.prepare(
            StepRequest(
                parent,
                "input",
                ATTEMPT_ID,
                (CompletedChild(a, first_child, "a-output", ContinueGraphRouting()),),
            )
        )

    remaining = await executor.prepare(
        StepRequest(
            parent,
            "input",
            ATTEMPT_ID,
            (
                CompletedChild(a, first_child, "a-output", ContinueGraphRouting()),
                MissingChild(b),
            ),
        )
    )
    assert isinstance(remaining, WaitingForChildren) and isinstance(remaining.action, StartMissingChildren)
    assert tuple(item.parent for item in remaining.action.children) == (b,)
    assert remaining.action.children[0].command.run_id == missing.action.children[1].command.run_id

    second_child = complete_child(reduce_graph_run(None, remaining.action.children[0].command), "child-b")
    step = await execute_step(
        step_request(
            graph,
            parent,
            "input",
            (
                CompletedChild(a, first_child, "a-output", ContinueGraphRouting()),
                CompletedChild(b, second_child, "b-output", ContinueGraphRouting()),
            ),
        )
    )
    assert isinstance(step, ClaimedStep)
    assert tuple(result.output for result in step.result.results if isinstance(result, TaskSuccess)) == (
        "a-output",
        "b-output",
    )
    assert apply_claimed(step).status is GraphRunStatus.COMPLETED


async def test_claimed_nested_frontier_requires_fresh_complete_child_projections() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    completed_child = complete_child(reduce_graph_run(None, missing.action.children[0].command))
    projection = CompletedChild(activation, completed_child, "output", ContinueGraphRouting())
    prepared = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (projection,)))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(parent, prepared.claim.command)

    with pytest.raises(ResultCollectionError, match="exactly and canonically"):
        await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    assert prepared.claim.consumed
    assert claimed.execution is not None


async def test_nested_graph_can_prepare_a_grandchild_with_exact_parent_coordinates() -> None:
    leaf = GraphDefinition(
        GraphDefinitionId("leaf.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("leaf"), echo),),
        (DirectEdge(GraphNodeId("leaf"), END),),
        (GraphNodeId("leaf"),),
    )
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NestedGraphNodeDefinition(GraphNodeId("child"), leaf),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    root = compile_graph(
        GraphDefinition(
            GraphDefinitionId("root.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(GraphNodeId("root"), child),),
            (DirectEdge(GraphNodeId("root"), END),),
            (GraphNodeId("root"),),
        )
    )
    executor = GraphExecutor(root)
    root_state = started(executor)
    child_activation = ParentGraphActivation(root_state.run_id, 0, GraphNodeId("root"))
    child_wait = await executor.prepare(StepRequest(root_state, "input", ATTEMPT_ID, (MissingChild(child_activation),)))
    assert isinstance(child_wait, WaitingForChildren) and isinstance(child_wait.action, StartMissingChildren)
    child_state = reduce_graph_run(None, child_wait.action.children[0].command)
    grandchild_activation = ParentGraphActivation(child_state.run_id, 0, GraphNodeId("child"))

    grandchild_wait = await executor.prepare(
        StepRequest(child_state, "input", ATTEMPT_ID, (MissingChild(grandchild_activation),))
    )
    assert isinstance(grandchild_wait, WaitingForChildren)
    assert isinstance(grandchild_wait.action, StartMissingChildren)
    grandchild = grandchild_wait.action.children[0]
    assert grandchild.command.parent == grandchild_activation
    assert grandchild.command.run_id == child_graph_run_id(
        child_state.run_id,
        child_state.superstep,
        GraphNodeId("child"),
    )
    assert grandchild.command.run_id != child_state.run_id


async def test_nested_completion_participates_in_a_cross_superstep_join() -> None:
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
                NodeDefinition(GraphNodeId("b"), echo),
                NodeDefinition(GraphNodeId("joined"), echo),
            ),
            (
                DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
                JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("joined")),
                DirectEdge(GraphNodeId("joined"), END),
            ),
            (GraphNodeId("a"),),
        )
    )
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("a"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child_state = complete_child(reduce_graph_run(None, missing.action.children[0].command))
    first = await execute_step(
        step_request(
            graph,
            parent,
            "input",
            (CompletedChild(activation, child_state, "child-output", ContinueGraphRouting()),),
        )
    )
    assert isinstance(first, ClaimedStep)
    after_child = apply_claimed(first)
    expected_progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("joined"),
        frozenset({GraphNodeId("a")}),
    )
    assert tuple(node.node_id for node in after_child.frontier.nodes) == (GraphNodeId("b"),)
    assert after_child.join_progress == (expected_progress,)

    second = await execute_step(step_request(graph, after_child, "input"))
    assert isinstance(second, ClaimedStep)
    after_b = apply_claimed(second)
    assert tuple(node.node_id for node in after_b.frontier.nodes) == (GraphNodeId("joined"),)
    assert after_b.join_progress == ()


async def test_child_start_preserves_all_canonical_entry_nodes() -> None:
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (
            NodeDefinition(GraphNodeId("c"), echo),
            NodeDefinition(GraphNodeId("a"), echo),
            NodeDefinition(GraphNodeId("b"), echo),
        ),
        (
            DirectEdge(GraphNodeId("a"), END),
            DirectEdge(GraphNodeId("b"), END),
            DirectEdge(GraphNodeId("c"), END),
        ),
        (GraphNodeId("c"), GraphNodeId("a"), GraphNodeId("b")),
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
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))

    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child_state = reduce_graph_run(None, missing.action.children[0].command)
    assert tuple(node.node_id for node in child_state.frontier.nodes) == (
        GraphNodeId("a"),
        GraphNodeId("b"),
        GraphNodeId("c"),
    )


async def test_same_nested_activation_replays_identity_and_new_superstep_changes_it() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    first_activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    first = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(first_activation),)))
    replay = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(first_activation),)))
    assert isinstance(first, WaitingForChildren) and isinstance(first.action, StartMissingChildren)
    assert replay == first

    next_parent = replace(parent, superstep=1)
    next_activation = ParentGraphActivation(next_parent.run_id, 1, GraphNodeId("nested"))
    next_step = await executor.prepare(StepRequest(next_parent, "input", ATTEMPT_ID, (MissingChild(next_activation),)))
    assert isinstance(next_step, WaitingForChildren) and isinstance(next_step.action, StartMissingChildren)
    assert next_step.action.children[0].command.run_id != first.action.children[0].command.run_id


async def test_terminal_dispositions_do_not_enter_planning() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    completed_step = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(completed_step, ClaimedStep)
    completed = apply_claimed(completed_step)
    assert isinstance(await executor.prepare(StepRequest(completed, "x", ATTEMPT_ID, ())), CompletedGraph)
    aborted = reduce_graph_run(initial, AbortGraphRun(initial.revision, GraphAbortReason("stop")))
    assert isinstance(await executor.prepare(StepRequest(aborted, "x", ATTEMPT_ID, ())), AbortedGraph)


async def test_concurrent_runs_share_one_executor_without_cross_run_state() -> None:
    barrier = asyncio.Barrier(2)

    async def execute(node_input: str) -> NodeSuccess[str]:
        await asyncio.wait_for(barrier.wait(), timeout=2)
        return NodeSuccess(node_input)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), execute))
    executor = GraphExecutor(graph)
    first_state = started(executor, "first-run")
    second_state = started(executor, "second-run")

    first_prepared, second_prepared = await asyncio.gather(
        executor.prepare(StepRequest(first_state, "first", ATTEMPT_ID, ())),
        executor.prepare(StepRequest(second_state, "second", ATTEMPT_ID, ())),
    )
    assert isinstance(first_prepared, ExecutableFrontier) and first_prepared.claim is not None
    assert isinstance(second_prepared, ExecutableFrontier) and second_prepared.claim is not None
    first_claimed = apply_command(first_state, first_prepared.claim.command)
    second_claimed = apply_command(second_state, second_prepared.claim.command)

    first, second = await asyncio.gather(
        executor.execute(first_prepared.claim, StepRequest(first_claimed, "first", ATTEMPT_ID, ())),
        executor.execute(second_prepared.claim, StepRequest(second_claimed, "second", ATTEMPT_ID, ())),
    )

    assert isinstance(first.results[0], TaskSuccess)
    assert isinstance(second.results[0], TaskSuccess)
    assert first.results[0].output == "first"
    assert second.results[0].output == "second"
    assert first.results[0].task.task_id != second.results[0].task.task_id
    assert first.command != second.command


async def test_parallel_nodes_receive_copied_caller_context() -> None:
    trace_id = ContextVar("trace_id", default="missing")

    async def read(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(f"{trace_id.get()}:{node_input}")

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), read),
        NodeDefinition(GraphNodeId("b"), read),
        entries=("a", "b"),
    )
    token = trace_id.set("trace")
    try:
        step = await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))
    finally:
        trace_id.reset(token)

    assert isinstance(step, ClaimedStep)
    assert tuple(result.output for result in step.result.results if isinstance(result, TaskSuccess)) == (
        "trace:input",
        "trace:input",
    )


async def test_parallel_context_changes_are_isolated_from_siblings_and_caller() -> None:
    trace_id = ContextVar("trace_id", default="missing")
    barrier = asyncio.Barrier(2)

    def node(name: str) -> NodeDefinition[str, str]:
        async def change(node_input: str) -> NodeSuccess[str]:
            assert trace_id.get() == "caller"
            trace_id.set(name)
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return NodeSuccess(f"{trace_id.get()}:{node_input}")

        return NodeDefinition(GraphNodeId(name), change)

    graph = graph_with_nodes(node("a"), node("b"), entries=("a", "b"))
    token = trace_id.set("caller")
    try:
        step = await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))
        assert trace_id.get() == "caller"
    finally:
        trace_id.reset(token)

    assert isinstance(step, ClaimedStep)
    assert tuple(result.output for result in step.result.results if isinstance(result, TaskSuccess)) == (
        "a:input",
        "b:input",
    )


async def test_single_node_context_change_does_not_leak_to_caller() -> None:
    trace_id = ContextVar("trace_id", default="missing")

    async def change(node_input: str) -> NodeSuccess[str]:
        assert trace_id.get() == "caller"
        trace_id.set("node")
        return NodeSuccess(f"{trace_id.get()}:{node_input}")

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), change))
    token = trace_id.set("caller")
    try:
        step = await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))
        assert trace_id.get() == "caller"
    finally:
        trace_id.reset(token)

    assert isinstance(step, ClaimedStep)
    assert isinstance(step.result.results[0], TaskSuccess)
    assert step.result.results[0].output == "node:input"


async def test_parallel_nodes_share_the_same_frozen_request_input() -> None:
    @dataclass(frozen=True, slots=True)
    class InputSnapshot:
        value: str

    barrier = asyncio.Barrier(2)
    observed: list[InputSnapshot] = []

    async def read(node_input: InputSnapshot) -> NodeSuccess[str]:
        observed.append(node_input)
        await asyncio.wait_for(barrier.wait(), timeout=2)
        return NodeSuccess(node_input.value)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), read),
                NodeDefinition(GraphNodeId("b"), read),
            ),
            (),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    node_input = InputSnapshot("input")

    step = await execute_step(
        step_request(graph, reduce_graph_run(None, GraphExecutor(graph).start_command(GraphRunId("run"))), node_input)
    )

    assert isinstance(step, ClaimedStep)
    assert len(observed) == 2
    assert all(item is node_input for item in observed)
    with pytest.raises(FrozenInstanceError):
        observed[0].value = "changed"  # type: ignore[misc]


async def test_parallel_exceptions_propagate_first_task_identity_deterministically() -> None:
    release_a = asyncio.Event()
    completions: list[str] = []

    async def fail_a(node_input: str) -> NodeSuccess[str]:
        await asyncio.wait_for(release_a.wait(), timeout=2)
        completions.append("a")
        raise ValueError(node_input)

    async def fail_b(node_input: str) -> NodeSuccess[str]:
        completions.append("b")
        release_a.set()
        raise RuntimeError(node_input)

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("b"), fail_b),
        NodeDefinition(GraphNodeId("a"), fail_a),
        entries=("a", "b"),
    )

    with pytest.raises(ValueError, match="input"):
        await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))
    assert completions == ["b", "a"]


async def test_cancelling_parallel_execution_waits_for_every_node_cleanup() -> None:
    started_a = asyncio.Event()
    started_b = asyncio.Event()
    never_complete = asyncio.Event()
    cancelled: list[str] = []
    cleaned: list[str] = []

    def node(name: str, entered: asyncio.Event) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            entered.set()
            try:
                await never_complete.wait()
            except asyncio.CancelledError:
                cancelled.append(name)
                raise
            finally:
                await asyncio.sleep(0)
                cleaned.append(name)
            return NodeSuccess(node_input)

        return NodeDefinition(GraphNodeId(name), execute)

    graph = graph_with_nodes(node("a", started_a), node("b", started_b), entries=("a", "b"))
    running = asyncio.create_task(execute_step(step_request(graph, started(GraphExecutor(graph)), "input")))
    async with asyncio.timeout(2):
        await asyncio.gather(started_a.wait(), started_b.wait())
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert sorted(cancelled) == ["a", "b"]
    assert sorted(cleaned) == ["a", "b"]


async def test_python_exception_waits_for_successful_sibling_but_creates_no_settlement() -> None:
    exploded = asyncio.Event()
    completed: list[str] = []

    async def finish(node_input: str) -> NodeSuccess[str]:
        await exploded.wait()
        completed.append("a")
        return NodeSuccess(node_input)

    async def fail(node_input: str) -> NodeSuccess[str]:
        completed.append("b")
        exploded.set()
        raise RuntimeError(node_input)

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), finish),
        NodeDefinition(GraphNodeId("b"), fail),
        entries=("a", "b"),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)

    with pytest.raises(RuntimeError, match="input"):
        await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    assert completed == ["b", "a"]
    assert claimed.execution is not None


async def test_python_exception_prevents_partial_typed_failure_settlement() -> None:
    barrier = asyncio.Barrier(2)
    completed: list[str] = []

    async def typed_failure(node_input: str) -> NodeFailure:
        await asyncio.wait_for(barrier.wait(), timeout=2)
        completed.append("a")
        return NodeFailure(GraphFailure(f"typed:{node_input}"))

    async def explode(node_input: str) -> NodeSuccess[str]:
        await asyncio.wait_for(barrier.wait(), timeout=2)
        completed.append("b")
        raise RuntimeError(node_input)

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), typed_failure),
        NodeDefinition(GraphNodeId("b"), explode),
        entries=("a", "b"),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)

    with pytest.raises(RuntimeError, match="input"):
        await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))

    assert sorted(completed) == ["a", "b"]
    assert claimed.execution is not None
    assert all(isinstance(node.settlement, PendingGraphNode) for node in claimed.frontier.nodes)


async def test_typed_failure_and_success_are_both_settled_after_one_invocation() -> None:
    barrier = asyncio.Barrier(2)
    calls = {"a": 0, "b": 0}

    async def succeed(node_input: str) -> NodeSuccess[str]:
        calls["a"] += 1
        await asyncio.wait_for(barrier.wait(), timeout=2)
        return NodeSuccess(node_input)

    async def fail(node_input: str) -> NodeFailure:
        calls["b"] += 1
        await asyncio.wait_for(barrier.wait(), timeout=2)
        return NodeFailure(GraphFailure(f"failed:{node_input}"))

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), succeed),
        NodeDefinition(GraphNodeId("b"), fail),
        entries=("a", "b"),
    )

    step = await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))

    assert isinstance(step, ClaimedStep)
    assert calls == {"a": 1, "b": 1}
    assert isinstance(step.result.command, SettleGraphExecution)
    assert step.result.command.outcomes == (
        SucceededGraphNodeOutcome(GraphNodeId("a"), ContinueGraphRouting()),
        FailedGraphNodeOutcome(GraphNodeId("b"), GraphFailure("failed:input")),
    )


async def test_node_return_outside_typed_contract_fails_closed() -> None:
    async def invalid(node_input: str) -> NodeSuccess[str]:
        return cast(NodeSuccess[str], node_input)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), invalid))

    with pytest.raises(NodeExecutionContractError, match="unsupported outcome"):
        await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))


async def test_node_success_subclass_satisfies_runtime_contract() -> None:
    class SpecializedSuccess(NodeSuccess[str]):
        pass

    async def succeed(node_input: str) -> NodeSuccess[str]:
        return SpecializedSuccess(node_input)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), succeed))

    step = await execute_step(step_request(graph, started(GraphExecutor(graph)), "input"))

    assert isinstance(step, ClaimedStep)
    assert isinstance(step.result.results[0], TaskSuccess)
    assert step.result.results[0].output == "input"
