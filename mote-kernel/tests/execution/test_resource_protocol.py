import asyncio

import pytest
from tests.execution.driver import (
    execute_claim,
    execute_step,
    reduce_claim_result,
    reduce_graph_command,
    step_request,
)

from mote_kernel.execution import ExecutedSuperstep, GraphExecutor, NestedTaskSuccess, PreparedFrontier, StepRequest
from mote_kernel.execution.claim import ExecutionClaimOwner
from mote_kernel.execution.engine.claim_stage import prepare_claim
from mote_kernel.execution.engine.frontier import prepare_frontier
from mote_kernel.execution.engine.superstep import execute_claimed_superstep
from mote_kernel.execution.errors import ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.graph import (
    END,
    CompiledGraph,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeFailure,
    NodeId,
    NodeSuccess,
    RouteId,
    compile_graph,
)
from mote_kernel.execution.graph.command import SelectRoute
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.execution.result import TaskFailure, TaskSuccess
from mote_kernel.execution.snapshot import ExecutionAttemptId
from mote_kernel.state.graph_state import (
    AcquireResources,
    CompleteGraphRun,
    FenceGraphExecution,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    ParticipantId,
    ResourceLock,
    ResourceSnapshot,
    reduce_graph_run,
    reduce_resources,
)
from mote_kernel.state.graph_state import GraphDefinitionId as StateDefinitionId
from mote_kernel.state.graph_state import GraphDefinitionVersion as StateDefinitionVersion

FILE = ResourceId("file")
DATABASE = ResourceId("database")
DIRECT_ATTEMPT = ExecutionAttemptId("direct-stage-test")


def state(*, frontier: tuple[str, ...] = ("a", "b"), resources: ResourceSnapshot | None = None) -> GraphRunState:
    return GraphRunState(
        GraphRunId("run"),
        StateDefinitionId("resource.graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        0,
        tuple(GraphNodeId(node_id) for node_id in frontier),
        resources=resources,
    )


async def _echo(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def _resource_and_nested_graph() -> CompiledGraph[str, str]:
    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child"), _echo),),
        (DirectEdge(NodeId("child"), END),),
        (NodeId("child"),),
    )
    return compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), _echo, (FILE,)), NestedGraphNodeDefinition(NodeId("b"), child)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )


def _resource_free_graph() -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), _echo),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )


@pytest.mark.asyncio
async def test_conflicting_resource_frontier_executes_all_waves_under_one_claim() -> None:
    calls: list[str] = []

    def node(name: str) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            return NodeSuccess(f"{name}:{node_input}")

        return NodeDefinition(NodeId(name), execute, (FILE,))

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (node("a"), node("b")),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = state()
    prepared = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert tuple(task.node_id for task in prepared.admission.admitted) == (NodeId("a"),)
    assert tuple(task.node_id for task in prepared.admission.waiting) == (NodeId("b"),)
    assert calls == []

    admitted = reduce_graph_command(initial, prepared.admission.command)
    claimed = await execute_claim(step_request(graph, admitted, "input"))

    assert isinstance(claimed.result, ExecutedSuperstep)
    assert tuple(result.task.node_id for result in claimed.result.results) == (NodeId("a"), NodeId("b"))
    assert calls == ["a", "b"]
    completed = reduce_claim_result(claimed)
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.resources is None
    assert completed.execution is None


