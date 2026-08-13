import asyncio
from dataclasses import replace

import pytest
from tests.execution.driver import ATTEMPT_ID, apply_command

from mote_kernel.execution import (
    ActiveChild,
    AwaitingResume,
    CompletedChild,
    ExecutableFrontier,
    GraphExecutor,
    MissingChild,
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeRequest,
    StartMissingChildren,
    StepRequest,
    TaskFailure,
    TaskInterrupt,
    WaitingForChildren,
)
from mote_kernel.execution.errors import ResultCollectionError
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
    NodeFailure,
    NodeInterrupt,
    NodeSuccess,
    ResumeInputBinding,
    SelectGraphRoute,
    compile_graph,
)
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FenceGraphExecution,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphInterruptPayload,
    GraphResumeInputCodecId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    ParentGraphActivation,
    ResourceAcquisition,
    ResourceLock,
    ResourceSnapshot,
    ResourceTransitionError,
    SettleGraphExecution,
    SucceededGraphNodeOutcome,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio
FILE = ResourceId("file")
DATABASE = ResourceId("database")


class Utf8Codec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


async def echo(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def resource_graph(*nodes: NodeDefinition[str, str]) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            nodes,
            tuple(DirectEdge(node.node_id, END) for node in nodes),
            tuple(node.node_id for node in nodes),
            (ResourceDefinition(FILE, 10), ResourceDefinition(DATABASE, 20)),
        )
    )


def started(executor: GraphExecutor[str, str]) -> GraphRunState:
    return reduce_graph_run(None, executor.start_command(GraphRunId("run")))


async def admitted_state(
    graph: CompiledGraph[str, str],
    executor: GraphExecutor[str, str],
    initial: GraphRunState,
) -> tuple[ExecutableFrontier, GraphRunState]:
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    assert prepared.admission is not None
    return prepared, apply_command(initial, prepared.admission.command)


async def test_conflicting_resources_execute_all_waves_under_one_all_pending_claim() -> None:
    calls: list[str] = []

    def node(name: str) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            return NodeSuccess(f"{name}:{node_input}")

        return NodeDefinition(GraphNodeId(name), execute, (FILE,))

    graph = resource_graph(node("a"), node("b"))
    executor = GraphExecutor(graph)
    initial = started(executor)
    admission, admitted = await admitted_state(graph, executor, initial)

    assert admission.admission is not None
    assert admission.admission.admitted_node_ids == (GraphNodeId("a"),)
    assert admission.admission.waiting_node_ids == (GraphNodeId("b"),)
    assert admitted.resources is not None
    assert tuple(item.node_id for item in admitted.resources.acquisitions) == (
        GraphNodeId("a"),
        GraphNodeId("b"),
    )

    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    assert prepared.claim.command.node_ids == (GraphNodeId("a"), GraphNodeId("b"))
    claimed = apply_command(admitted, prepared.claim.command)
    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    completed = apply_command(claimed, result.command)

    assert calls == ["a", "b"]
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.resources is None
    assert completed.execution is None


async def test_resource_free_sibling_shares_batch_without_fake_acquisition() -> None:
    barrier = asyncio.Barrier(2)
    calls: list[str] = []

    def node(name: str, resources: tuple[ResourceId, ...]) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return NodeSuccess(node_input)

        return NodeDefinition(GraphNodeId(name), execute, resources)

    graph = resource_graph(node("a", (FILE,)), node("b", ()))
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    assert admitted.resources is not None
    assert tuple(item.node_id for item in admitted.resources.acquisitions) == (GraphNodeId("a"),)

    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    assert prepared.claim.command.node_ids == (GraphNodeId("a"), GraphNodeId("b"))
    claimed = apply_command(admitted, prepared.claim.command)
    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))

    assert sorted(calls) == ["a", "b"]
    assert apply_command(claimed, result.command).status is GraphRunStatus.COMPLETED


