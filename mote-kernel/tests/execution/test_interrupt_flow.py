import asyncio
from dataclasses import replace

import pytest

from mote_kernel.execution import ExecutedSuperstep, GraphExecutor, PreparedFrontier, StepRequest
from mote_kernel.execution.engine.claim_stage import require_claim_tasks
from mote_kernel.execution.engine.resolution_input import effective_node_input
from mote_kernel.execution.errors import ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.graph import (
    END,
    CompiledGraph,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    NodeDefinition,
    NodeFailure,
    NodeId,
    NodeSuccess,
    ResolutionBinding,
    ResolutionCodecId,
    compile_graph,
)
from mote_kernel.execution.snapshot import ExecutionAttemptId
from mote_kernel.state.graph_state import (
    FenceGraphExecution,
    GraphFailure,
    GraphInterruptId,
    GraphInterruptIdentity,
    GraphInterruptLifecycle,
    GraphInterruptPayload,
    GraphInterruptReceipt,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
)
from mote_kernel.state.graph_state import GraphDefinitionId as StateGraphDefinitionId
from mote_kernel.state.graph_state.command import RequestGraphRunInterrupt, ResolveGraphRunInterrupt
from mote_kernel.state.graph_state.reducer import GraphStateTransitionError, reduce_graph_run


class Utf8ResolutionDecoder:
    def decode(self, payload: bytes) -> str:
        return payload.decode("utf-8")


def graph_with_nodes(
    nodes: tuple[NodeDefinition[str, str], ...],
    edges: tuple[DirectEdge, ...] = (),
    *,
    codec_id: str | None = "input.v1",
    codec_version: int = 1,
) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("interrupt.graph"),
            version=GraphDefinitionVersion(1),
            nodes=nodes,
            edges=edges,
            entries=(NodeId("a"),),
            resolution=(
                ResolutionBinding(ResolutionCodecId(codec_id), codec_version, Utf8ResolutionDecoder())
                if codec_id is not None
                else None
            ),
        )
    )


def started(executor: GraphExecutor[str, str]) -> GraphRunState:
    command = executor.start_command(GraphRunId("root"))
    return reduce_graph_run(None, command)


def request(state: GraphRunState, attempt: str, node_input: str = "ordinary") -> StepRequest[str, str]:
    return StepRequest(state, node_input, ExecutionAttemptId(attempt))


def apply(current: GraphRunState, command: GraphRunCommand) -> GraphRunState:
    return reduce_graph_run(current, command)


async def prepare_claim(
    executor: GraphExecutor[str, str], state: GraphRunState, attempt: str, node_input: str = "ordinary"
):
    prepared = await executor.prepare(request(state, attempt, node_input))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.execution is not None
    return prepared.execution


@pytest.mark.asyncio
async def test_claim_is_owned_by_one_executor_and_consumed_at_most_once() -> None:
    calls = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return NodeSuccess(node_input)

    graph = graph_with_nodes((NodeDefinition(NodeId("a"), blocking),))
    executor = GraphExecutor(graph)
    initial = started(executor)
    run = initial
    worker_a = await prepare_claim(executor, run, "worker")
    worker_b = await prepare_claim(executor, run, "worker")
    assert not worker_a.consumed
    assert worker_a.command.attempt_id != worker_b.command.attempt_id
    with pytest.raises(ResultCollectionError, match="tasks do not match"):
        require_claim_tasks(worker_a, ())
    claimed = apply(initial, worker_a.command)

    with pytest.raises(ResultCollectionError, match="does not match committed"):
        await executor.execute(worker_b, request(claimed, "worker"))
    assert not worker_b.consumed
    assert calls == 0

    first = asyncio.create_task(executor.execute(worker_a, request(claimed, "worker")))
    await entered.wait()
    assert worker_a.consumed
    with pytest.raises(ResultCollectionError, match="already been consumed"):
        await executor.execute(worker_a, request(claimed, "worker"))
    assert calls == 1
    with pytest.raises(GraphStateTransitionError, match="drain"):
        apply(
            claimed,
            RequestGraphRunInterrupt(
                claimed.superstep,
                GraphInterruptIdentity(claimed.run_id, GraphInterruptId("pause"), 1),
                GraphInterruptPayload(b"request"),
            ),
        )
    release.set()
    assert isinstance(await first, ExecutedSuperstep)