@pytest.mark.asyncio
async def test_cancelling_a_resource_wave_keeps_the_committed_claim_fenced() -> None:
    completed_a = asyncio.Event()
    started_b = asyncio.Event()
    allow_b_completion = asyncio.Event()
    cancelled: list[str] = []
    calls: list[str] = []

    async def execute_a(node_input: str) -> NodeSuccess[str]:
        calls.append("a")
        completed_a.set()
        return NodeSuccess(f"a:{node_input}")

    async def execute_b(node_input: str) -> NodeSuccess[str]:
        calls.append("b")
        started_b.set()
        try:
            await allow_b_completion.wait()
        except asyncio.CancelledError:
            cancelled.append("b")
            raise
        return NodeSuccess(f"b:{node_input}")

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute_a, (FILE,)),
                NodeDefinition(NodeId("b"), execute_b, (DATABASE,)),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10), ResourceDefinition(DATABASE, 20)),
        )
    )
    executor = GraphExecutor(graph)
    initial = state()
    admission = await executor.prepare(StepRequest(initial, "input", DIRECT_ATTEMPT))
    assert admission.admission is not None
    admitted = reduce_graph_run(initial, admission.admission.command)
    prepared = await executor.prepare(StepRequest(admitted, "input", DIRECT_ATTEMPT))
    assert prepared.execution is not None
    claim = prepared.execution
    claimed = reduce_graph_run(admitted, claim.command)

    running = asyncio.create_task(executor.execute(claim, StepRequest(claimed, "input", DIRECT_ATTEMPT)))
    async with asyncio.timeout(2):
        await asyncio.gather(completed_a.wait(), started_b.wait())
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert sorted(calls) == ["a", "b"]
    assert cancelled == ["b"]
    assert claim.consumed
    assert claimed.execution is not None
    with pytest.raises(ResultCollectionError, match="already been consumed"):
        await executor.execute(claim, StepRequest(claimed, "input", DIRECT_ATTEMPT))
    fenced = reduce_graph_run(
        claimed,
        FenceGraphExecution(claimed.superstep, claimed.execution.token),
    )
    assert fenced.execution is None

    allow_b_completion.set()
    retry = await executor.prepare(StepRequest(fenced, "input", DIRECT_ATTEMPT))
    assert retry.execution is not None
    retried_claim = retry.execution
    retried_state = reduce_graph_run(fenced, retried_claim.command)
    retried = await executor.execute(
        retried_claim,
        StepRequest(retried_state, "input", DIRECT_ATTEMPT),
    )
    completed = reduce_graph_run(retried_state, retried.command)

    assert calls.count("a") == 2
    assert calls.count("b") == 2
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.execution is None
    assert completed.resources is None


@pytest.mark.asyncio
async def test_nonconflicting_resources_execute_in_one_wave() -> None:
    both_started = asyncio.Barrier(2)
    calls: list[str] = []

    def node(name: str, resource_id: ResourceId) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            await asyncio.wait_for(both_started.wait(), timeout=2)
            return NodeSuccess(node_input)

        return NodeDefinition(NodeId(name), execute, (resource_id,))

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                node("a", FILE),
                node("b", DATABASE),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10), ResourceDefinition(DATABASE, 20)),
        )
    )
    initial = state()
    prepared = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier) and prepared.admission is not None
    assert len(prepared.admission.admitted) == 2
    admitted = reduce_graph_command(initial, prepared.admission.command)

    executed = await execute_step(step_request(graph, admitted, "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert sorted(calls) == ["a", "b"]


@pytest.mark.asyncio
async def test_partially_held_multi_resource_task_waits_for_full_admission() -> None:
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()
    calls: list[str] = []

    async def hold_database(node_input: str) -> NodeSuccess[str]:
        calls.append("holder")
        holder_entered.set()
        await release_holder.wait()
        return NodeSuccess(node_input)

    async def use_both(node_input: str) -> NodeSuccess[str]:
        calls.append("multi")
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), hold_database, (DATABASE,)),
                NodeDefinition(NodeId("b"), use_both, (FILE, DATABASE)),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10), ResourceDefinition(DATABASE, 20)),
        )
    )
    initial = state()
    admission = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(admission, PreparedFrontier) and admission.admission is not None
    admitted = reduce_graph_command(initial, admission.admission.command)

    execution = asyncio.create_task(execute_step(step_request(graph, admitted, "input")))
    await holder_entered.wait()
    assert calls == ["holder"]
    release_holder.set()
    result = await execution

    assert isinstance(result, ExecutedSuperstep)
    assert calls == ["holder", "multi"]


@pytest.mark.asyncio
async def test_resource_node_exception_keeps_the_claim_for_explicit_fencing() -> None:
    calls: list[str] = []
    attempts = 0

    async def fail(node_input: str) -> NodeSuccess[str]:
        nonlocal attempts
        attempts += 1
        calls.append("a")
        if attempts == 1:
            raise RuntimeError("node exploded")
        return NodeSuccess(node_input)

    async def waiting(node_input: str) -> NodeSuccess[str]:
        calls.append("b")
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), fail, (FILE,)), NodeDefinition(NodeId("b"), waiting, (FILE,))),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    executor = GraphExecutor(graph)
    initial = state()
    admission = await executor.prepare(StepRequest(initial, "input", DIRECT_ATTEMPT))
    assert admission.admission is not None
    admitted = reduce_graph_run(initial, admission.admission.command)
    prepared = await executor.prepare(StepRequest(admitted, "input", DIRECT_ATTEMPT))
    assert prepared.execution is not None
    claim = prepared.execution
    claimed = reduce_graph_run(admitted, claim.command)

    with pytest.raises(RuntimeError, match="exploded"):
        await executor.execute(claim, StepRequest(claimed, "input", DIRECT_ATTEMPT))

    assert calls == ["a"]
    assert claim.consumed
    assert claimed.execution is not None
    fenced = reduce_graph_run(
        claimed,
        FenceGraphExecution(claimed.superstep, claimed.execution.token),
    )
    assert fenced.execution is None

    retry = await executor.prepare(StepRequest(fenced, "input", DIRECT_ATTEMPT))
    assert retry.execution is not None
    retried_claim = retry.execution
    retried_state = reduce_graph_run(fenced, retried_claim.command)
    retried = await executor.execute(
        retried_claim,
        StepRequest(retried_state, "input", DIRECT_ATTEMPT),
    )
    completed = reduce_graph_run(retried_state, retried.command)

    assert attempts == 2
    assert calls == ["a", "a", "b"]
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.resources is None