async def test_resource_free_frontier_accepts_and_clears_empty_scheduler_snapshot() -> None:
    graph = resource_graph(NodeDefinition(GraphNodeId("free"), echo))
    executor = GraphExecutor(graph)
    initial = replace(
        started(executor),
        resources=ResourceSnapshot((ResourceLock(FILE), ResourceLock(DATABASE))),
    )

    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)
    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    completed = apply_command(claimed, result.command)

    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.resources is None


async def test_resource_exception_retains_lease_and_admission_until_exact_fence() -> None:
    calls: list[str] = []
    attempts = 0

    async def fail_once(node_input: str) -> NodeSuccess[str]:
        nonlocal attempts
        attempts += 1
        calls.append("a")
        if attempts == 1:
            raise RuntimeError(node_input)
        return NodeSuccess(node_input)

    async def waiting(node_input: str) -> NodeSuccess[str]:
        calls.append("b")
        return NodeSuccess(node_input)

    graph = resource_graph(
        NodeDefinition(GraphNodeId("a"), fail_once, (FILE,)),
        NodeDefinition(GraphNodeId("b"), waiting, (FILE,)),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    prepared = await executor.prepare(StepRequest(admitted, "explode", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)

    with pytest.raises(RuntimeError, match="explode"):
        await executor.execute(prepared.claim, StepRequest(claimed, "explode", ATTEMPT_ID, ()))

    assert calls == ["a"]
    assert claimed.execution is not None and claimed.resources is not None
    fenced = apply_command(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    assert fenced.execution is None and fenced.resources is None

    _, readmitted = await admitted_state(graph, executor, fenced)
    retry = await executor.prepare(StepRequest(readmitted, "retry", ATTEMPT_ID, ()))
    assert isinstance(retry, ExecutableFrontier) and retry.claim is not None
    retry_state = apply_command(readmitted, retry.claim.command)
    result = await executor.execute(retry.claim, StepRequest(retry_state, "retry", ATTEMPT_ID, ()))
    assert apply_command(retry_state, result.command).status is GraphRunStatus.COMPLETED
    assert calls == ["a", "a", "b"]


async def test_cancelled_resource_execution_keeps_committed_state_for_fence() -> None:
    started_task = asyncio.Event()
    blocked = asyncio.Event()

    async def wait_forever(node_input: str) -> NodeSuccess[str]:
        started_task.set()
        await blocked.wait()
        return NodeSuccess(node_input)

    graph = resource_graph(NodeDefinition(GraphNodeId("a"), wait_forever, (FILE,)))
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)
    running = asyncio.create_task(executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ())))
    await asyncio.wait_for(started_task.wait(), timeout=2)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert claimed.execution is not None and claimed.resources is not None
    fenced = apply_command(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    assert fenced.execution is None and fenced.resources is None


def nested_resource_graph() -> CompiledGraph[str, str]:
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("resource"), echo, (FILE,)),
                NestedGraphNodeDefinition(GraphNodeId("nested"), child),
            ),
            (DirectEdge(GraphNodeId("resource"), END), DirectEdge(GraphNodeId("nested"), END)),
            (GraphNodeId("nested"), GraphNodeId("resource")),
            (ResourceDefinition(FILE, 10),),
        )
    )


async def test_missing_child_precedes_admission_then_only_resource_node_participates() -> None:
    graph = nested_resource_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)
    child_claim = reduce_graph_run(
        child,
        ClaimGraphExecution(child.revision, GraphExecutionAttemptId("child"), (GraphNodeId("child"),)),
    )
    assert child_claim.execution is not None
    child_completed = reduce_graph_run(
        child_claim,
        SettleGraphExecution(
            child_claim.revision,
            child_claim.execution.token,
            (SucceededGraphNodeOutcome(GraphNodeId("child"), ContinueGraphRouting()),),
            CompleteGraphFrontier(),
        ),
    )
    projection = CompletedChild(activation, child_completed, "child-output", ContinueGraphRouting())
    admission = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (projection,)))
    assert isinstance(admission, ExecutableFrontier) and admission.admission is not None
    admitted = apply_command(parent, admission.admission.command)
    assert admitted.resources is not None
    assert tuple(item.node_id for item in admitted.resources.acquisitions) == (GraphNodeId("resource"),)

    claim = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, (projection,)))
    assert isinstance(claim, ExecutableFrontier) and claim.claim is not None
    assert claim.claim.command.node_ids == (GraphNodeId("nested"), GraphNodeId("resource"))