@pytest.mark.asyncio
async def test_cancelled_parallel_claim_requires_explicit_fencing() -> None:
    started_a = asyncio.Event()
    started_b = asyncio.Event()
    never_complete = asyncio.Event()
    cancelled: list[str] = []

    def blocking(name: str, started_event: asyncio.Event) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            started_event.set()
            try:
                await never_complete.wait()
            except asyncio.CancelledError:
                cancelled.append(name)
                raise
            return NodeSuccess(node_input)

        return NodeDefinition(NodeId(name), execute)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.graph"),
            GraphDefinitionVersion(1),
            (blocking("a", started_a), blocking("b", started_b)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    claim = await prepare_claim(executor, initial, "worker")
    claimed = apply(initial, claim.command)

    running = asyncio.create_task(executor.execute(claim, request(claimed, "worker")))
    async with asyncio.timeout(2):
        await asyncio.gather(started_a.wait(), started_b.wait())
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert sorted(cancelled) == ["a", "b"]
    assert claim.consumed
    assert claimed.execution is not None
    with pytest.raises(SnapshotMismatchError, match="active execution lease"):
        await executor.prepare(request(claimed, "retry"))
    with pytest.raises(ResultCollectionError, match="already been consumed"):
        await executor.execute(claim, request(claimed, "worker"))

    fenced = apply(claimed, FenceGraphExecution(claimed.superstep, claimed.execution.token))
    retry = await prepare_claim(executor, fenced, "retry")
    retried = apply(fenced, retry.command)

    assert retried.execution is not None
    assert retried.execution.token.generation == claimed.execution.token.generation + 1


@pytest.mark.asyncio
async def test_parallel_node_exception_settles_sibling_but_keeps_claim_fenced() -> None:
    failure_returned = asyncio.Event()
    completed: list[str] = []

    async def finish(node_input: str) -> NodeSuccess[str]:
        await failure_returned.wait()
        completed.append("a")
        return NodeSuccess(node_input)

    async def explode(node_input: str) -> NodeSuccess[str]:
        completed.append("b")
        failure_returned.set()
        raise RuntimeError(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), finish), NodeDefinition(NodeId("b"), explode)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    claim = await prepare_claim(executor, initial, "worker")
    claimed = apply(initial, claim.command)

    with pytest.raises(RuntimeError, match="ordinary"):
        await executor.execute(claim, request(claimed, "worker"))

    assert completed == ["b", "a"]
    assert claim.consumed
    assert claimed.execution is not None
    fenced = apply(claimed, FenceGraphExecution(claimed.superstep, claimed.execution.token))
    assert fenced.execution is None


@pytest.mark.asyncio
async def test_infrastructure_exception_prevents_partial_typed_failure_settlement() -> None:
    completed: list[str] = []

    async def fail(node_input: str) -> NodeFailure:
        del node_input
        completed.append("a")
        return NodeFailure("typed failure")

    async def explode(node_input: str) -> NodeSuccess[str]:
        completed.append("b")
        raise RuntimeError(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), fail), NodeDefinition(NodeId("b"), explode)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    claim = await prepare_claim(executor, initial, "worker")
    claimed = apply(initial, claim.command)

    with pytest.raises(RuntimeError, match="ordinary"):
        await executor.execute(claim, request(claimed, "worker"))

    assert sorted(completed) == ["a", "b"]
    assert claim.consumed
    assert claimed.execution is not None
    assert claimed.status is GraphRunStatus.RUNNING


@pytest.mark.asyncio
async def test_execution_claim_cannot_move_to_an_equivalent_but_different_executor() -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = graph_with_nodes((NodeDefinition(NodeId("a"), identity),))
    owner = GraphExecutor(graph)
    other = GraphExecutor(graph)
    initial = started(owner)
    claim = await prepare_claim(owner, initial, "worker")
    claimed = apply(initial, claim.command)

    with pytest.raises(ResultCollectionError, match="another graph executor"):
        await other.execute(claim, request(claimed, "worker"))
    assert not claim.consumed


@pytest.mark.asyncio
async def test_execution_claim_cannot_move_to_a_different_worker_attempt() -> None:
    calls = 0

    async def identity(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), identity),)))
    initial = started(executor)
    claim = await prepare_claim(executor, initial, "worker-a")
    claimed = apply(initial, claim.command)

    with pytest.raises(ResultCollectionError, match="does not match committed"):
        await executor.execute(claim, request(claimed, "worker-b"))

    assert not claim.consumed
    assert calls == 0
    result = await executor.execute(claim, request(claimed, "worker-a"))
    assert isinstance(result, ExecutedSuperstep)
    assert calls == 1