@pytest.mark.asyncio
async def test_resource_and_resource_free_tasks_share_the_claim() -> None:
    both_started = asyncio.Barrier(2)
    calls: list[str] = []

    def node(name: str, resources: tuple[ResourceId, ...] = ()) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            await asyncio.wait_for(both_started.wait(), timeout=2)
            return NodeSuccess(node_input)

        return NodeDefinition(NodeId(name), execute, resources)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (node("a", (FILE,)), node("b")),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = state()
    prepared = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier) and prepared.admission is not None
    admitted = reduce_graph_command(initial, prepared.admission.command)

    executed = await execute_step(step_request(graph, admitted, "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert sorted(calls) == ["a", "b"]


@pytest.mark.asyncio
async def test_active_execution_cannot_reenter_resource_admission() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute, (FILE,)),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = state(frontier=("a",))
    prepared = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier) and prepared.admission is not None
    admitted = reduce_graph_command(initial, prepared.admission.command)
    executor = GraphExecutor(graph)
    claim = await executor.prepare(step_request(graph, admitted, "input").execution_request())
    assert claim.execution is not None
    claimed = reduce_graph_command(admitted, claim.execution.command)

    with pytest.raises(SnapshotMismatchError, match="active execution lease"):
        await executor.prepare(step_request(graph, claimed, "input").execution_request())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["wrong-order", "stale-acquisition"])
async def test_committed_parallel_snapshot_must_match_graph_and_pending_tasks(case: str) -> None:
    graph = _resource_free_graph()
    if case == "wrong-order":
        resources = ResourceSnapshot((ResourceLock(DATABASE),))
        message = "resource order"
    else:
        resources = reduce_resources(
            ResourceSnapshot((ResourceLock(FILE),)),
            AcquireResources(ParticipantId("stale"), (FILE,)),
        )
        message = "outside pending resource tasks"

    with pytest.raises(ResultCollectionError, match=message):
        await execute_step(step_request(graph, state(frontier=("a",), resources=resources), "input"))


@pytest.mark.asyncio
async def test_claimed_stage_revalidates_pending_resource_participants() -> None:
    graph = _resource_free_graph()
    stale = reduce_resources(
        ResourceSnapshot((ResourceLock(FILE),)),
        AcquireResources(ParticipantId("stale"), (FILE,)),
    )
    stale_state = state(frontier=("a",), resources=stale)
    direct_request = StepRequest(stale_state, "input", DIRECT_ATTEMPT)
    direct_frontier = prepare_frontier(graph, direct_request)
    owner = ExecutionClaimOwner()
    direct_claim = prepare_claim(
        owner,
        stale_state,
        DIRECT_ATTEMPT,
        direct_frontier.pending_tasks,
    )
    claimed = reduce_graph_run(stale_state, direct_claim.command)
    await direct_claim.consume(owner, claimed, DIRECT_ATTEMPT)
    with pytest.raises(ResultCollectionError, match="outside pending resource tasks"):
        await execute_claimed_superstep(
            graph,
            StepRequest(claimed, "input", DIRECT_ATTEMPT),
            direct_claim,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resources",
    [None, ResourceSnapshot((ResourceLock(FILE),))],
)
async def test_resource_free_frontier_skips_admission_and_clears_prior_scheduler_state(
    resources: ResourceSnapshot | None,
) -> None:
    graph = _resource_free_graph()
    initial = state(frontier=("a",), resources=resources)
    executor = GraphExecutor(graph)

    prepared = await executor.prepare(StepRequest(initial, "input", DIRECT_ATTEMPT))

    assert prepared.admission is None
    assert prepared.execution is not None
    claimed = reduce_graph_run(initial, prepared.execution.command)
    result = await executor.execute(
        prepared.execution,
        StepRequest(claimed, "input", DIRECT_ATTEMPT),
    )
    completed = reduce_graph_run(claimed, result.command)
    assert completed.resources is None


