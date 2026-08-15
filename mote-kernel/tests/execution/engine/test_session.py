import asyncio
from dataclasses import replace
from typing import cast

import pytest

from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.engine.task import ExecutableTask
from mote_kernel.execution.errors import InvalidRoutingCommandError, ResultCollectionError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph import (
    END,
    CompiledGraph,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    NestedGraphNodeDefinition,
    Node,
    NodeDefinition,
    NodeFailure,
    NodeInterrupt,
    NodeSuccess,
    ResumeInputBinding,
    compile_graph,
)
from mote_kernel.execution.identity import ExecutionRequestAttemptId
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.execution.result import ExecutableFrontier
from mote_kernel.state.graph_state import (
    FailedGraphNode,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphInterruptPayload,
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    InterruptedGraphNode,
    ResourceId,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


class _TextCodec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


def graph(
    nodes: tuple[NodeDefinition[str, str] | NestedGraphNodeDefinition[str, str], ...],
    *,
    resources: tuple[ResourceDefinition, ...] = (),
    entries: tuple[str, ...] = ("a", "b"),
) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("session.graph"),
            GraphDefinitionVersion(1),
            tuple(nodes),
            (),
            tuple(GraphNodeId(node_id) for node_id in entries),
            tuple(resources),
        )
    )


def interrupt_graph(node: Node[str, str], *, with_sibling: bool = False) -> CompiledGraph[str, str]:
    codec = _TextCodec()
    nodes = (NodeDefinition(GraphNodeId("a"), node),)
    entries = (GraphNodeId("a"),)
    if with_sibling:

        async def sibling(value: str) -> NodeSuccess[str]:
            return NodeSuccess(value)

        nodes = (*nodes, NodeDefinition(GraphNodeId("b"), sibling))
        entries = (*entries, GraphNodeId("b"))
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("session.interrupt"),
            GraphDefinitionVersion(1),
            nodes,
            tuple(DirectEdge(node_id, END) for node_id in entries),
            entries,
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("session.v1"), 1, codec, codec),
        )
    )


async def claim_session(
    graph: CompiledGraph[str, str],
    state: GraphRunState,
    *,
    limits: ExecutionLimits | None = None,
) -> tuple[GraphExecutor[str, str], GraphRunState, GraphExecutionSession[str, str]]:
    effective_limits = ExecutionLimits() if limits is None else limits
    executor = GraphExecutor(graph)
    request = StepRequest(state, "input", ExecutionRequestAttemptId("request"), (), effective_limits)
    prepared = await executor.prepare(request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), (), effective_limits),
    )
    return executor, claimed, session


async def test_completion_is_yielded_before_the_frontier_finishes() -> None:
    a_done = asyncio.Event()
    release_b = asyncio.Event()

    async def a(value: str) -> NodeSuccess[str]:
        a_done.set()
        return NodeSuccess("a")

    async def b(value: str) -> NodeSuccess[str]:
        await release_b.wait()
        return NodeSuccess("b")

    compiled = graph((NodeDefinition(GraphNodeId("a"), a), NodeDefinition(GraphNodeId("b"), b)))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await asyncio.wait_for(session.next(claimed), timeout=1)
        assert first.result.task.node_id == GraphNodeId("a")
        after_a = reduce_graph_run(claimed, first.command)
        assert after_a.execution is not None
        assert after_a.frontier.nodes[1].settlement.__class__.__name__ == "PendingGraphNode"
        release_b.set()
        second = await asyncio.wait_for(session.next(after_a), timeout=1)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await session.aclose()