@pytest.mark.asyncio
async def test_fenced_unstarted_claim_cannot_execute_against_current_state() -> None:
    calls = 0

    async def identity(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), identity),)))
    initial = started(executor)
    prepared_claim = await prepare_claim(executor, initial, "worker")
    claimed = apply(initial, prepared_claim.command)
    assert claimed.execution is not None
    fenced = apply(claimed, FenceGraphExecution(claimed.superstep, claimed.execution.token))

    with pytest.raises(ResultCollectionError, match="does not match committed"):
        await executor.execute(prepared_claim, request(fenced, "worker"))

    assert not prepared_claim.consumed
    assert calls == 0
    retry_claim = await prepare_claim(executor, fenced, "worker")
    retry_state = apply(fenced, retry_claim.command)
    with pytest.raises(ResultCollectionError, match="does not match committed"):
        await executor.execute(prepared_claim, request(retry_state, "worker"))
    result = await executor.execute(retry_claim, request(retry_state, "worker"))

    assert isinstance(result, ExecutedSuperstep)
    assert calls == 1


@pytest.mark.asyncio
async def test_late_settlement_cannot_overwrite_a_reclaimed_execution() -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), identity),)))
    initial = started(executor)
    first_claim = await prepare_claim(executor, initial, "worker-one")
    first_state = apply(initial, first_claim.command)
    first_result = await executor.execute(first_claim, request(first_state, "worker-one"))
    assert first_state.execution is not None
    fenced = apply(first_state, FenceGraphExecution(first_state.superstep, first_state.execution.token))
    retry_claim = await prepare_claim(executor, fenced, "worker-two")
    retry_state = apply(fenced, retry_claim.command)
    retry_result = await executor.execute(retry_claim, request(retry_state, "worker-two"))

    with pytest.raises(GraphStateTransitionError, match="own the active execution lease"):
        apply(retry_state, first_result.command)

    completed = apply(retry_state, retry_result.command)
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.execution is None


@pytest.mark.asyncio
async def test_node_initiated_cancellation_waits_for_sibling_and_keeps_claim_fenced() -> None:
    cancellation_raised = asyncio.Event()
    completed: list[str] = []

    async def finish(node_input: str) -> NodeSuccess[str]:
        await cancellation_raised.wait()
        completed.append("a")
        return NodeSuccess(node_input)

    async def cancel(node_input: str) -> NodeSuccess[str]:
        del node_input
        completed.append("b")
        cancellation_raised.set()
        raise asyncio.CancelledError

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), finish), NodeDefinition(NodeId("b"), cancel)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    claim = await prepare_claim(executor, initial, "worker")
    claimed = apply(initial, claim.command)

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(claim, request(claimed, "worker"))

    assert completed == ["b", "a"]
    assert claim.consumed
    assert claimed.execution is not None


@pytest.mark.asyncio
async def test_claim_validation_uses_one_canonical_order_for_different_length_node_ids() -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("aa"), identity),
                NodeDefinition(NodeId("z"), identity),
            ),
            (
                DirectEdge(NodeId("aa"), END),
                DirectEdge(NodeId("z"), END),
            ),
            (NodeId("aa"), NodeId("z")),
        )
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    claim = await prepare_claim(executor, initial, "worker")
    claimed = apply(initial, claim.command)

    result = await executor.execute(claim, request(claimed, "worker"))

    assert tuple(item.task.node_id for item in result.results) == (NodeId("aa"), NodeId("z"))


@pytest.mark.asyncio
async def test_executor_rejects_a_run_owned_by_another_graph_definition() -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), identity),)))
    foreign = replace(started(executor), definition_id=StateGraphDefinitionId("other.graph"))

    with pytest.raises(SnapshotMismatchError, match="not owned by this graph executor"):
        await executor.prepare(request(foreign, "worker"))


@pytest.mark.asyncio
async def test_suspended_run_prepares_as_idle_without_an_execution_claim() -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), identity),)))
    initial = started(executor)
    suspended = apply(
        initial,
        RequestGraphRunInterrupt(
            initial.superstep,
            GraphInterruptIdentity(initial.run_id, GraphInterruptId("pause"), 1),
            GraphInterruptPayload(b"request"),
        ),
    )

    prepared = await executor.prepare(request(suspended, "worker"))

    assert prepared.admission is None
    assert prepared.nested_runs == ()
    assert prepared.execution is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [GraphRunStatus.COMPLETED, GraphRunStatus.FAILED])
async def test_terminal_run_prepares_as_idle_without_an_execution_claim(status: GraphRunStatus) -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), identity),)))
    initial = started(executor)
    terminal = replace(
        initial,
        status=status,
        frontier=(),
        failure=GraphFailure("failed") if status is GraphRunStatus.FAILED else None,
    )

    prepared = await executor.prepare(request(terminal, "worker"))

    assert prepared.admission is None
    assert prepared.nested_runs == ()
    assert prepared.execution is None


