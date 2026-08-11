import asyncio
from contextvars import ContextVar
from dataclasses import FrozenInstanceError, dataclass

import pytest

from mote_kernel.execution import (
    ExecutedSuperstep,
    NestedTaskFailure,
    NestedTaskSuccess,
    PreparedFrontier,
    StepRequest,
    step_graph,
)
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.task import TaskId
from mote_kernel.execution.errors import ExecutionLimitError, NodeExecutionContractError, ResultCollectionError
from mote_kernel.execution.graph import (
    END,
    CompiledGraph,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    JoinEdge,
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeFailure,
    NodeId,
    NodeSuccess,
    RouteId,
    compile_graph,
)
from mote_kernel.execution.graph.command import SelectRoute
from mote_kernel.execution.graph_run import project_execution_snapshot
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskSuccess
from mote_kernel.state.graph_state import (
    AdvanceGraphRun,
    CompleteGraphRun,
    FailGraphRun,
    GraphFailure,
    GraphJoinProgress,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphTaskId,
    ParentGraphTask,
    StartGraphRun,
    reduce_graph_run,
)
from mote_kernel.state.graph_state import (
    GraphDefinitionId as StateDefinitionId,
)
from mote_kernel.state.graph_state import (
    GraphDefinitionVersion as StateDefinitionVersion,
)

pytestmark = pytest.mark.asyncio


def state(*, frontier: tuple[str, ...] = ("a",), superstep: int = 0) -> GraphRunState:
    return GraphRunState(
        GraphRunId("run"),
        StateDefinitionId("test.graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        superstep,
        tuple(GraphNodeId(node_id) for node_id in frontier),
    )


def child_state(
    command: StartGraphRun,
    *,
    failure: str | None = None,
) -> GraphRunState:
    started = reduce_graph_run(None, command)
    if failure is None:
        return reduce_graph_run(started, CompleteGraphRun(0))
    return reduce_graph_run(started, FailGraphRun(0, GraphFailure(failure)))


async def test_step_executes_direct_node_once_and_proposes_advance_without_mutating_state() -> None:
    calls: list[str] = []

    async def first(node_input: str) -> NodeSuccess[str]:
        calls.append(node_input)
        return NodeSuccess(node_input.upper())

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), first), NodeDefinition(NodeId("b"), first)),
            (DirectEdge(NodeId("a"), NodeId("b")), DirectEdge(NodeId("b"), END)),
            (NodeId("a"),),
        )
    )
    committed = state()

    executed = await step_graph(StepRequest(graph, committed, "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert calls == ["input"]
    assert executed.command == AdvanceGraphRun(0, (GraphNodeId("b"),))
    assert isinstance(executed.results[0], TaskSuccess)
    assert executed.results[0].output == "INPUT"
    assert committed.superstep == 0
    assert committed.frontier == (GraphNodeId("a"),)
    with pytest.raises(FrozenInstanceError):
        executed.command.expected_superstep = 1  # type: ignore[misc]


async def test_step_executes_frontier_concurrently_and_collects_results_in_deterministic_order() -> None:
    both_started = asyncio.Barrier(2)
    release_a = asyncio.Event()
    completions: list[str] = []

    def node(name: str) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            await asyncio.wait_for(both_started.wait(), timeout=2)
            if name == "a":
                assert await asyncio.wait_for(release_a.wait(), timeout=2)
            completions.append(name)
            if name == "b":
                release_a.set()
            return NodeSuccess(f"{name}:{node_input}")

        return NodeDefinition(NodeId(name), execute)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (node("b"), node("a")),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )

    executed = await step_graph(StepRequest(graph, state(frontier=("b", "a")), "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert completions == ["b", "a"]
    assert tuple(result.task.node_id for result in executed.results) == (NodeId("a"), NodeId("b"))
    assert tuple(result.output for result in executed.results if isinstance(result, TaskSuccess)) == (
        "a:input",
        "b:input",
    )
    assert executed.command == CompleteGraphRun(0)


async def test_concurrent_runs_share_compiled_graph_without_cross_run_state() -> None:
    both_runs_started = asyncio.Barrier(2)

    async def execute(node_input: str) -> NodeSuccess[str]:
        await asyncio.wait_for(both_runs_started.wait(), timeout=2)
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )
    other_run = GraphRunState(
        GraphRunId("other-run"),
        StateDefinitionId("test.graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        0,
        (GraphNodeId("a"),),
    )

    first, second = await asyncio.gather(
        step_graph(StepRequest(graph, state(), "first")),
        step_graph(StepRequest(graph, other_run, "second")),
    )

    assert isinstance(first, ExecutedSuperstep)
    assert isinstance(second, ExecutedSuperstep)
    assert first.command == second.command == CompleteGraphRun(0)
    assert isinstance(first.results[0], TaskSuccess)
    assert isinstance(second.results[0], TaskSuccess)
    assert first.results[0].output == "first"
    assert second.results[0].output == "second"
    assert first.results[0].task.task_id != second.results[0].task.task_id


async def test_step_enforces_custom_parallel_limit_before_scheduling_nodes() -> None:
    calls: list[str] = []

    async def execute(node_input: str) -> NodeSuccess[str]:
        calls.append(node_input)
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute), NodeDefinition(NodeId("b"), execute)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )

    with pytest.raises(ExecutionLimitError, match="parallel"):
        await step_graph(
            StepRequest(
                graph,
                state(frontier=("a", "b")),
                "input",
                limits=ExecutionLimits(max_parallel_tasks=1),
            )
        )

    assert calls == []


async def test_parallel_frontier_copies_context_into_each_node_invocation() -> None:
    trace_id = ContextVar("trace_id", default="missing")

    async def read_trace(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(f"{trace_id.get()}:{node_input}")

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), read_trace), NodeDefinition(NodeId("b"), read_trace)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    token = trace_id.set("trace")
    try:
        executed = await step_graph(StepRequest(graph, state(frontier=("a", "b")), "input"))
    finally:
        trace_id.reset(token)

    assert isinstance(executed, ExecutedSuperstep)
    assert tuple(result.output for result in executed.results if isinstance(result, TaskSuccess)) == (
        "trace:input",
        "trace:input",
    )