async def test_resource_release_makes_waiter_selectable_on_next_acknowledged_state() -> None:
    resource = ResourceId("file")

    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph(
        (
            NodeDefinition(GraphNodeId("a"), node, (resource,)),
            NodeDefinition(GraphNodeId("b"), node, (resource,)),
        ),
        resources=(ResourceDefinition(resource, 0),),
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        assert first.result.task.node_id == GraphNodeId("a")
        after_a = reduce_graph_run(claimed, first.command)
        assert after_a.resources is not None and after_a.resources.acquisitions[0].admitted
        second = await session.next(after_a)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await session.aclose()


async def test_resource_waiter_starts_only_after_the_settlement_successor_is_acknowledged() -> None:
    resource = ResourceId("file")
    waiter_started = asyncio.Event()

    async def owner(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    async def waiter(value: str) -> NodeSuccess[str]:
        waiter_started.set()
        return NodeSuccess(value)

    compiled = graph(
        (
            NodeDefinition(GraphNodeId("a"), owner, (resource,)),
            NodeDefinition(GraphNodeId("b"), waiter, (resource,)),
        ),
        resources=(ResourceDefinition(resource, 0),),
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        assert first.result.task.node_id == GraphNodeId("a")
        await asyncio.sleep(0)
        assert not waiter_started.is_set()

        after_owner = reduce_graph_run(claimed, first.command)
        second = await session.next(after_owner)
        assert waiter_started.is_set()
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await session.aclose()


async def test_queued_typed_sibling_does_not_delay_a_newly_admitted_waiter() -> None:
    resource = ResourceId("file")
    release_initial = asyncio.Event()
    release_waiter = asyncio.Event()
    owner_started = asyncio.Event()
    sibling_started = asyncio.Event()
    waiter_started = asyncio.Event()

    async def owner(value: str) -> NodeSuccess[str]:
        owner_started.set()
        await release_initial.wait()
        return NodeSuccess("a")

    async def waiter(value: str) -> NodeSuccess[str]:
        waiter_started.set()
        await release_waiter.wait()
        return NodeSuccess("b")

    async def sibling(value: str) -> NodeSuccess[str]:
        sibling_started.set()
        await release_initial.wait()
        return NodeSuccess("x")

    compiled = graph(
        (
            NodeDefinition(GraphNodeId("a"), owner, (resource,)),
            NodeDefinition(GraphNodeId("b"), waiter, (resource,)),
            NodeDefinition(GraphNodeId("x"), sibling),
        ),
        resources=(ResourceDefinition(resource, 0),),
        entries=("a", "b", "x"),
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(
        compiled,
        state,
        limits=ExecutionLimits(max_parallel_tasks=2),
    )
    try:
        first_call = asyncio.create_task(session.next(claimed))
        await asyncio.wait_for(
            asyncio.gather(owner_started.wait(), sibling_started.wait()),
            timeout=1,
        )
        assert not waiter_started.is_set()

        release_initial.set()
        first = await asyncio.wait_for(first_call, timeout=1)
        assert first.result.task.node_id == GraphNodeId("a")
        assert not waiter_started.is_set()

        after_owner = reduce_graph_run(claimed, first.command)
        second = await asyncio.wait_for(session.next(after_owner), timeout=1)
        assert second.result.task.node_id == GraphNodeId("x")
        assert waiter_started.is_set()

        after_sibling = reduce_graph_run(after_owner, second.command)
        release_waiter.set()
        third = await asyncio.wait_for(session.next(after_sibling), timeout=1)
        assert third.result.task.node_id == GraphNodeId("b")
    finally:
        await session.aclose()


async def test_queued_ordinary_error_prevents_newly_admitted_waiter_from_starting() -> None:
    resource = ResourceId("file")
    release_initial = asyncio.Event()
    owner_started = asyncio.Event()
    sibling_started = asyncio.Event()
    waiter_started = asyncio.Event()

    async def owner(value: str) -> NodeSuccess[str]:
        owner_started.set()
        await release_initial.wait()
        return NodeSuccess("a")

    async def waiter(value: str) -> NodeSuccess[str]:
        waiter_started.set()
        return NodeSuccess("b")

    async def failing_sibling(value: str) -> NodeSuccess[str]:
        sibling_started.set()
        await release_initial.wait()
        raise RuntimeError("sibling failed")

    compiled = graph(
        (
            NodeDefinition(GraphNodeId("a"), owner, (resource,)),
            NodeDefinition(GraphNodeId("b"), waiter, (resource,)),
            NodeDefinition(GraphNodeId("x"), failing_sibling),
        ),
        resources=(ResourceDefinition(resource, 0),),
        entries=("a", "b", "x"),
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(
        compiled,
        state,
        limits=ExecutionLimits(max_parallel_tasks=2),
    )
    try:
        first_call = asyncio.create_task(session.next(claimed))
        await asyncio.wait_for(
            asyncio.gather(owner_started.wait(), sibling_started.wait()),
            timeout=1,
        )
        release_initial.set()

        first = await asyncio.wait_for(first_call, timeout=1)
        assert first.result.task.node_id == GraphNodeId("a")
        after_owner = reduce_graph_run(claimed, first.command)

        with pytest.raises(RuntimeError, match="sibling failed"):
            await session.next(after_owner)
        assert not waiter_started.is_set()
    finally:
        await session.aclose()


async def test_max_parallel_limit_applies_to_dynamic_selection() -> None:
    started: list[str] = []
    gate = asyncio.Event()

    async def node(value: str) -> NodeSuccess[str]:
        started.append(value)
        await gate.wait()
        return NodeSuccess(value)

    compiled = graph(
        (
            NodeDefinition(GraphNodeId("a"), node),
            NodeDefinition(GraphNodeId("b"), node),
            NodeDefinition(GraphNodeId("c"), node),
        ),
        entries=("a", "b", "c"),
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state, limits=ExecutionLimits(max_parallel_tasks=2))
    try:
        first_wait = asyncio.create_task(session.next(claimed))
        for _ in range(10):
            await asyncio.sleep(0)
            if len(started) == 2:
                break
        assert len(started) == 2
        gate.set()
        first = await first_wait
        after = reduce_graph_run(claimed, first.command)
        second = await session.next(after)
        after = reduce_graph_run(after, second.command)
        third = await session.next(after)
        assert third.result.task.node_id == GraphNodeId("c")
    finally:
        await session.aclose()


async def test_concurrent_next_is_rejected_before_a_second_command_can_be_produced() -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    invocations = 0

    async def node(value: str) -> NodeSuccess[str]:
        nonlocal invocations
        invocations += 1
        started.set()
        await release.wait()
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first_call = asyncio.create_task(session.next(claimed))
        await asyncio.wait_for(started.wait(), timeout=1)

        with pytest.raises(ResultCollectionError, match="in-progress next"):
            await session.next(claimed)

        release.set()
        completed = await asyncio.wait_for(first_call, timeout=1)
        assert invocations == 1
        settled = reduce_graph_run(claimed, completed.command)
        with pytest.raises(StopAsyncIteration):
            await session.next(settled)
    finally:
        await session.aclose()


async def test_ordinary_error_drains_started_typed_siblings_and_stops_new_activation() -> None:
    gate = asyncio.Event()
    started: list[str] = []

    def good(name: str):
        async def execute(value: str) -> NodeSuccess[str]:
            started.append(name)
            await gate.wait()
            return NodeSuccess(value)

        return execute

    async def bad(value: str) -> NodeSuccess[str]:
        started.append("b")
        raise RuntimeError("bad")

    compiled = graph(
        (
            NodeDefinition(GraphNodeId("a"), good("a")),
            NodeDefinition(GraphNodeId("b"), bad),
            NodeDefinition(GraphNodeId("c"), good("c")),
        ),
        entries=("a", "b", "c"),
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state, limits=ExecutionLimits(max_parallel_tasks=2))
    try:
        await asyncio.sleep(0)
        gate.set()
        current = claimed
        yielded: list[GraphNodeId] = []
        while True:
            try:
                result = await session.next(current)
            except RuntimeError as error:
                assert str(error) == "bad"
                break
            yielded.append(result.result.task.node_id)
            current = reduce_graph_run(current, result.command)
        assert "c" not in started
        assert GraphNodeId("a") in yielded
    finally:
        await session.aclose()


async def test_close_is_idempotent_and_cancels_live_tasks() -> None:
    cancelled = asyncio.Event()
    started = asyncio.Event()
    cancellations = 0

    async def node(value: str) -> NodeSuccess[str]:
        nonlocal cancellations
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancellations += 1
            cancelled.set()
            raise
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    waiter = asyncio.create_task(session.next(claimed))
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.gather(session.aclose(), session.aclose())
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cancelled.is_set()
    assert cancellations == 1
    assert session.quiescent
    with pytest.raises(ResultCollectionError):
        await session.next(claimed)


async def test_async_context_manager_reaches_quiescence_and_closed_next_fails_closed() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    executor, claimed, session = await claim_session(compiled, state)
    del executor
    async with session:
        completed = await session.next(claimed)
        settled = reduce_graph_run(claimed, completed.command)
        with pytest.raises(StopAsyncIteration):
            await session.next(settled)
        assert session.quiescent
    assert session.quiescent
    with pytest.raises(ResultCollectionError, match="closed"):
        await session.next(settled)


async def test_next_cancellation_performs_cancellation_safe_close() -> None:
    started = asyncio.Event()

    async def node(value: str) -> NodeSuccess[str]:
        started.set()
        await asyncio.sleep(10)
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    waiter = asyncio.create_task(session.next(claimed))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert session.quiescent
    with pytest.raises(ResultCollectionError, match="closed"):
        await session.next(claimed)


async def test_repeated_next_cancellation_waits_for_cleanup_to_finish() -> None:
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def node(value: str) -> NodeSuccess[str]:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()
            raise
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    waiter = asyncio.create_task(session.next(claimed))
    cancelled = False
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        waiter.cancel()
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)

        waiter.cancel()
        await asyncio.sleep(0)
        assert not waiter.done()
        assert not cleanup_finished.is_set()
        assert not session.quiescent
    finally:
        release_cleanup.set()
        try:
            await asyncio.wait_for(waiter, timeout=1)
        except asyncio.CancelledError:
            cancelled = True
        await session.aclose()

    assert cancelled
    assert cleanup_finished.is_set()
    assert session.quiescent
    with pytest.raises(ResultCollectionError, match="closed"):
        await session.next(claimed)


async def test_failure_completion_is_settled_as_a_node_failure() -> None:
    async def fail(value: str) -> NodeFailure:
        return NodeFailure(GraphFailure("failed"))

    compiled = graph((NodeDefinition(GraphNodeId("a"), fail),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        completed = await session.next(claimed)
        settled = reduce_graph_run(claimed, completed.command)
    finally:
        await session.aclose()
    assert isinstance(settled.frontier.nodes[0].settlement, FailedGraphNode)


async def test_interrupt_completion_is_settled_with_a_state_identity() -> None:
    async def interrupt(value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(b"question"))

    compiled = interrupt_graph(interrupt)
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        completed = await session.next(claimed)
        settled = reduce_graph_run(claimed, completed.command)
    finally:
        await session.aclose()
    settlement = settled.frontier.nodes[0].settlement
    assert isinstance(settlement, InterruptedGraphNode)
    assert settlement.interrupt.identity.node_id == GraphNodeId("a")


async def test_session_acknowledges_failure_and_interrupt_variants_before_next_completion() -> None:
    async def fail(value: str) -> NodeFailure:
        return NodeFailure(GraphFailure("failed"))

    failure_graph = graph((NodeDefinition(GraphNodeId("a"), fail), NodeDefinition(GraphNodeId("b"), fail)))
    failure_state = reduce_graph_run(None, GraphExecutor(failure_graph).start_command(GraphRunId("failure-run")))
    _executor, failure_claimed, failure_session = await claim_session(failure_graph, failure_state)
    try:
        first = await failure_session.next(failure_claimed)
        after = reduce_graph_run(failure_claimed, first.command)
        second = await failure_session.next(after)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await failure_session.aclose()

    async def interrupt(value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(b"question"))

    interrupt_compiled = interrupt_graph(interrupt, with_sibling=True)
    interrupt_state = reduce_graph_run(
        None,
        GraphExecutor(interrupt_compiled).start_command(GraphRunId("interrupt-run")),
    )
    _executor, interrupt_claimed, interrupt_session = await claim_session(
        interrupt_compiled,
        interrupt_state,
    )
    try:
        first = await interrupt_session.next(interrupt_claimed)
        after = reduce_graph_run(interrupt_claimed, first.command)
        second = await interrupt_session.next(after)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await interrupt_session.aclose()


async def test_session_initial_state_guards_fail_closed() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        with pytest.raises(ResultCollectionError, match="claim successor"):
            await session.next(replace(claimed, revision=claimed.revision + 1))

        completed = await session.next(claimed)
        assert completed.result.task.node_id == GraphNodeId("a")
    finally:
        await session.aclose()


async def test_public_session_contract_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError, match="Protocols cannot be instantiated"):
        cast(type[object], GraphExecutionSession)()


async def test_session_acknowledgement_rejects_a_target_that_remains_pending() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node), NodeDefinition(GraphNodeId("b"), node)))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        _ = await session.next(claimed)
        invalid = replace(claimed, revision=claimed.revision + 1)
        with pytest.raises(ResultCollectionError, match="did not settle"):
            await session.next(invalid)
    finally:
        await session.aclose()


async def test_session_acknowledgement_rejects_an_unrelated_settlement_change() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node), NodeDefinition(GraphNodeId("b"), node)))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        unrelated = replace(
            after,
            frontier=replace(
                after.frontier,
                nodes=(
                    after.frontier.nodes[0],
                    replace(after.frontier.nodes[1], settlement=FailedGraphNode(GraphFailure("changed"))),
                ),
            ),
            execution=None,
        )
        with pytest.raises(ResultCollectionError, match="unrelated node"):
            await session.next(unrelated)
    finally:
        await session.aclose()