def resolved(executor: GraphExecutor[str, str]) -> GraphRunState:
    initial = started(executor)
    identity = GraphInterruptIdentity(initial.run_id, GraphInterruptId("pause"), 1)
    requested = apply(
        initial,
        RequestGraphRunInterrupt(initial.superstep, identity, GraphInterruptPayload(b"request")),
    )
    return apply(
        requested,
        ResolveGraphRunInterrupt(
            requested.superstep,
            identity,
            GraphInterruptPayload(b"approved"),
        ),
    )


@pytest.mark.asyncio
async def test_executor_owned_decoder_is_the_only_resolution_input() -> None:
    received: list[str] = []

    async def record(node_input: str) -> NodeSuccess[str]:
        received.append(node_input)
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), record),)))
    current = resolved(executor)
    claim = await prepare_claim(executor, current, "worker", "DENIED")
    claimed = apply(current, claim.command)

    result = await executor.execute(claim, request(claimed, "worker", "DENIED"))

    assert isinstance(result, ExecutedSuperstep)
    assert received == ["approved"]


@pytest.mark.asyncio
async def test_consumed_resolution_is_never_delivered_to_a_node_again() -> None:
    received: list[str] = []

    async def record(node_input: str) -> NodeSuccess[str]:
        received.append(node_input)
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), record),)))
    current = resolved(executor)
    interrupt = current.interrupt
    assert interrupt is not None
    consumed_run = replace(
        current,
        superstep=1,
        interrupt=replace(
            interrupt,
            lifecycle=GraphInterruptLifecycle.CONSUMED,
            receipt=GraphInterruptReceipt(0),
        ),
    )
    current = consumed_run
    claim = await prepare_claim(executor, consumed_run, "worker")
    claimed = apply(current, claim.command)

    await executor.execute(claim, request(claimed, "worker"))

    assert received == ["ordinary"]


def test_invalid_interrupt_lifecycle_cannot_supply_node_input() -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = graph_with_nodes((NodeDefinition(NodeId("a"), identity),))
    current = resolved(GraphExecutor(graph))
    interrupt = current.interrupt
    assert interrupt is not None
    requested = replace(
        current,
        interrupt=replace(
            interrupt,
            lifecycle=GraphInterruptLifecycle.REQUESTED,
            resolution_payload=None,
        ),
    )

    with pytest.raises(SnapshotMismatchError, match="only a resolved interrupt"):
        effective_node_input(graph, requested, "ordinary")


