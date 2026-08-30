import asyncio
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.driver import step_request

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.engine.superstep import ExecutableFrontier
from mote_kernel.execution.engine.task import ExecutableTask
from mote_kernel.execution.errors import InvalidRoutingCommandError, ResultCollectionError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition, NodeCallable
from mote_kernel.execution.graph.ports import (
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.state.graph_state import (
    FailedGraphNode,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFailure,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    InterruptedGraphNode,
    PendingGraphNode,
    ResourceId,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


class _TextCodec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


def node(
    node_id: str,
    operation: NodeCallable[str],
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


def graph(
    nodes: tuple[CallableNodeDefinition[str], ...],
    *,
    resources: tuple[ResourceDefinition, ...] = (),
) -> CompiledGraph[str]:
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("session.graph"),
            version=GraphDefinitionVersion(1),
            nodes=nodes,
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
            resources=resources,
        )
    )


def interrupt_graph(
    operation: NodeCallable[str],
    *,
    with_sibling: bool = False,
) -> CompiledGraph[str]:
    codec = _TextCodec()
    nodes = (node("a", operation),)
    if with_sibling:

        async def sibling(values: Graph.Values[str]) -> Graph.Values[str]:
            return values

        nodes = (*nodes, node("b", sibling))
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("session.interrupt"),
            version=GraphDefinitionVersion(1),
            nodes=nodes,
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
            resume_input=ResumeInputBinding(
                GraphResumeInputCodecId("session.v1"),
                1,
                codec,
                codec,
            ),
        )
    )


def request(
    graph: CompiledGraph[str],
    state: GraphRunState,
    limits: ExecutionLimits,
) -> StepRequest[str]:
    return step_request(graph, state, "input", limits=limits).execution_request()


def claim_session(
    graph: CompiledGraph[str],
    state: GraphRunState,
    *,
    limits: ExecutionLimits | None = None,
) -> tuple[GraphExecutor[str], GraphRunState, GraphExecutionSession[str]]:
    effective_limits = ExecutionLimits() if limits is None else limits
    executor = GraphExecutor(graph)
    execution_request = request(graph, state, effective_limits)
    prepared = executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = executor.issue_session(prepared.claim, claimed)
    return executor, claimed, session


async def test_completion_is_yielded_before_the_frontier_finishes() -> None:
    a_done = asyncio.Event()
    release_b = asyncio.Event()

    async def a(_values: Graph.Values[str]) -> Graph.Values[str]:
        a_done.set()
        return Graph.values(value="a")

    async def b(_values: Graph.Values[str]) -> Graph.Values[str]:
        await release_b.wait()
        return Graph.values(value="b")

    compiled = graph((node("a", a), node("b", b)))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    try:
        first = await asyncio.wait_for(session.next(claimed), timeout=1)
        assert first.result.task.node_id == GraphNodeId("a")
        assert a_done.is_set()
        after_a = reduce_graph_run(claimed, first.command)
        assert after_a.execution is not None
        assert isinstance(after_a.frontier.nodes[1].settlement, PendingGraphNode)
        release_b.set()
        second = await asyncio.wait_for(session.next(after_a), timeout=1)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await session.aclose()