async def test_parallel_node_context_changes_are_isolated_from_siblings_and_caller() -> None:
    trace_id = ContextVar("trace_id", default="missing")
    both_changed = asyncio.Barrier(2)

    def node(name: str) -> NodeDefinition[str, str]:
        async def change_trace(node_input: str) -> NodeSuccess[str]:
            assert trace_id.get() == "caller"
            trace_id.set(name)
            await asyncio.wait_for(both_changed.wait(), timeout=2)
            return NodeSuccess(f"{trace_id.get()}:{node_input}")

        return NodeDefinition(NodeId(name), change_trace)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (node("a"), node("b")),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    token = trace_id.set("caller")
    try:
        executed = await step_graph(StepRequest(graph, state(frontier=("a", "b")), "input"))
        assert trace_id.get() == "caller"
    finally:
        trace_id.reset(token)

    assert isinstance(executed, ExecutedSuperstep)
    assert tuple(result.output for result in executed.results if isinstance(result, TaskSuccess)) == (
        "a:input",
        "b:input",
    )


async def test_single_node_context_changes_do_not_leak_to_step_caller() -> None:
    trace_id = ContextVar("trace_id", default="missing")

    async def change_trace(node_input: str) -> NodeSuccess[str]:
        assert trace_id.get() == "caller"
        trace_id.set("node")
        return NodeSuccess(f"{trace_id.get()}:{node_input}")

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), change_trace),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )
    token = trace_id.set("caller")
    try:
        executed = await step_graph(StepRequest(graph, state(), "input"))
        assert trace_id.get() == "caller"
    finally:
        trace_id.reset(token)

    assert isinstance(executed, ExecutedSuperstep)
    assert isinstance(executed.results[0], TaskSuccess)
    assert executed.results[0].output == "node:input"