async def test_session_acknowledgement_rejects_a_mismatched_settlement_variant() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node), NodeDefinition(GraphNodeId("b"), node)))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        invalid = replace(
            after,
            frontier=replace(
                after.frontier,
                nodes=(
                    replace(after.frontier.nodes[0], settlement=FailedGraphNode(GraphFailure("wrong"))),
                    after.frontier.nodes[1],
                ),
            ),
        )
        with pytest.raises(ResultCollectionError, match="does not match"):
            await session.next(invalid)
    finally:
        await session.aclose()


async def test_session_acknowledgement_rejects_a_missing_partial_execution_token() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node), NodeDefinition(GraphNodeId("b"), node)))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        invalid = replace(after, execution=None)
        with pytest.raises(ResultCollectionError, match="retain its execution"):
            await session.next(invalid)
    finally:
        await session.aclose()


async def test_session_acknowledgement_rejects_a_changed_execution_token() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node), NodeDefinition(GraphNodeId("b"), node)))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        assert after.execution is not None
        changed = replace(
            after,
            execution=GraphExecutionLease(
                GraphExecutionToken(after.execution.token.generation, GraphExecutionAttemptId("other"))
            ),
        )
        with pytest.raises(ResultCollectionError, match="active execution token"):
            await session.next(changed)
    finally:
        await session.aclose()