async def test_compiled_resource_requirement_drift_fails_before_claim() -> None:
    graph = resource_graph(NodeDefinition(GraphNodeId("a"), echo, (FILE,)))
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    assert admitted.resources is not None
    drifted = replace(
        admitted,
        resources=ResourceSnapshot(
            (ResourceLock(FILE), ResourceLock(DATABASE, GraphNodeId("a"))),
            (
                ResourceAcquisition(
                    GraphNodeId("a"),
                    (DATABASE,),
                    (DATABASE,),
                ),
            ),
        ),
    )
    with pytest.raises(ResourceTransitionError, match="requirements"):
        await executor.prepare(StepRequest(drifted, "input", ATTEMPT_ID, ()))


async def test_committed_resource_snapshot_rejects_wrong_order_and_stale_participant() -> None:
    graph = resource_graph(NodeDefinition(GraphNodeId("a"), echo, (FILE,)))
    executor = GraphExecutor(graph)
    initial = started(executor)
    wrong_order = replace(
        initial,
        resources=ResourceSnapshot((ResourceLock(DATABASE), ResourceLock(FILE))),
    )
    with pytest.raises(ResourceTransitionError, match="resource order"):
        await executor.prepare(StepRequest(wrong_order, "input", ATTEMPT_ID, ()))

    stale = replace(
        initial,
        resources=ResourceSnapshot(
            (ResourceLock(FILE, GraphNodeId("stale")), ResourceLock(DATABASE)),
            (
                ResourceAcquisition(
                    GraphNodeId("stale"),
                    (FILE,),
                    (FILE,),
                ),
            ),
        ),
    )
    with pytest.raises(GraphStateTransitionError, match="outside current pending"):
        await executor.prepare(StepRequest(stale, "input", ATTEMPT_ID, ()))


async def test_competing_claims_after_admission_have_one_durable_winner() -> None:
    graph = resource_graph(NodeDefinition(GraphNodeId("a"), echo, (FILE,)))
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    first = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    second = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(first, ExecutableFrontier) and first.claim is not None
    assert isinstance(second, ExecutableFrontier) and second.claim is not None
    assert first.claim.command.attempt_id != second.claim.command.attempt_id

    claimed = apply_command(admitted, first.claim.command)
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        apply_command(claimed, second.claim.command)
    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(second.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    assert not second.claim.consumed


async def test_claimed_resource_stage_revalidates_exact_participants() -> None:
    graph = resource_graph(
        NodeDefinition(GraphNodeId("a"), echo, (FILE,)),
        NodeDefinition(GraphNodeId("b"), echo),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.admission is not None
    admitted = apply_command(initial, prepared.admission.command)
    claim = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(claim, ExecutableFrontier) and claim.claim is not None
    claimed = apply_command(admitted, claim.claim.command)
    forged = replace(
        claimed,
        resources=ResourceSnapshot(
            (ResourceLock(FILE, GraphNodeId("b")), ResourceLock(DATABASE)),
            (ResourceAcquisition(GraphNodeId("b"), (FILE,), (FILE,)),),
        ),
    )

    with pytest.raises(ResultCollectionError, match="exactly cover"):
        await executor.execute(claim.claim, StepRequest(forged, "input", ATTEMPT_ID, ()))
    assert claim.claim.consumed


async def test_resource_frontier_waits_for_active_child_before_admission() -> None:
    graph = nested_resource_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)

    waiting = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (ActiveChild(activation, child),)))

    assert isinstance(waiting, WaitingForChildren)
    assert waiting.action.children == (ActiveChild(activation, child),)
    assert parent.resources is parent.execution is None


