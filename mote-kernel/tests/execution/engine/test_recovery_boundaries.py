from dataclasses import replace

import pytest
from tests.execution.driver import step_request
from tests.execution.engine.factories import callable_node, output_value

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import DirectEdge
from mote_kernel.execution.graph.ports import normalize_graph_output_declarations
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.result import ExecutableFrontier, ReadyToResolve, TaskSuccess
from mote_kernel.state.graph_state import (
    FenceGraphExecution,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    PendingGraphNode,
    SucceededGraphNode,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


def graph(
    *nodes: str,
    edges: tuple[DirectEdge, ...] = (),
    entries: tuple[str, ...] | None = None,
) -> CompiledGraph[str]:
    selected = tuple(nodes) if entries is None else entries
    incoming = {edge.target for edge in edges if edge.target != END}
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.graph"),
            GraphDefinitionVersion(1),
            tuple(callable_node(node) for node in nodes),
            tuple(edges),
            tuple(GraphNodeId(node) for node in selected if GraphNodeId(node) in incoming),
            normalize_graph_output_declarations({}),
        )
    )


async def claim(
    graph: CompiledGraph[str],
    executor: GraphExecutor[str],
    state: GraphRunState,
) -> tuple[GraphRunState, GraphExecutionSession[str]]:
    request = step_request(graph, state, "input").execution_request()
    prepared = await executor.prepare(request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        step_request(graph, claimed, "input").execution_request(),
    )
    return claimed, session


async def test_completion_before_command_apply_is_replayable_after_crash() -> None:
    compiled = graph("a")
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    claimed, session = await claim(compiled, executor, initial)
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
    retry_claimed, retry_session = await claim(compiled, executor, fenced)
    try:
        replay = await retry_session.next(retry_claimed)
        assert isinstance(replay.result, TaskSuccess)
        assert output_value(replay.result.output) == "input"
    finally:
        await retry_session.aclose()


async def test_applied_settlement_survives_crash_before_waiter_start() -> None:
    compiled = graph("a", "b", entries=("a", "b"))
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    claimed, session = await claim(compiled, executor, initial)
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
    initial = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    claimed, session = await claim(compiled, executor, initial)
    try:
        result = await session.next(claimed)
        settled = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()
    assert settled.execution is None
    ready = await executor.prepare(step_request(compiled, settled, "input").execution_request())
    assert isinstance(ready, ReadyToResolve)
    completed = reduce_graph_run(settled, ready.command)
    assert completed.status.name == "COMPLETED"


async def test_session_rejects_state_that_skips_the_acknowledged_revision() -> None:
    compiled = graph("a", "b", entries=("a", "b"))
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    claimed, session = await claim(compiled, executor, initial)
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
    initial = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    claimed, session = await claim(compiled, executor, initial)
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
    async def good(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    async def bad(values: Graph.Values[str]) -> Graph.Values[str]:
        del values
        raise RuntimeError("later failure")

    compiled = compile_graph(
        GraphDefinition(
            GraphDefinitionId("recovery.error"),
            GraphDefinitionVersion(1),
            (
                replace(callable_node("a"), operation=good),
                replace(callable_node("b"), operation=bad),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (),
            normalize_graph_output_declarations({}),
        )
    )
    executor = GraphExecutor(compiled)
    initial = reduce_graph_run(None, project_start_graph_command(compiled, GraphRunId("run")))
    prepared = await executor.prepare(step_request(compiled, initial, "input").execution_request())
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        step_request(compiled, claimed, "input").execution_request(),
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