async def test_quiescent_session_rejects_next_after_terminal_acknowledgement() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        settled = reduce_graph_run(claimed, first.command)
        with pytest.raises(StopAsyncIteration):
            await session.next(settled)
        with pytest.raises(ResultCollectionError, match="quiescent"):
            await session.next(settled)
    finally:
        await session.aclose()


async def test_invalid_routing_completion_drains_a_typed_sibling() -> None:
    async def invalid(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    async def valid(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = compile_graph(
        GraphDefinition(
            GraphDefinitionId("session.invalid-route"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), invalid), NodeDefinition(GraphNodeId("b"), valid)),
            (ConditionalEdge(GraphNodeId("a"), GraphRouteId("go"), GraphNodeId("b")),),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        with pytest.raises(InvalidRoutingCommandError):
            await session.next(after)
    finally:
        await session.aclose()


async def test_invalid_queued_routing_completion_becomes_an_ordinary_error() -> None:
    async def valid(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    async def invalid(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = compile_graph(
        GraphDefinition(
            GraphDefinitionId("session.queued-invalid-route"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), valid), NodeDefinition(GraphNodeId("b"), invalid)),
            (ConditionalEdge(GraphNodeId("b"), GraphRouteId("go"), END),),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        assert first.result.task.node_id == GraphNodeId("a")
        after = reduce_graph_run(claimed, first.command)
        with pytest.raises(InvalidRoutingCommandError):
            await session.next(after)
    finally:
        await session.aclose()


async def test_no_executable_pending_node_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    compiled = graph((NodeDefinition(GraphNodeId("a"), node),), entries=("a",))
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)

    def no_selection(_self: GraphExecutionSession[str, str]) -> tuple[ExecutableTask[str], ...]:
        return ()

    monkeypatch.setattr(type(session), "_select_ordinary", no_selection)
    try:
        with pytest.raises(ResultCollectionError, match="no executable pending"):
            await session.next(claimed)
    finally:
        await session.aclose()


async def test_multiple_ordinary_errors_are_reported_by_canonical_task_identity() -> None:
    release_a = asyncio.Event()
    completion_order: list[str] = []

    async def fail_a(value: str) -> NodeSuccess[str]:
        await release_a.wait()
        completion_order.append("a")
        raise ValueError(value)

    async def fail_b(value: str) -> NodeSuccess[str]:
        completion_order.append("b")
        release_a.set()
        raise RuntimeError(value)

    compiled = graph(
        (
            NodeDefinition(GraphNodeId("a"), fail_a),
            NodeDefinition(GraphNodeId("b"), fail_b),
        ),
        entries=("a", "b"),
    )
    state = reduce_graph_run(None, GraphExecutor(compiled).start_command(GraphRunId("run")))
    _executor, claimed, session = await claim_session(compiled, state)
    try:
        with pytest.raises(ValueError, match="input"):
            await session.next(claimed)
        assert completion_order == ["b", "a"]
        assert session.quiescent
    finally:
        await session.aclose()