async def test_quiescent_abort_clears_unclaimed_admission() -> None:
    graph = resource_graph(NodeDefinition(GraphNodeId("a"), echo, (FILE,)))
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    assert admitted.resources is not None and admitted.execution is None

    aborted = apply_command(admitted, AbortGraphRun(admitted.revision, GraphAbortReason("stop")))

    assert aborted.status is GraphRunStatus.ABORTED
    assert aborted.resources is None


async def test_nonconflicting_resource_nodes_execute_in_one_concurrent_wave() -> None:
    barrier = asyncio.Barrier(2)
    calls: list[str] = []

    def node(name: str, resource: ResourceId) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return NodeSuccess(node_input)

        return NodeDefinition(GraphNodeId(name), execute, (resource,))

    graph = resource_graph(node("a", FILE), node("b", DATABASE))
    executor = GraphExecutor(graph)
    initial = started(executor)
    admission, admitted = await admitted_state(graph, executor, initial)
    assert admission.admission is not None
    assert admission.admission.admitted_node_ids == (GraphNodeId("a"), GraphNodeId("b"))
    assert admission.admission.waiting_node_ids == ()

    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)
    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))

    assert sorted(calls) == ["a", "b"]
    assert apply_command(claimed, result.command).status is GraphRunStatus.COMPLETED


async def test_partial_multi_resource_acquisition_executes_after_prefix_owner_releases() -> None:
    calls: list[str] = []

    def node(name: str, resources: tuple[ResourceId, ...]) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            return NodeSuccess(node_input)

        return NodeDefinition(GraphNodeId(name), execute, resources)

    graph = resource_graph(node("a", (DATABASE,)), node("b", (FILE, DATABASE)))
    executor = GraphExecutor(graph)
    initial = started(executor)
    admission, admitted = await admitted_state(graph, executor, initial)
    assert admission.admission is not None
    assert admission.admission.admitted_node_ids == (GraphNodeId("a"),)
    assert admission.admission.waiting_node_ids == (GraphNodeId("b"),)
    assert admitted.resources is not None
    b_acquisition = admitted.resources.acquisitions[1]
    assert b_acquisition.acquired == (FILE,)
    assert b_acquisition.waiting_for == DATABASE

    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)
    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))

    assert calls == ["a", "b"]
    assert apply_command(claimed, result.command).status is GraphRunStatus.COMPLETED


async def test_three_conflicting_resource_nodes_execute_once_in_fifo_order() -> None:
    calls: list[str] = []

    def node(name: str) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            return NodeSuccess(node_input)

        return NodeDefinition(GraphNodeId(name), execute, (FILE,))

    graph = resource_graph(node("c"), node("a"), node("b"))
    executor = GraphExecutor(graph)
    initial = started(executor)
    admission, admitted = await admitted_state(graph, executor, initial)
    assert admission.admission is not None
    assert admission.admission.admitted_node_ids == (GraphNodeId("a"),)
    assert admission.admission.waiting_node_ids == (GraphNodeId("b"), GraphNodeId("c"))

    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)
    await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))

    assert calls == ["a", "b", "c"]
    assert len(calls) == len(set(calls))


async def test_resource_free_node_executes_only_in_first_of_multiple_resource_waves() -> None:
    calls: list[str] = []

    def node(name: str, resources: tuple[ResourceId, ...] = ()) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            return NodeSuccess(node_input)

        return NodeDefinition(GraphNodeId(name), execute, resources)

    graph = resource_graph(node("a", (FILE,)), node("b", (FILE,)), node("free"))
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)

    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))

    assert calls.count("free") == 1
    assert calls == ["free", "a", "b"]
    assert len(result.results) == 3