async def test_parallel_nodes_share_one_frozen_read_only_input_snapshot() -> None:
    @dataclass(frozen=True, slots=True)
    class InputSnapshot:
        value: str

    both_started = asyncio.Barrier(2)
    observed: list[InputSnapshot] = []

    async def read_snapshot(node_input: InputSnapshot) -> NodeSuccess[str]:
        observed.append(node_input)
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return NodeSuccess(node_input.value)

    graph = compile_graph(
        GraphDefinition[InputSnapshot, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), read_snapshot), NodeDefinition(NodeId("b"), read_snapshot)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    node_input = InputSnapshot("input")

    executed = await step_graph(StepRequest(graph, state(frontier=("a", "b")), node_input))

    assert isinstance(executed, ExecutedSuperstep)
    assert len(observed) == 2
    assert all(item is node_input for item in observed)
    with pytest.raises(FrozenInstanceError):
        observed[0].value = "changed"  # type: ignore[misc]


async def test_step_preserves_typed_conditional_routing() -> None:
    async def choose(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input, SelectRoute(RouteId("right")))

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            tuple(NodeDefinition(NodeId(node_id), choose) for node_id in ("a", "left", "right")),
            (
                ConditionalEdge(NodeId("a"), RouteId("left"), NodeId("left")),
                ConditionalEdge(NodeId("a"), RouteId("right"), NodeId("right")),
            ),
            (NodeId("a"),),
        )
    )

    executed = await step_graph(StepRequest(graph, state(), "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert executed.command == AdvanceGraphRun(0, (GraphNodeId("right"),))


async def test_node_failure_becomes_fail_command_without_retry() -> None:
    calls = 0

    async def fail(node_input: str) -> NodeFailure:
        nonlocal calls
        calls += 1
        return NodeFailure(f"failed: {node_input}")

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), fail),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    executed = await step_graph(StepRequest(graph, state(superstep=3), "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert calls == 1
    assert executed.command == FailGraphRun(3, GraphFailure("failed: input"))
    assert executed.results == (TaskFailure(executed.results[0].task, "failed: input"),)


async def test_unexpected_node_exception_propagates_without_retry_or_conversion() -> None:
    calls = 0

    async def explode(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        raise ValueError(node_input)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), explode),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    with pytest.raises(ValueError, match="input"):
        await step_graph(StepRequest(graph, state(), "input"))

    assert calls == 1


async def test_parallel_frontier_propagates_first_task_exception_deterministically() -> None:
    release_a = asyncio.Event()
    completions: list[str] = []

    async def fail_a(node_input: str) -> NodeSuccess[str]:
        assert await asyncio.wait_for(release_a.wait(), timeout=2)
        await asyncio.sleep(0)
        completions.append("a")
        raise ValueError(node_input)

    async def fail_b(node_input: str) -> NodeSuccess[str]:
        completions.append("b")
        release_a.set()
        raise RuntimeError(node_input)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("b"), fail_b), NodeDefinition(NodeId("a"), fail_a)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )

    with pytest.raises(ValueError, match="input"):
        await step_graph(StepRequest(graph, state(frontier=("a", "b")), "input"))

    assert completions == ["b", "a"]


