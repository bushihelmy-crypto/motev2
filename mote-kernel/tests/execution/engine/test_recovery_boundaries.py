import pytest

from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph import (
    END,
    CompiledGraph,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    NodeDefinition,
    NodeSuccess,
    compile_graph,
)
from mote_kernel.execution.identity import ExecutionRequestAttemptId
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import ExecutableFrontier, ReadyToResolve, TaskSuccess
from mote_kernel.state.graph_state import (
    FenceGraphExecution,
    GraphRunId,
    GraphRunState,
    PendingGraphNode,
    SucceededGraphNode,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


async def identity(value: str) -> NodeSuccess[str]:
    return NodeSuccess(value)


def graph(
    *nodes: str,
    edges: tuple[DirectEdge, ...] = (),
    entries: tuple[str, ...] | None = None,
) -> CompiledGraph[str, str]:
    selected = tuple(nodes) if entries is None else entries
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.graph"),
            GraphDefinitionVersion(1),
            tuple(NodeDefinition(GraphNodeId(node), identity) for node in nodes),
            tuple(edges),
            tuple(GraphNodeId(node) for node in selected),
        )
    )


async def claim(
    executor: GraphExecutor[str, str], state: GraphRunState
) -> tuple[GraphRunState, GraphExecutionSession[str, str]]:
    prepared = await executor.prepare(StepRequest(state, "input", ExecutionRequestAttemptId("request"), ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), ()),
    )
    return claimed, session


async def test_completion_before_command_apply_is_replayable_after_crash() -> None:
    compiled = graph("a")
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    claimed, session = await claim(executor, initial)
    try:
        _ = await session.next(claimed)
        # The command is intentionally dropped.  The durable state remains Pending.
        settlement = claimed.frontier.nodes[0].settlement
        assert isinstance(settlement, PendingGraphNode)
        assert settlement == PendingGraphNode(settlement.input)
    finally:
        await session.aclose()
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    retry_claimed, retry_session = await claim(executor, fenced)
    try:
        replay = await retry_session.next(retry_claimed)
        assert isinstance(replay.result, TaskSuccess)
        assert replay.result.output == "input"
    finally:
        await retry_session.aclose()


async def test_applied_settlement_survives_crash_before_waiter_start() -> None:
    compiled = graph("a", "b", entries=("a", "b"))
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    claimed, session = await claim(executor, initial)
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
    finally:
        await session.aclose()
    assert not isinstance(after.frontier.nodes[0].settlement, PendingGraphNode)
    assert after.execution is not None
    assert not isinstance(after.frontier.nodes[0].settlement, PendingGraphNode)


async def test_final_settlement_recovers_as_ready_to_resolve_without_reexecution() -> None:
    compiled = graph("a", edges=(DirectEdge(GraphNodeId("a"), END),))
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    claimed, session = await claim(executor, initial)
    try:
        result = await session.next(claimed)
        settled = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()
    assert settled.execution is None
    ready = await executor.prepare(StepRequest(settled, "input", ExecutionRequestAttemptId("request-2"), ()))
    assert isinstance(ready, ReadyToResolve)
    completed = reduce_graph_run(settled, ready.command)
    assert completed.status.name == "COMPLETED"


async def test_session_rejects_state_that_skips_the_acknowledged_revision() -> None:
    compiled = graph("a", "b", entries=("a", "b"))
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    claimed, session = await claim(executor, initial)
    try:
        first = await session.next(claimed)
        with pytest.raises(Exception, match="successor revision"):
            await session.next(claimed)
        acknowledged = reduce_graph_run(claimed, first.command)
        second = await session.next(acknowledged)
        assert second.result.task.node_id != first.result.task.node_id
    finally:
        await session.aclose()


async def test_exact_fence_after_partial_settlement_does_not_reset_siblings() -> None:
    compiled = graph("a", "b", entries=("a", "b"))
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    claimed, session = await claim(executor, initial)
    try:
        result = await session.next(claimed)
        partial = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()
    assert partial.execution is not None
    fenced = reduce_graph_run(partial, FenceGraphExecution(partial.revision, partial.execution.token))
    assert not isinstance(fenced.frontier.nodes[0].settlement, PendingGraphNode)
    assert isinstance(fenced.frontier.nodes[1].settlement, PendingGraphNode)


async def test_ordinary_error_after_applied_sibling_settlement_preserves_that_sibling() -> None:
    async def good(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    async def bad(value: str) -> NodeSuccess[str]:
        raise RuntimeError("later failure")

    compiled = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.error"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), good), NodeDefinition(GraphNodeId("b"), bad)),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(
        StepRequest(
            initial,
            "input",
            ExecutionRequestAttemptId("request"),
            (),
        )
    )
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), ()),
    )
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        assert first.result.task.node_id == GraphNodeId("a")
        with pytest.raises(RuntimeError, match="later failure"):
            await session.next(after)
    finally:
        await session.aclose()
    assert isinstance(after.frontier.nodes[0].settlement, SucceededGraphNode)
    assert isinstance(after.frontier.nodes[1].settlement, PendingGraphNode)
    assert after.execution is not None
    fenced = reduce_graph_run(after, FenceGraphExecution(after.revision, after.execution.token))
    assert isinstance(fenced.frontier.nodes[0].settlement, SucceededGraphNode)
    assert isinstance(fenced.frontier.nodes[1].settlement, PendingGraphNode)