async def test_typed_resource_failure_settles_after_releasing_scheduler_state() -> None:
    async def fail(node_input: str) -> NodeFailure:
        return NodeFailure(GraphFailure(f"failed:{node_input}"))

    graph = resource_graph(NodeDefinition(GraphNodeId("a"), fail, (FILE,)))
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)

    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    failed = apply_command(claimed, result.command)

    assert isinstance(result.results[0], TaskFailure)
    assert failed.status is GraphRunStatus.RUNNING
    assert failed.execution is failed.resources is None


async def test_resource_waves_collect_failure_and_interrupt_without_blocking_waiters() -> None:
    calls: list[str] = []

    async def fail(node_input: str) -> NodeFailure:
        calls.append("a")
        return NodeFailure(GraphFailure(f"failed:{node_input}"))

    async def interrupt(node_input: str) -> NodeInterrupt:
        calls.append("b")
        return NodeInterrupt(GraphInterruptPayload(f"question:{node_input}".encode()))

    codec = Utf8Codec()
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), fail, (FILE,)),
                NodeDefinition(GraphNodeId("b"), interrupt, (FILE,)),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(FILE, 10),),
            ResumeInputBinding(GraphResumeInputCodecId("utf8.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    admission, admitted = await admitted_state(graph, executor, initial)
    assert admission.admission is not None
    assert admission.admission.admitted_node_ids == (GraphNodeId("a"),)
    assert admission.admission.waiting_node_ids == (GraphNodeId("b"),)
    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)

    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    awaiting = apply_command(claimed, result.command)

    assert calls == ["a", "b"]
    assert isinstance(result.results[0], TaskFailure)
    assert isinstance(result.results[1], TaskInterrupt)
    assert awaiting.execution is awaiting.resources is None
    assert await executor.prepare(StepRequest(awaiting, "ignored", ATTEMPT_ID, ())) == AwaitingResume(
        (GraphNodeId("a"),),
        (GraphNodeId("b"),),
    )


async def test_later_resource_wave_exception_discards_all_earlier_typed_results() -> None:
    calls: list[str] = []

    async def succeed(node_input: str) -> NodeSuccess[str]:
        calls.append("a")
        return NodeSuccess(node_input)

    async def explode(node_input: str) -> NodeSuccess[str]:
        calls.append("b")
        raise RuntimeError(f"later:{node_input}")

    graph = resource_graph(
        NodeDefinition(GraphNodeId("a"), succeed, (FILE,)),
        NodeDefinition(GraphNodeId("b"), explode, (FILE,)),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, admitted = await admitted_state(graph, executor, initial)
    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)

    with pytest.raises(RuntimeError, match="later:input"):
        await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))

    assert calls == ["a", "b"]
    assert claimed.execution is not None and claimed.resources is not None
    fenced = apply_command(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    assert fenced.execution is fenced.resources is None
    assert fenced.frontier == initial.frontier


async def test_failure_override_survives_resource_admission_and_reaches_only_its_node() -> None:
    received: dict[str, list[str]] = {"a": [], "b": []}

    def fail_once(name: str):
        async def execute(node_input: str):
            received[name].append(node_input)
            if len(received[name]) == 1:
                return NodeFailure(GraphFailure(f"{name} failed"))
            return NodeSuccess(node_input)

        return execute

    codec = Utf8Codec()
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource-resume.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), fail_once("a"), (FILE,)),
                NodeDefinition(GraphNodeId("b"), fail_once("b"), (FILE,)),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(FILE, 10),),
            ResumeInputBinding(GraphResumeInputCodecId("utf8.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    _, first_admitted = await admitted_state(graph, executor, initial)
    first_prepared = await executor.prepare(StepRequest(first_admitted, "initial", ATTEMPT_ID, ()))
    assert isinstance(first_prepared, ExecutableFrontier) and first_prepared.claim is not None
    first_claimed = apply_command(first_admitted, first_prepared.claim.command)
    first_result = await executor.execute(
        first_prepared.claim,
        StepRequest(first_claimed, "initial", ATTEMPT_ID, ()),
    )
    failed = apply_command(first_claimed, first_result.command)
    resumed = apply_command(
        failed,
        executor.resume(
            ResumeRequest(
                failed,
                (
                    ResumeFailedNodeRequest(GraphNodeId("a"), OverrideNodeInput("override-a")),
                    ResumeFailedNodeRequest(GraphNodeId("b"), OverrideNodeInput("override-b")),
                ),
            )
        ),
    )

    _, readmitted = await admitted_state(graph, executor, resumed)
    retry = await executor.prepare(StepRequest(readmitted, "request-default", ATTEMPT_ID, ()))
    assert isinstance(retry, ExecutableFrontier) and retry.claim is not None
    retry_claimed = apply_command(readmitted, retry.claim.command)
    retry_result = await executor.execute(
        retry.claim,
        StepRequest(retry_claimed, "request-default", ATTEMPT_ID, ()),
    )

    assert received == {"a": ["initial", "override-a"], "b": ["initial", "override-b"]}
    assert apply_command(retry_claimed, retry_result.command).status is GraphRunStatus.COMPLETED


async def test_conditional_route_acquires_only_selected_target_resource() -> None:
    calls: list[str] = []

    async def choose(node_input: str) -> NodeSuccess[str]:
        calls.append("choose")
        return NodeSuccess(node_input, SelectGraphRoute(GraphRouteId("right")))

    async def target(node_input: str) -> NodeSuccess[str]:
        calls.append("right")
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("choose"), choose),
                NodeDefinition(GraphNodeId("left"), target, (FILE,)),
                NodeDefinition(GraphNodeId("right"), target, (FILE,)),
            ),
            (
                ConditionalEdge(GraphNodeId("choose"), GraphRouteId("left"), GraphNodeId("left")),
                ConditionalEdge(GraphNodeId("choose"), GraphRouteId("right"), GraphNodeId("right")),
                DirectEdge(GraphNodeId("left"), END),
                DirectEdge(GraphNodeId("right"), END),
            ),
            (GraphNodeId("choose"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    first = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(first, ExecutableFrontier) and first.claim is not None
    first_claimed = apply_command(initial, first.claim.command)
    first_result = await executor.execute(first.claim, StepRequest(first_claimed, "input", ATTEMPT_ID, ()))
    routed = apply_command(first_claimed, first_result.command)

    admission = await executor.prepare(StepRequest(routed, "input", ATTEMPT_ID, ()))
    assert isinstance(admission, ExecutableFrontier) and admission.admission is not None
    admitted = apply_command(routed, admission.admission.command)
    assert admitted.resources is not None
    assert tuple(item.node_id for item in admitted.resources.acquisitions) == (GraphNodeId("right"),)


async def test_resource_and_completed_nested_node_settle_one_frontier() -> None:
    graph = nested_resource_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)
    child_claim = reduce_graph_run(
        child,
        ClaimGraphExecution(child.revision, GraphExecutionAttemptId("child"), (GraphNodeId("child"),)),
    )
    assert child_claim.execution is not None
    child_completed = reduce_graph_run(
        child_claim,
        SettleGraphExecution(
            child_claim.revision,
            child_claim.execution.token,
            (SucceededGraphNodeOutcome(GraphNodeId("child"), ContinueGraphRouting()),),
            CompleteGraphFrontier(),
        ),
    )
    projection = CompletedChild(activation, child_completed, "child-output", ContinueGraphRouting())
    admission = await executor.prepare(StepRequest(parent, "input", ATTEMPT_ID, (projection,)))
    assert isinstance(admission, ExecutableFrontier) and admission.admission is not None
    admitted = apply_command(parent, admission.admission.command)
    prepared = await executor.prepare(StepRequest(admitted, "input", ATTEMPT_ID, (projection,)))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(admitted, prepared.claim.command)

    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, (projection,)))

    assert tuple(item.task.node_id for item in result.results) == (
        GraphNodeId("nested"),
        GraphNodeId("resource"),
    )
    assert apply_command(claimed, result.command).status is GraphRunStatus.COMPLETED