async def test_resource_release_makes_waiter_selectable_on_next_acknowledged_state() -> None:
    resource = ResourceId("file")

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph(
        (
            node("a", operation, resources=(resource,)),
            node("b", operation, resources=(resource,)),
        ),
        resources=(ResourceDefinition(resource, 0),),
    )
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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

    async def owner(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    async def waiter(values: Graph.Values[str]) -> Graph.Values[str]:
        waiter_started.set()
        return values

    compiled = graph(
        (
            node("a", owner, resources=(resource,)),
            node("b", waiter, resources=(resource,)),
        ),
        resources=(ResourceDefinition(resource, 0),),
    )
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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

    async def owner(_values: Graph.Values[str]) -> Graph.Values[str]:
        owner_started.set()
        await release_initial.wait()
        return Graph.values(value="a")

    async def waiter(_values: Graph.Values[str]) -> Graph.Values[str]:
        waiter_started.set()
        await release_waiter.wait()
        return Graph.values(value="b")

    async def sibling(_values: Graph.Values[str]) -> Graph.Values[str]:
        sibling_started.set()
        await release_initial.wait()
        return Graph.values(value="x")

    compiled = graph(
        (
            node("a", owner, resources=(resource,)),
            node("b", waiter, resources=(resource,)),
            node("x", sibling),
        ),
        resources=(ResourceDefinition(resource, 0),),
    )
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(
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

    async def owner(_values: Graph.Values[str]) -> Graph.Values[str]:
        owner_started.set()
        await release_initial.wait()
        return Graph.values(value="a")

    async def waiter(_values: Graph.Values[str]) -> Graph.Values[str]:
        waiter_started.set()
        return Graph.values(value="b")

    async def failing_sibling(_values: Graph.Values[str]) -> Graph.Values[str]:
        sibling_started.set()
        await release_initial.wait()
        raise RuntimeError("sibling failed")

    compiled = graph(
        (
            node("a", owner, resources=(resource,)),
            node("b", waiter, resources=(resource,)),
            node("x", failing_sibling),
        ),
        resources=(ResourceDefinition(resource, 0),),
    )
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(
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

    def operation(name: str) -> NodeCallable[str]:
        async def run(values: Graph.Values[str]) -> Graph.Values[str]:
            started.append(name)
            await gate.wait()
            return values

        return run

    compiled = graph(
        (
            node("a", operation("a")),
            node("b", operation("b")),
            node("c", operation("c")),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(
        compiled,
        state,
        limits=ExecutionLimits(max_parallel_tasks=2),
    )
    try:
        first_wait = asyncio.create_task(session.next(claimed))
        for _ in range(10):
            await asyncio.sleep(0)
            if len(started) == 2:
                break
        assert started == ["a", "b"]
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

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal invocations
        invocations += 1
        started.set()
        await release.wait()
        return values

    compiled = graph((node("a", operation),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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

    def good(name: str) -> NodeCallable[str]:
        async def execute(values: Graph.Values[str]) -> Graph.Values[str]:
            started.append(name)
            await gate.wait()
            return values

        return execute

    async def bad(_values: Graph.Values[str]) -> Graph.Values[str]:
        started.append("b")
        raise RuntimeError("bad")

    compiled = graph(
        (
            node("a", good("a")),
            node("b", bad),
            node("c", good("c")),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(
        compiled,
        state,
        limits=ExecutionLimits(max_parallel_tasks=2),
    )
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

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal cancellations
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancellations += 1
            cancelled.set()
            raise
        return values

    compiled = graph((node("a", operation),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    waiter = asyncio.create_task(session.next(claimed))
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.gather(session.aclose(), session.aclose())
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cancelled.is_set()
    assert cancellations == 1
    with pytest.raises(ResultCollectionError):
        await session.next(claimed)


async def test_async_context_manager_reaches_quiescence_and_closed_next_fails_closed() -> None:
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    executor, claimed, session = claim_session(compiled, state)
    del executor
    async with session:
        completed = await session.next(claimed)
        settled = reduce_graph_run(claimed, completed.command)
        with pytest.raises(StopAsyncIteration):
            await session.next(settled)
    with pytest.raises(ResultCollectionError, match="closed"):
        await session.next(settled)


async def test_next_cancellation_performs_cancellation_safe_close() -> None:
    started = asyncio.Event()

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        started.set()
        await asyncio.sleep(10)
        return values

    compiled = graph((node("a", operation),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    waiter = asyncio.create_task(session.next(claimed))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    with pytest.raises(ResultCollectionError, match="closed"):
        await session.next(claimed)


async def test_repeated_next_cancellation_waits_for_cleanup_to_finish() -> None:
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()
            raise
        return values

    compiled = graph((node("a", operation),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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
    finally:
        release_cleanup.set()
        try:
            await asyncio.wait_for(waiter, timeout=1)
        except asyncio.CancelledError:
            cancelled = True
        await session.aclose()

    assert cancelled
    assert cleanup_finished.is_set()
    with pytest.raises(ResultCollectionError, match="closed"):
        await session.next(claimed)


async def test_failure_completion_is_settled_as_a_node_failure() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    compiled = graph((node("a", fail),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    try:
        completed = await session.next(claimed)
        settled = reduce_graph_run(claimed, completed.command)
    finally:
        await session.aclose()
    assert isinstance(settled.frontier.nodes[0].settlement, FailedGraphNode)


async def test_interrupt_completion_is_settled_with_a_state_identity() -> None:
    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"question")

    compiled = interrupt_graph(interrupt)
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    try:
        completed = await session.next(claimed)
        settled = reduce_graph_run(claimed, completed.command)
    finally:
        await session.aclose()
    settlement = settled.frontier.nodes[0].settlement
    assert isinstance(settlement, InterruptedGraphNode)
    assert settlement.interrupt.identity.node_id == GraphNodeId("a")


async def test_session_acknowledges_failure_and_interrupt_variants_before_next_completion() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    failure_graph = graph((node("a", fail), node("b", fail)))
    failure_state = reduce_graph_run(None, project_start_graph_command(failure_graph, GraphRunId("failure-run")))
    _executor, failure_claimed, failure_session = claim_session(failure_graph, failure_state)
    try:
        first = await failure_session.next(failure_claimed)
        after = reduce_graph_run(failure_claimed, first.command)
        second = await failure_session.next(after)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await failure_session.aclose()

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"question")

    interrupt_compiled = interrupt_graph(interrupt, with_sibling=True)
    interrupt_state = reduce_graph_run(
        None,
        project_start_graph_command(interrupt_compiled, GraphRunId("interrupt-run")),
    )
    _executor, interrupt_claimed, interrupt_session = claim_session(
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
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation), node("b", operation)))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    try:
        _ = await session.next(claimed)
        invalid = replace(claimed, revision=claimed.revision + 1)
        with pytest.raises(ResultCollectionError, match="exact reducer successor"):
            await session.next(invalid)
    finally:
        await session.aclose()


async def test_session_acknowledgement_rejects_an_unrelated_settlement_change() -> None:
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation), node("b", operation)))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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
        with pytest.raises(ResultCollectionError, match="exact reducer successor"):
            await session.next(unrelated)
    finally:
        await session.aclose()


async def test_session_acknowledgement_rejects_a_mismatched_settlement_variant() -> None:
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation), node("b", operation)))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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
        with pytest.raises(ResultCollectionError, match="exact reducer successor"):
            await session.next(invalid)
    finally:
        await session.aclose()


async def test_session_acknowledgement_rejects_a_missing_partial_execution_token() -> None:
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation), node("b", operation)))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        invalid = replace(after, execution=None)
        with pytest.raises(ResultCollectionError, match="exact reducer successor"):
            await session.next(invalid)
    finally:
        await session.aclose()


async def test_session_acknowledgement_rejects_a_changed_execution_token() -> None:
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation), node("b", operation)))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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
        with pytest.raises(ResultCollectionError, match="exact reducer successor"):
            await session.next(changed)
    finally:
        await session.aclose()


async def test_quiescent_session_rejects_next_after_terminal_acknowledgement() -> None:
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
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
    async def invalid(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    async def valid(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("session.invalid-route"),
            version=GraphDefinitionVersion(1),
            nodes=(node("a", invalid), node("b", valid)),
            edges=(ConditionalEdge(GraphNodeId("a"), GraphRouteId("go"), GraphNodeId("b")),),
            entries=(GraphNodeId("b"),),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        with pytest.raises(InvalidRoutingCommandError):
            await session.next(after)
    finally:
        await session.aclose()


async def test_invalid_queued_routing_completion_becomes_an_ordinary_error() -> None:
    async def valid(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    async def invalid(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("session.queued-invalid-route"),
            version=GraphDefinitionVersion(1),
            nodes=(node("a", valid), node("b", invalid)),
            edges=(ConditionalEdge(GraphNodeId("b"), GraphRouteId("go"), END),),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    try:
        first = await session.next(claimed)
        assert first.result.task.node_id == GraphNodeId("a")
        after = reduce_graph_run(claimed, first.command)
        with pytest.raises(InvalidRoutingCommandError):
            await session.next(after)
    finally:
        await session.aclose()


async def test_no_executable_pending_node_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    compiled = graph((node("a", operation),))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)

    def no_selection(_self: GraphExecutionSession[str]) -> tuple[ExecutableTask[str], ...]:
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

    async def fail_a(values: Graph.Values[str]) -> Graph.Values[str]:
        await release_a.wait()
        completion_order.append("a")
        raise ValueError(values["value"])

    async def fail_b(values: Graph.Values[str]) -> Graph.Values[str]:
        completion_order.append("b")
        release_a.set()
        raise RuntimeError(values["value"])

    compiled = graph((node("a", fail_a), node("b", fail_b)))
    state = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    _executor, claimed, session = claim_session(compiled, state)
    try:
        with pytest.raises(ValueError, match="input"):
            await session.next(claimed)
        assert completion_order == ["b", "a"]
        with pytest.raises(ResultCollectionError, match="quiescent"):
            await session.next(claimed)
    finally:
        await session.aclose()