@pytest.mark.parametrize("missing", ["decoder", "payload"])
def test_resolved_interrupt_requires_decoder_and_payload(missing: str) -> None:
    async def identity(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = graph_with_nodes((NodeDefinition(NodeId("a"), identity),))
    current = resolved(GraphExecutor(graph))
    interrupt = current.interrupt
    assert interrupt is not None
    if missing == "decoder":
        invalid_graph = graph_with_nodes((NodeDefinition(NodeId("a"), identity),), codec_id=None)
        invalid_state = replace(current, resolution_codec=None)
    else:
        invalid_graph = graph
        invalid_state = replace(current, interrupt=replace(interrupt, resolution_payload=None))

    with pytest.raises(SnapshotMismatchError, match=missing):
        effective_node_input(invalid_graph, invalid_state, "ordinary")


@pytest.mark.asyncio
async def test_two_interrupt_generations_deliver_only_their_own_resolution() -> None:
    received: list[str] = []

    async def record(node_input: str) -> NodeSuccess[str]:
        received.append(node_input)
        return NodeSuccess(node_input)

    graph = graph_with_nodes(
        (NodeDefinition(NodeId("a"), record), NodeDefinition(NodeId("b"), record)),
        (DirectEdge(NodeId("a"), NodeId("b")), DirectEdge(NodeId("b"), END)),
    )
    executor = GraphExecutor(graph)
    current = started(executor)

    first_identity = GraphInterruptIdentity(current.run_id, GraphInterruptId("pause"), 1)
    current = apply(
        current,
        RequestGraphRunInterrupt(current.superstep, first_identity, GraphInterruptPayload(b"request-one")),
    )
    current = apply(
        current,
        ResolveGraphRunInterrupt(current.superstep, first_identity, GraphInterruptPayload(b"answer-one")),
    )
    first_claim = await prepare_claim(executor, current, "worker-one")
    first_claimed = apply(current, first_claim.command)
    first_result = await executor.execute(first_claim, request(first_claimed, "worker-one", "ordinary"))
    current = apply(first_claimed, first_result.command)

    assert current.superstep == 1
    assert current.interrupt is not None
    assert current.interrupt.lifecycle is GraphInterruptLifecycle.CONSUMED
    assert current.interrupt.receipt == GraphInterruptReceipt(0)

    second_identity = GraphInterruptIdentity(current.run_id, GraphInterruptId("pause"), 2)
    current = apply(
        current,
        RequestGraphRunInterrupt(current.superstep, second_identity, GraphInterruptPayload(b"request-two")),
    )
    current = apply(
        current,
        ResolveGraphRunInterrupt(current.superstep, second_identity, GraphInterruptPayload(b"answer-two")),
    )
    second_claim = await prepare_claim(executor, current, "worker-two")
    second_claimed = apply(current, second_claim.command)
    second_result = await executor.execute(second_claim, request(second_claimed, "worker-two", "ordinary"))
    completed = apply(second_claimed, second_result.command)

    assert received == ["answer-one", "answer-two"]
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.interrupt is not None
    assert completed.interrupt.identity == second_identity
    assert completed.interrupt.lifecycle is GraphInterruptLifecycle.CONSUMED
    assert completed.interrupt.receipt == GraphInterruptReceipt(1)


@pytest.mark.asyncio
async def test_resolution_decode_failure_retains_claim_until_explicit_fencing() -> None:
    calls = 0

    class RejectingDecoder:
        def decode(self, payload: bytes) -> str:
            raise ValueError(payload.decode("utf-8"))

    async def record(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), record),),
            (DirectEdge(NodeId("a"), END),),
            (NodeId("a"),),
            resolution=ResolutionBinding(ResolutionCodecId("input.v1"), 1, RejectingDecoder()),
        )
    )
    executor = GraphExecutor(graph)
    current = resolved(executor)
    claim = await prepare_claim(executor, current, "worker")
    claimed = apply(current, claim.command)

    with pytest.raises(ValueError, match="approved"):
        await executor.execute(claim, request(claimed, "worker"))

    assert claim.consumed
    assert calls == 0
    assert claimed.execution is not None
    fenced = apply(
        claimed,
        FenceGraphExecution(claimed.superstep, claimed.execution.token),
    )
    assert fenced.execution is None


@pytest.mark.asyncio
async def test_resolution_is_redelivered_after_exception_until_progress_commits() -> None:
    received: list[str] = []

    async def fail_once(node_input: str) -> NodeSuccess[str]:
        received.append(node_input)
        if len(received) == 1:
            raise RuntimeError("worker stopped before settlement")
        return NodeSuccess(node_input)

    executor = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), fail_once),)))
    current = resolved(executor)
    first_claim = await prepare_claim(executor, current, "worker-one")
    first_claimed = apply(current, first_claim.command)

    with pytest.raises(RuntimeError, match="before settlement"):
        await executor.execute(first_claim, request(first_claimed, "worker-one"))

    assert first_claimed.interrupt is not None
    assert first_claimed.interrupt.lifecycle is GraphInterruptLifecycle.RESOLVED
    assert first_claimed.interrupt.receipt is None
    assert first_claimed.execution is not None
    fenced = apply(
        first_claimed,
        FenceGraphExecution(first_claimed.superstep, first_claimed.execution.token),
    )
    retry_claim = await prepare_claim(executor, fenced, "worker-two")
    retry_claimed = apply(fenced, retry_claim.command)
    result = await executor.execute(retry_claim, request(retry_claimed, "worker-two"))
    completed = apply(retry_claimed, result.command)

    assert received == ["approved", "approved"]
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.interrupt is not None
    assert completed.interrupt.lifecycle is GraphInterruptLifecycle.CONSUMED
    assert completed.interrupt.receipt == GraphInterruptReceipt(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("codec_id", "codec_version"),
    [(None, 1), ("input.v2", 1), ("input.v1", 2)],
)
async def test_resolved_interrupt_requires_executor_owned_resolution_binding(
    codec_id: str | None,
    codec_version: int,
) -> None:
    calls = 0

    async def record(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    bound = GraphExecutor(graph_with_nodes((NodeDefinition(NodeId("a"), record),)))
    current = resolved(bound)
    unbound = GraphExecutor(
        graph_with_nodes(
            (NodeDefinition(NodeId("a"), record),),
            codec_id=codec_id,
            codec_version=codec_version,
        )
    )

    with pytest.raises(SnapshotMismatchError, match="resolution codec"):
        await unbound.prepare(request(current, "worker"))
    assert calls == 0