@pytest.mark.asyncio
async def test_only_one_resource_claim_can_be_accepted_after_admission() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), _echo, (FILE,)),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    executor = GraphExecutor(graph)
    initial = state(frontier=("a",))
    admission = await executor.prepare(StepRequest(initial, "input", DIRECT_ATTEMPT))
    assert admission.admission is not None
    admitted = reduce_graph_run(initial, admission.admission.command)
    first = await executor.prepare(StepRequest(admitted, "input", DIRECT_ATTEMPT))
    second = await executor.prepare(StepRequest(admitted, "input", DIRECT_ATTEMPT))
    assert first.execution is not None
    assert second.execution is not None
    assert first.execution.command.attempt_id != second.execution.command.attempt_id

    claimed = reduce_graph_run(admitted, first.execution.command)

    with pytest.raises(GraphStateTransitionError, match="active execution lease"):
        reduce_graph_run(claimed, second.execution.command)
    with pytest.raises(ResultCollectionError, match="does not match committed"):
        await executor.execute(
            second.execution,
            StepRequest(claimed, "input", DIRECT_ATTEMPT),
        )
    assert not second.execution.consumed


@pytest.mark.asyncio
async def test_three_conflicting_resource_tasks_execute_once_in_fifo_order() -> None:
    calls: list[str] = []

    def node(name: str) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            return NodeSuccess(node_input)

        return NodeDefinition(NodeId(name), execute, (FILE,))

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (node("a"), node("b"), node("c")),
            tuple(DirectEdge(NodeId(name), END) for name in ("a", "b", "c")),
            tuple(NodeId(name) for name in ("a", "b", "c")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = state(frontier=("a", "b", "c"))
    admission = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(admission, PreparedFrontier) and admission.admission is not None
    admitted = reduce_graph_command(initial, admission.admission.command)

    result = await execute_claim(step_request(graph, admitted, "input"))

    assert calls == ["a", "b", "c"]
    assert len(result.result.results) == 3


@pytest.mark.asyncio
async def test_resource_free_task_executes_once_across_multiple_resource_waves() -> None:
    calls: list[str] = []

    def node(name: str, resources: tuple[ResourceId, ...]) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            return NodeSuccess(node_input)

        return NodeDefinition(NodeId(name), execute, resources)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (node("a", (FILE,)), node("b", (FILE,)), node("c", ())),
            tuple(DirectEdge(NodeId(name), END) for name in ("a", "b", "c")),
            tuple(NodeId(name) for name in ("a", "b", "c")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = state(frontier=("a", "b", "c"))
    admission = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(admission, PreparedFrontier) and admission.admission is not None
    admitted = reduce_graph_command(initial, admission.admission.command)

    await execute_claim(step_request(graph, admitted, "input"))

    assert calls.count("a") == 1
    assert calls.count("b") == 1
    assert calls.count("c") == 1


@pytest.mark.asyncio
async def test_claimed_resource_stage_rejects_missing_admission() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute, (FILE,)),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    missing_admission = state(
        frontier=("a",),
        resources=ResourceSnapshot((ResourceLock(FILE),)),
    )
    direct_request = StepRequest(missing_admission, "input", DIRECT_ATTEMPT)
    direct_frontier = prepare_frontier(graph, direct_request)
    owner = ExecutionClaimOwner()
    direct_claim = prepare_claim(
        owner,
        missing_admission,
        DIRECT_ATTEMPT,
        direct_frontier.pending_tasks,
    )
    claimed = reduce_graph_run(missing_admission, direct_claim.command)
    await direct_claim.consume(owner, claimed, DIRECT_ATTEMPT)

    with pytest.raises(ResultCollectionError, match="cannot advance"):
        await execute_claimed_superstep(
            graph,
            StepRequest(claimed, "input", DIRECT_ATTEMPT),
            direct_claim,
        )


@pytest.mark.asyncio
async def test_claimed_resource_stage_rejects_absent_parallel_snapshot() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute, (FILE,)),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    missing_admission = state(frontier=("a",))
    direct_request = StepRequest(missing_admission, "input", DIRECT_ATTEMPT)
    direct_frontier = prepare_frontier(graph, direct_request)
    owner = ExecutionClaimOwner()
    direct_claim = prepare_claim(
        owner,
        missing_admission,
        DIRECT_ATTEMPT,
        direct_frontier.pending_tasks,
    )
    claimed = reduce_graph_run(missing_admission, direct_claim.command)
    await direct_claim.consume(owner, claimed, DIRECT_ATTEMPT)

    with pytest.raises(ResultCollectionError, match="committed resources snapshot"):
        await execute_claimed_superstep(
            graph,
            StepRequest(claimed, "input", DIRECT_ATTEMPT),
            direct_claim,
        )