async def test_cancelling_parallel_frontier_cancels_every_node_task() -> None:
    started_a = asyncio.Event()
    started_b = asyncio.Event()
    never_complete = asyncio.Event()
    cancelled: list[str] = []
    cleaned: list[str] = []

    def node(name: str, started: asyncio.Event) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            started.set()
            try:
                await never_complete.wait()
            except asyncio.CancelledError:
                cancelled.append(name)
                raise
            finally:
                await asyncio.sleep(0)
                cleaned.append(name)
            return NodeSuccess(node_input)

        return NodeDefinition(NodeId(name), execute)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (node("a", started_a), node("b", started_b)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    execution = asyncio.create_task(step_graph(StepRequest(graph, state(frontier=("a", "b")), "input")))
    async with asyncio.timeout(2):
        await asyncio.gather(started_a.wait(), started_b.wait())
    execution.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execution

    assert sorted(cancelled) == ["a", "b"]
    assert sorted(cleaned) == ["a", "b"]


async def test_mixed_success_and_failure_settles_failure_after_each_task_runs_once() -> None:
    failure_returned = asyncio.Event()
    completions: list[str] = []

    async def success(node_input: str) -> NodeSuccess[str]:
        assert await asyncio.wait_for(failure_returned.wait(), timeout=2)
        completions.append("a")
        return NodeSuccess(node_input)

    async def failure(node_input: str) -> NodeFailure:
        completions.append("b")
        failure_returned.set()
        return NodeFailure(f"failed: {node_input}")

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), success), NodeDefinition(NodeId("b"), failure)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )

    executed = await step_graph(StepRequest(graph, state(frontier=("b", "a")), "input"))

    assert completions == ["b", "a"]
    assert isinstance(executed, ExecutedSuperstep)
    assert executed.command == FailGraphRun(0, GraphFailure("failed: input"))


async def test_node_return_outside_typed_contract_fails_closed() -> None:
    async def invalid(node_input: str) -> NodeSuccess[str]:
        return node_input  # type: ignore[return-value]

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), invalid),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    with pytest.raises(NodeExecutionContractError):
        await step_graph(StepRequest(graph, state(), "input"))