@pytest.mark.asyncio
async def test_failed_resource_node_settles_failure_after_releasing_scheduler_state() -> None:
    async def fail(node_input: str) -> NodeFailure:
        return NodeFailure(f"failed: {node_input}")

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), fail, (FILE,)), NodeDefinition(NodeId("b"), fail, (FILE,))),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = state()
    prepared = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier) and prepared.admission is not None
    admitted = reduce_graph_command(initial, prepared.admission.command)

    claimed = await execute_claim(step_request(graph, admitted, "input"))

    assert isinstance(claimed.result.results[0], TaskFailure)
    failed = reduce_claim_result(claimed)
    assert failed.status is GraphRunStatus.FAILED


@pytest.mark.asyncio
async def test_conditional_route_acquires_only_selected_target_resource() -> None:
    async def choose(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input, SelectRoute(RouteId("left")))

    async def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), choose),
                NodeDefinition(NodeId("left"), execute, (FILE,)),
                NodeDefinition(NodeId("right"), execute, (FILE,)),
            ),
            (
                ConditionalEdge(NodeId("a"), RouteId("left"), NodeId("left")),
                ConditionalEdge(NodeId("a"), RouteId("right"), NodeId("right")),
                DirectEdge(NodeId("left"), END),
                DirectEdge(NodeId("right"), END),
            ),
            (NodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    first = await execute_claim(step_request(graph, state(frontier=("a",)), "input"))
    next_state = reduce_claim_result(first)
    prepared = await execute_step(step_request(graph, next_state, "input"))
    assert isinstance(prepared, PreparedFrontier) and prepared.admission is not None
    assert tuple(task.node_id for task in prepared.admission.admitted) == (NodeId("left"),)


@pytest.mark.asyncio
async def test_resource_and_completed_nested_task_settle_one_frontier() -> None:
    graph = _resource_and_nested_graph()
    initial = state()
    prepared = await execute_step(step_request(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier) and prepared.admission is None
    nested = prepared.nested_runs[0]
    child_state = reduce_graph_run(None, nested.command)
    child_claimed = await execute_claim(step_request(nested.graph, child_state, "input"))
    child_result = child_claimed.result.results[0]
    assert isinstance(child_result, TaskSuccess)
    completed_child = reduce_claim_result(child_claimed)
    nested_result = NestedTaskSuccess(nested.parent_task.task_id, completed_child, child_result.output)
    admission = await execute_step(
        step_request(
            graph,
            initial,
            "input",
            nested_results=(nested_result,),
        )
    )
    assert isinstance(admission, PreparedFrontier) and admission.admission is not None
    admitted = reduce_graph_command(initial, admission.admission.command)

    executed = await execute_step(
        step_request(
            graph,
            admitted,
            "input",
            nested_results=(nested_result,),
        )
    )

    assert isinstance(executed, ExecutedSuperstep)
    assert isinstance(executed.command, CompleteGraphRun)
    assert tuple(result.task.node_id for result in executed.results) == (NodeId("a"), NodeId("b"))


@pytest.mark.asyncio
async def test_resource_frontier_prepares_nested_run_before_resource_admission() -> None:
    graph = _resource_and_nested_graph()
    initial = state()
    waiting = await execute_step(step_request(graph, initial, "input"))

    assert isinstance(waiting, PreparedFrontier)
    assert waiting.admission is None
    assert len(waiting.nested_runs) == 1
    assert waiting.execution is None


@pytest.mark.asyncio
async def test_claimed_stage_rejects_waiting_for_an_unstarted_nested_run() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child"), execute),),
        (DirectEdge(NodeId("child"), END),),
        (NodeId("child"),),
    )
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(NodeId("a"), child),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
        )
    )
    initial = state(frontier=("a",))
    direct_request = StepRequest(initial, "input", DIRECT_ATTEMPT)
    direct_frontier = prepare_frontier(graph, direct_request)
    owner = ExecutionClaimOwner()
    direct_claim = prepare_claim(
        owner,
        initial,
        DIRECT_ATTEMPT,
        direct_frontier.pending_tasks,
    )
    claimed = reduce_graph_run(initial, direct_claim.command)
    await direct_claim.consume(owner, claimed, DIRECT_ATTEMPT)

    with pytest.raises(ResultCollectionError, match="waiting for nested"):
        await execute_claimed_superstep(
            graph,
            StepRequest(claimed, "input", DIRECT_ATTEMPT),
            direct_claim,
        )