async def test_node_success_subclass_satisfies_runtime_contract() -> None:
    class SpecializedSuccess(NodeSuccess[str]):
        pass

    async def succeed(node_input: str) -> NodeSuccess[str]:
        return SpecializedSuccess(node_input)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), succeed),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    executed = await step_graph(StepRequest(graph, state(), "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert executed.command == CompleteGraphRun(0)
    assert isinstance(executed.results[0], TaskSuccess)
    assert executed.results[0].output == "input"


async def test_nested_graph_prepares_child_run_then_settles_parent_from_child_result() -> None:
    async def child_node(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child"), child_node),),
        (DirectEdge(NodeId("child"), END),),
        (NodeId("child"),),
    )
    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(NodeId("a"), child),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    prepared = await step_graph(StepRequest(parent, state(), "input"))

    assert isinstance(prepared, PreparedFrontier)
    assert len(prepared.nested_runs) == 1
    nested_run = prepared.nested_runs[0]
    started_child_state = reduce_graph_run(None, nested_run.command)
    assert started_child_state.parent == ParentGraphTask(GraphRunId("run"), GraphTaskId(nested_run.parent_task.task_id))
    executed_child = await step_graph(StepRequest(nested_run.graph, started_child_state, "input"))
    assert isinstance(executed_child, ExecutedSuperstep)
    assert executed_child.command == CompleteGraphRun(0)
    child_result = executed_child.results[0]
    assert isinstance(child_result, TaskSuccess)

    executed_parent = await step_graph(
        StepRequest(
            parent,
            state(),
            "input",
            nested_results=(
                NestedTaskSuccess(
                    nested_run.parent_task.task_id,
                    child_state(nested_run.command),
                    child_result.output,
                ),
            ),
        )
    )

    assert isinstance(executed_parent, ExecutedSuperstep)
    assert executed_parent.command == CompleteGraphRun(0)
    assert executed_parent.results == (TaskSuccess(nested_run.parent_task, "input"),)


def nested_parent() -> tuple[CompiledGraph[str, str], GraphDefinition[str, str]]:
    async def child_node(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child"), child_node),),
        (DirectEdge(NodeId("child"), END),),
        (NodeId("child"),),
    )
    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(NodeId("a"), child),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )
    return parent, child


async def test_nested_graph_failure_fails_parent_task() -> None:
    parent, _ = nested_parent()
    prepared = await step_graph(StepRequest(parent, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    parent_task = prepared.nested_runs[0].parent_task

    executed = await step_graph(
        StepRequest(
            parent,
            state(),
            "input",
            nested_results=(
                NestedTaskFailure(
                    parent_task.task_id,
                    child_state(prepared.nested_runs[0].command, failure="child failed"),
                    "child failed",
                ),
            ),
        )
    )

    assert isinstance(executed, ExecutedSuperstep)
    assert executed.command == FailGraphRun(0, GraphFailure("child failed"))


async def test_nested_graph_result_preserves_parent_conditional_routing() -> None:
    _, child = nested_parent()

    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(NodeId("a"), child),
                NodeDefinition(NodeId("left"), identity),
                NodeDefinition(NodeId("right"), identity),
            ),
            (
                ConditionalEdge(NodeId("a"), RouteId("left"), NodeId("left")),
                ConditionalEdge(NodeId("a"), RouteId("right"), NodeId("right")),
            ),
            (NodeId("a"),),
        )
    )
    prepared = await step_graph(StepRequest(parent, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    nested_run = prepared.nested_runs[0]
    parent_task = nested_run.parent_task

    executed = await step_graph(
        StepRequest(
            parent,
            state(),
            "input",
            nested_results=(
                NestedTaskSuccess(
                    parent_task.task_id,
                    child_state(nested_run.command),
                    "output",
                    SelectRoute(RouteId("right")),
                ),
            ),
        )
    )

    assert isinstance(executed, ExecutedSuperstep)
    assert executed.command == AdvanceGraphRun(0, (GraphNodeId("right"),))


async def test_nested_results_must_have_unique_task_identity() -> None:
    parent, _ = nested_parent()
    prepared = await step_graph(StepRequest(parent, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    result = NestedTaskSuccess(
        prepared.nested_runs[0].parent_task.task_id,
        child_state(prepared.nested_runs[0].command),
        "output",
    )

    with pytest.raises(ResultCollectionError, match="unique"):
        await step_graph(StepRequest(parent, state(), "input", nested_results=(result, result)))


async def test_unknown_nested_result_fails_before_preparing_missing_child() -> None:
    parent, _ = nested_parent()

    with pytest.raises(ResultCollectionError, match="unknown parent"):
        await step_graph(
            StepRequest(
                parent,
                state(),
                "input",
                nested_results=(NestedTaskSuccess(TaskId("unknown"), state(), "output"),),
            )
        )


async def test_nested_result_with_forged_child_run_identity_fails_closed() -> None:
    parent, _ = nested_parent()
    prepared = await step_graph(StepRequest(parent, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    parent_task = prepared.nested_runs[0].parent_task

    with pytest.raises(ResultCollectionError, match="child run identity"):
        await step_graph(
            StepRequest(
                parent,
                state(),
                "input",
                nested_results=(
                    NestedTaskSuccess(
                        parent_task.task_id,
                        GraphRunState(
                            GraphRunId("forged-child"),
                            StateDefinitionId("child.graph"),
                            StateDefinitionVersion(1),
                            GraphRunStatus.COMPLETED,
                            0,
                            (),
                            ParentGraphTask(GraphRunId("run"), GraphTaskId(parent_task.task_id)),
                        ),
                        "output",
                    ),
                ),
            )
        )


@pytest.mark.parametrize(
    "status",
    (GraphRunStatus.RUNNING, GraphRunStatus.SUSPENDED, GraphRunStatus.FAILED),
)
async def test_nested_success_requires_committed_completed_child_state(status: GraphRunStatus) -> None:
    parent, _ = nested_parent()
    prepared = await step_graph(StepRequest(parent, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    nested_run = prepared.nested_runs[0]
    started = reduce_graph_run(None, nested_run.command)
    candidate = GraphRunState(
        started.run_id,
        started.definition_id,
        started.definition_version,
        status,
        started.superstep,
        started.frontier if status is not GraphRunStatus.FAILED else (),
        started.parent,
        GraphFailure("child failed") if status is GraphRunStatus.FAILED else None,
    )

    with pytest.raises(ResultCollectionError, match="committed completed"):
        await step_graph(
            StepRequest(
                parent,
                state(),
                "input",
                nested_results=(NestedTaskSuccess(nested_run.parent_task.task_id, candidate, "output"),),
            )
        )


async def test_nested_failure_must_match_committed_failed_child_state() -> None:
    parent, _ = nested_parent()
    prepared = await step_graph(StepRequest(parent, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    nested_run = prepared.nested_runs[0]

    with pytest.raises(ResultCollectionError, match="committed failed"):
        await step_graph(
            StepRequest(
                parent,
                state(),
                "input",
                nested_results=(
                    NestedTaskFailure(
                        nested_run.parent_task.task_id,
                        child_state(nested_run.command, failure="committed failure"),
                        "forged failure",
                    ),
                ),
            )
        )


@pytest.mark.parametrize(
    ("definition_id", "definition_version", "parent_link", "message"),
    (
        (StateDefinitionId("other.graph"), StateDefinitionVersion(1), None, "definition"),
        (StateDefinitionId("child.graph"), StateDefinitionVersion(2), None, "definition"),
        (
            StateDefinitionId("child.graph"),
            StateDefinitionVersion(1),
            ParentGraphTask(GraphRunId("other-parent"), GraphTaskId("other-task")),
            "parent task",
        ),
    ),
)
async def test_nested_result_must_match_committed_child_ownership(
    definition_id: StateDefinitionId,
    definition_version: StateDefinitionVersion,
    parent_link: ParentGraphTask | None,
    message: str,
) -> None:
    parent, _ = nested_parent()
    prepared = await step_graph(StepRequest(parent, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    nested_run = prepared.nested_runs[0]
    completed = child_state(nested_run.command)
    mismatched = GraphRunState(
        completed.run_id,
        definition_id,
        definition_version,
        completed.status,
        completed.superstep,
        completed.frontier,
        completed.parent if parent_link is None else parent_link,
    )

    with pytest.raises(ResultCollectionError, match=message):
        await step_graph(
            StepRequest(
                parent,
                state(),
                "input",
                nested_results=(NestedTaskSuccess(nested_run.parent_task.task_id, mismatched, "output"),),
            )
        )


async def test_scheduler_rejects_missing_nested_result_when_called_directly() -> None:
    parent, _ = nested_parent()
    execution_snapshot = project_execution_snapshot(state())
    tasks = plan_tasks(parent, execution_snapshot, ExecutionLimits())

    with pytest.raises(ResultCollectionError, match="exactly cover"):
        await execute_tasks(parent, tasks, "input")


async def test_scheduler_accepts_an_empty_committed_batch() -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), identity),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    assert await execute_tasks(graph, (), "input") == ()


async def test_sibling_invocations_of_same_child_definition_have_distinct_run_identities() -> None:
    _, child = nested_parent()
    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(NodeId("a"), child),
                NestedGraphNodeDefinition(NodeId("b"), child),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )

    prepared = await step_graph(StepRequest(parent, state(frontier=("b", "a")), "input"))

    assert isinstance(prepared, PreparedFrontier)
    assert tuple(run.parent_task.node_id for run in prepared.nested_runs) == (NodeId("a"), NodeId("b"))
    assert len({run.command.run_id for run in prepared.nested_runs}) == 2


async def test_partially_completed_nested_frontier_prepares_only_missing_child() -> None:
    _, child = nested_parent()
    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(NodeId("a"), child),
                NestedGraphNodeDefinition(NodeId("b"), child),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    first = await step_graph(StepRequest(parent, state(frontier=("a", "b")), "input"))
    assert isinstance(first, PreparedFrontier)
    completed = first.nested_runs[0]

    remaining = await step_graph(
        StepRequest(
            parent,
            state(frontier=("a", "b")),
            "input",
            nested_results=(
                NestedTaskSuccess(completed.parent_task.task_id, child_state(completed.command), "a output"),
            ),
        )
    )

    assert isinstance(remaining, PreparedFrontier)
    assert len(remaining.nested_runs) == 1
    assert remaining.nested_runs[0].parent_task.node_id == NodeId("b")
    assert remaining.nested_runs[0].command.run_id == first.nested_runs[1].command.run_id

    settled = await step_graph(
        StepRequest(
            parent,
            state(frontier=("a", "b")),
            "input",
            nested_results=(
                NestedTaskSuccess(completed.parent_task.task_id, child_state(completed.command), "a output"),
                NestedTaskSuccess(
                    remaining.nested_runs[0].parent_task.task_id,
                    child_state(remaining.nested_runs[0].command),
                    "b output",
                ),
            ),
        )
    )

    assert isinstance(settled, ExecutedSuperstep)
    assert tuple(result.task.node_id for result in settled.results) == (NodeId("a"), NodeId("b"))
    assert settled.command == CompleteGraphRun(0)


async def test_mixed_nested_success_and_failure_fails_parent_deterministically() -> None:
    _, child = nested_parent()

    async def after(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(NodeId("a"), child),
                NestedGraphNodeDefinition(NodeId("b"), child),
                NodeDefinition(NodeId("after"), after),
            ),
            (
                DirectEdge(NodeId("a"), NodeId("after")),
                DirectEdge(NodeId("b"), NodeId("after")),
                DirectEdge(NodeId("after"), END),
            ),
            (NodeId("a"), NodeId("b")),
        )
    )
    prepared = await step_graph(StepRequest(parent, state(frontier=("a", "b")), "input"))
    assert isinstance(prepared, PreparedFrontier)
    first, second = prepared.nested_runs
    success = NestedTaskSuccess(first.parent_task.task_id, child_state(first.command), "a output")
    failure = NestedTaskFailure(
        second.parent_task.task_id,
        child_state(second.command, failure="b failed"),
        "b failed",
    )

    forward = await step_graph(
        StepRequest(parent, state(frontier=("a", "b")), "input", nested_results=(success, failure))
    )
    reversed_result = await step_graph(
        StepRequest(parent, state(frontier=("a", "b")), "input", nested_results=(failure, success))
    )

    assert isinstance(forward, ExecutedSuperstep)
    assert isinstance(reversed_result, ExecutedSuperstep)
    assert forward == reversed_result
    assert forward.command == FailGraphRun(0, GraphFailure("b failed"))
    assert tuple(result.task.node_id for result in forward.results) == (NodeId("a"), NodeId("b"))


async def test_replaying_same_parent_task_prepares_identical_child_run() -> None:
    parent, _ = nested_parent()
    request = StepRequest(parent, state(superstep=4), "input")

    first = await step_graph(request)
    second = await step_graph(request)

    assert isinstance(first, PreparedFrontier)
    assert isinstance(second, PreparedFrontier)
    assert first == second
    assert first.nested_runs[0].command.run_id == second.nested_runs[0].command.run_id


async def test_regular_sibling_waits_for_nested_child_and_executes_once_when_child_settles() -> None:
    calls: list[str] = []

    async def regular(node_input: str) -> NodeSuccess[str]:
        calls.append(node_input)
        return NodeSuccess(node_input)

    _, child = nested_parent()
    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(NodeId("a"), child),
                NodeDefinition(NodeId("b"), regular),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    committed = state(frontier=("b", "a"))

    prepared = await step_graph(StepRequest(parent, committed, "input"))

    assert isinstance(prepared, PreparedFrontier)
    assert calls == []
    nested_run = prepared.nested_runs[0]
    parent_task = nested_run.parent_task

    executed = await step_graph(
        StepRequest(
            parent,
            committed,
            "input",
            nested_results=(NestedTaskSuccess(parent_task.task_id, child_state(nested_run.command), "child output"),),
        )
    )

    assert isinstance(executed, ExecutedSuperstep)
    assert calls == ["input"]
    assert tuple(result.task.node_id for result in executed.results) == (NodeId("a"), NodeId("b"))
    assert executed.command == CompleteGraphRun(0)


async def test_stale_nested_completion_from_prior_superstep_is_rejected() -> None:
    parent, _ = nested_parent()
    prior = await step_graph(StepRequest(parent, state(superstep=1), "input"))
    assert isinstance(prior, PreparedFrontier)
    stale = NestedTaskSuccess(
        prior.nested_runs[0].parent_task.task_id,
        child_state(prior.nested_runs[0].command),
        "output",
    )

    with pytest.raises(ResultCollectionError, match="unknown parent"):
        await step_graph(StepRequest(parent, state(superstep=2), "input", nested_results=(stale,)))


async def test_nested_graph_can_recursively_prepare_a_grandchild_run() -> None:
    async def leaf_node(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    leaf = GraphDefinition[str, str](
        GraphDefinitionId("leaf.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("leaf"), leaf_node),),
        (DirectEdge(NodeId("leaf"), END),),
        (NodeId("leaf"),),
    )
    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NestedGraphNodeDefinition(NodeId("child"), leaf),),
        (DirectEdge(NodeId("child"), END),),
        (NodeId("child"),),
    )
    root = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(NodeId("a"), child),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    child_prepared = await step_graph(StepRequest(root, state(), "input"))
    assert isinstance(child_prepared, PreparedFrontier)
    child_run = child_prepared.nested_runs[0]
    child_state = reduce_graph_run(None, child_run.command)
    grandchild_prepared = await step_graph(StepRequest(child_run.graph, child_state, "input"))

    assert isinstance(grandchild_prepared, PreparedFrontier)
    grandchild_run = grandchild_prepared.nested_runs[0]
    assert grandchild_run.command.parent == ParentGraphTask(
        child_state.run_id, GraphTaskId(grandchild_run.parent_task.task_id)
    )
    assert grandchild_run.command.run_id != child_run.command.run_id


async def test_nested_completion_participates_in_cross_superstep_join() -> None:
    _, child = nested_parent()

    async def regular(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(NodeId("a"), child),
                NodeDefinition(NodeId("b"), regular),
                NodeDefinition(NodeId("joined"), regular),
            ),
            (
                DirectEdge(NodeId("a"), NodeId("b")),
                JoinEdge((NodeId("a"), NodeId("b")), NodeId("joined")),
                DirectEdge(NodeId("joined"), END),
            ),
            (NodeId("a"),),
        )
    )
    prepared = await step_graph(StepRequest(parent, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    nested_run = prepared.nested_runs[0]

    first = await step_graph(
        StepRequest(
            parent,
            state(),
            "input",
            nested_results=(
                NestedTaskSuccess(
                    nested_run.parent_task.task_id,
                    child_state(nested_run.command),
                    "child output",
                ),
            ),
        )
    )

    assert isinstance(first, ExecutedSuperstep)
    expected_progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("joined"),
        frozenset({GraphNodeId("a")}),
    )
    assert first.command == AdvanceGraphRun(0, (GraphNodeId("b"),), (expected_progress,))
    second_state = GraphRunState(
        GraphRunId("run"),
        StateDefinitionId("test.graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        1,
        (GraphNodeId("b"),),
        join_progress=(expected_progress,),
    )

    second = await step_graph(StepRequest(parent, second_state, "input"))

    assert isinstance(second, ExecutedSuperstep)
    assert second.command == AdvanceGraphRun(1, (GraphNodeId("joined"),))


async def test_nested_graph_start_preserves_all_sorted_child_entries() -> None:
    async def child_node(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (
            NodeDefinition(NodeId("c"), child_node),
            NodeDefinition(NodeId("a"), child_node),
            NodeDefinition(NodeId("b"), child_node),
        ),
        (
            DirectEdge(NodeId("a"), END),
            DirectEdge(NodeId("b"), END),
            DirectEdge(NodeId("c"), END),
        ),
        (NodeId("c"), NodeId("a"), NodeId("b")),
    )
    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(NodeId("a"), child),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )

    prepared = await step_graph(StepRequest(parent, state(), "input"))

    assert isinstance(prepared, PreparedFrontier)
    assert prepared.nested_runs[0].command.frontier == (GraphNodeId("a"), GraphNodeId("b"), GraphNodeId("c"))
