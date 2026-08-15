import asyncio
from dataclasses import replace

import pytest

from mote_kernel.execution import (
    AbortedGraph,
    AwaitingResume,
    ExecutableFrontier,
    GraphExecutionSession,
    GraphExecutor,
    ReadyToResolve,
    ResumeFailedNodeRequest,
    SkipFailedNodeRequest,
    StepRequest,
    UseRequestInput,
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
    JoinEdge,
    Node,
    NodeDefinition,
    NodeFailure,
    NodeInterrupt,
    NodeOutcome,
    NodeSuccess,
    ResumeInputBinding,
    SelectGraphRoute,
    compile_graph,
)
from mote_kernel.execution.identity import ExecutionRequestAttemptId
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ContinueGraphRouting,
    FenceGraphExecution,
    GraphAbortReason,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    PendingGraphNode,
    UseStepRequestInput,
    frontier_node,
    graph_interrupt_id,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


class Codec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


class ValidatingCodec(Codec):
    def encode(self, value: str) -> bytes:
        if value.startswith("invalid"):
            raise ValueError("invalid resume input")
        return super().encode(value)

    def decode(self, payload: bytes) -> str:
        value = super().decode(payload)
        if value.startswith("invalid"):
            raise ValueError("invalid resume payload")
        return value


def interrupt_graph(node: Node[str, str], *, codec: Codec | None = None) -> CompiledGraph[str, str]:
    effective_codec = Codec() if codec is None else codec
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), node),),
            (DirectEdge(GraphNodeId("a"), END),),
            (GraphNodeId("a"),),
            resume_input=ResumeInputBinding(
                GraphResumeInputCodecId("input.v1"),
                1,
                effective_codec,
                effective_codec,
            ),
        )
    )


def interrupt_pair_graph(
    first: Node[str, str], second: Node[str, str], *, codec: Codec | None = None
) -> CompiledGraph[str, str]:
    effective_codec = Codec() if codec is None else codec
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.pair"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), first), NodeDefinition(GraphNodeId("b"), second)),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            resume_input=ResumeInputBinding(
                GraphResumeInputCodecId("input.v1"),
                1,
                effective_codec,
                effective_codec,
            ),
        )
    )


def started(executor: GraphExecutor[str, str]) -> GraphRunState:
    return reduce_graph_run(None, executor.start_command(GraphRunId("run")))


async def claim_and_session(
    executor: GraphExecutor[str, str], state: GraphRunState
) -> tuple[GraphRunState, GraphExecutionSession[str, str]]:
    request = StepRequest(state, "input", ExecutionRequestAttemptId("request"), ())
    prepared = await executor.prepare(request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), ()),
    )
    return claimed, session


async def settle_pending(executor: GraphExecutor[str, str], state: GraphRunState, node_input: str) -> GraphRunState:
    request_id = ExecutionRequestAttemptId("settle-request")
    prepared = await executor.prepare(StepRequest(state, node_input, request_id, ()))
    assert isinstance(prepared, ExecutableFrontier)
    current = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(current, node_input, request_id, ()))
    try:
        while current.execution is not None:
            completed = await session.next(current)
            current = reduce_graph_run(current, completed.command)
    finally:
        await session.aclose()
    return current


async def resolve_ready(executor: GraphExecutor[str, str], state: GraphRunState) -> GraphRunState:
    disposition = await executor.prepare(
        StepRequest(state, "resolution-input", ExecutionRequestAttemptId("resolve-request"), ())
    )
    assert isinstance(disposition, ReadyToResolve)
    return reduce_graph_run(state, disposition.command)


async def test_interrupt_is_a_node_completion_and_creates_awaiting_resume_state() -> None:
    async def interrupt(value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(b"question"))

    graph = interrupt_graph(interrupt)
    executor = GraphExecutor(graph)
    state = started(executor)
    claimed, session = await claim_and_session(executor, state)
    try:
        result = await session.next(claimed)
        interrupted = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()
    assert isinstance(interrupted.frontier.nodes[0].settlement, InterruptedGraphNode)
    disposition = await executor.prepare(StepRequest(interrupted, "input", ExecutionRequestAttemptId("request"), ()))
    assert isinstance(disposition, AwaitingResume)


async def test_interrupt_identity_is_coordinate_scoped_and_stale_ids_fail_closed() -> None:
    async def interrupt(value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(b"question"))

    graph = interrupt_graph(interrupt)
    executor = GraphExecutor(graph)
    state = started(executor)
    claimed, session = await claim_and_session(executor, state)
    try:
        result = await session.next(claimed)
        interrupted = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()
    settlement = interrupted.frontier.nodes[0].settlement
    assert isinstance(settlement, InterruptedGraphNode)
    identity = settlement.interrupt.identity
    exact = graph_interrupt_id(identity.run_id, identity.superstep, identity.node_id, identity.execution_generation)
    with pytest.raises(Exception, match="does not match"):
        executor.resume(
            ResumeRequest(
                interrupted,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        graph_interrupt_id(GraphRunId("wrong"), 0, GraphNodeId("a"), 1),
                        OverrideNodeInput("answer"),
                    ),
                ),
            )
        )
    command = executor.resume(
        ResumeRequest(
            interrupted,
            (ResumeInterruptedNodeRequest(GraphNodeId("a"), exact, OverrideNodeInput("answer")),),
        )
    )
    resumed = reduce_graph_run(interrupted, command)
    assert isinstance(resumed.frontier.nodes[0].settlement, PendingGraphNode)


async def test_resume_reuses_same_activation_coordinates_with_new_execution_generation() -> None:
    calls = 0

    async def node(value: str) -> NodeOutcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        return NodeSuccess(value)

    graph = interrupt_graph(node)
    executor = GraphExecutor(graph)
    state = started(executor)
    claimed, session = await claim_and_session(executor, state)
    try:
        first = await session.next(claimed)
        interrupted = reduce_graph_run(claimed, first.command)
    finally:
        await session.aclose()
    settlement = interrupted.frontier.nodes[0].settlement
    assert isinstance(settlement, InterruptedGraphNode)
    identity = settlement.interrupt.identity
    resumed = reduce_graph_run(
        interrupted,
        executor.resume(
            ResumeRequest(
                interrupted,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        graph_interrupt_id(
                            identity.run_id, identity.superstep, identity.node_id, identity.execution_generation
                        ),
                        OverrideNodeInput("answer"),
                    ),
                ),
            )
        ),
    )
    claimed_again, session_again = await claim_and_session(executor, resumed)
    try:
        result = await session_again.next(claimed_again)
        completed = reduce_graph_run(claimed_again, result.command)
    finally:
        await session_again.aclose()
    assert completed.frontier.nodes[0].settlement.__class__.__name__ == "SucceededGraphNode"
    assert completed.execution_sequence == identity.execution_generation + 1


async def test_interrupt_result_payload_remains_opaque_bytes() -> None:
    async def interrupt(value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(b"\x00question"))

    graph = interrupt_graph(interrupt)
    executor = GraphExecutor(graph)
    state = started(executor)
    claimed, session = await claim_and_session(executor, state)
    try:
        result = await session.next(claimed)
        interrupted = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()
    settlement = interrupted.frontier.nodes[0].settlement
    assert isinstance(settlement, InterruptedGraphNode)
    assert settlement.interrupt.request_payload == b"\x00question"


async def test_interrupt_completion_does_not_wait_for_a_slow_sibling() -> None:
    release = asyncio.Event()

    async def interrupt(value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(b"question"))

    async def slow(value: str) -> NodeSuccess[str]:
        await release.wait()
        return NodeSuccess(value)

    graph = interrupt_pair_graph(interrupt, slow)
    executor = GraphExecutor(graph)
    state = started(executor)
    claimed, session = await claim_and_session(executor, state)
    try:
        first = await asyncio.wait_for(session.next(claimed), timeout=1)
        assert first.result.task.node_id == GraphNodeId("a")
        after = reduce_graph_run(claimed, first.command)
        assert isinstance(after.frontier.nodes[0].settlement, InterruptedGraphNode)
        assert isinstance(after.frontier.nodes[1].settlement, PendingGraphNode)
        release.set()
        second = await asyncio.wait_for(session.next(after), timeout=1)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await session.aclose()


async def test_multiple_interrupts_can_be_resumed_one_at_a_time_by_exact_identity() -> None:
    async def interrupt(value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(value.encode()))

    graph = interrupt_pair_graph(interrupt, interrupt)
    executor = GraphExecutor(graph)
    state = started(executor)
    claimed, session = await claim_and_session(executor, state)
    try:
        first = await session.next(claimed)
        after_first = reduce_graph_run(claimed, first.command)
        second = await session.next(after_first)
        interrupted = reduce_graph_run(after_first, second.command)
    finally:
        await session.aclose()
    first_settlement = interrupted.frontier.nodes[0].settlement
    second_settlement = interrupted.frontier.nodes[1].settlement
    assert isinstance(first_settlement, InterruptedGraphNode)
    assert isinstance(second_settlement, InterruptedGraphNode)
    first_id = graph_interrupt_id(
        first_settlement.interrupt.identity.run_id,
        first_settlement.interrupt.identity.superstep,
        first_settlement.interrupt.identity.node_id,
        first_settlement.interrupt.identity.execution_generation,
    )
    resumed_command = executor.resume(
        ResumeRequest(
            interrupted,
            (ResumeInterruptedNodeRequest(GraphNodeId("a"), first_id, OverrideNodeInput("answer-a")),),
        )
    )
    resumed = reduce_graph_run(interrupted, resumed_command)
    assert isinstance(resumed.frontier.nodes[0].settlement, PendingGraphNode)
    assert isinstance(resumed.frontier.nodes[1].settlement, InterruptedGraphNode)
    with pytest.raises(Exception, match="does not match"):
        executor.resume(
            ResumeRequest(
                interrupted,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        graph_interrupt_id(GraphRunId("wrong"), 0, GraphNodeId("a"), 1),
                        OverrideNodeInput("x"),
                    ),
                ),
            )
        )


async def test_interrupt_round_trip_keeps_request_and_resume_payloads_distinct() -> None:
    received: list[str] = []

    async def node(node_input: str) -> NodeOutcome[str]:
        received.append(node_input)
        if len(received) == 1:
            return NodeInterrupt(GraphInterruptPayload(b"request-question"))
        return NodeSuccess(node_input)

    graph = interrupt_graph(node)
    executor = GraphExecutor(graph)
    interrupted = await settle_pending(executor, started(executor), "ordinary")
    settlement = interrupted.frontier.nodes[0].settlement
    assert isinstance(settlement, InterruptedGraphNode)
    identity = settlement.interrupt.identity
    interrupt_id = graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )
    resumed = reduce_graph_run(
        interrupted,
        executor.resume(
            ResumeRequest(
                interrupted,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        interrupt_id,
                        OverrideNodeInput("resume-answer"),
                    ),
                ),
            )
        ),
    )
    pending = resumed.frontier.nodes[0].settlement
    assert isinstance(pending, PendingGraphNode)
    assert isinstance(pending.input, OverrideGraphNodeInput)
    assert settlement.interrupt.request_payload == GraphInterruptPayload(b"request-question")
    assert pending.input.payload == GraphResumeInputPayload(b"resume-answer")

    settled = await settle_pending(executor, resumed, "ignored")
    assert settled.status is GraphRunStatus.RUNNING
    assert received == ["ordinary", "resume-answer"]


async def test_failure_resume_delivers_default_and_override_inputs_per_node() -> None:
    received: dict[str, list[str]] = {"a": [], "b": []}

    def fail_once(name: str) -> Node[str, str]:
        async def node(node_input: str) -> NodeOutcome[str]:
            received[name].append(node_input)
            if len(received[name]) == 1:
                return NodeFailure(GraphFailure(f"{name} failed"))
            return NodeSuccess(node_input)

        return node

    graph = interrupt_pair_graph(fail_once("a"), fail_once("b"))
    executor = GraphExecutor(graph)
    failed = await settle_pending(executor, started(executor), "initial")
    resumed = reduce_graph_run(
        failed,
        executor.resume(
            ResumeRequest(
                failed,
                (
                    ResumeFailedNodeRequest(GraphNodeId("a"), UseRequestInput()),
                    ResumeFailedNodeRequest(GraphNodeId("b"), OverrideNodeInput("override-b")),
                ),
            )
        ),
    )
    settled = await settle_pending(executor, resumed, "request-default")
    assert settled.execution is None
    assert received == {
        "a": ["initial", "request-default"],
        "b": ["initial", "override-b"],
    }


async def test_resume_codec_errors_before_claim_leave_state_quiescent() -> None:
    async def fail(node_input: str) -> NodeFailure:
        return NodeFailure(GraphFailure(node_input))

    graph = interrupt_graph(fail, codec=ValidatingCodec())
    executor = GraphExecutor(graph)
    failed = await settle_pending(executor, started(executor), "initial")
    with pytest.raises(ValueError, match="invalid resume input"):
        executor.resume(
            ResumeRequest(
                failed,
                (ResumeFailedNodeRequest(GraphNodeId("a"), OverrideNodeInput("invalid-value")),),
            )
        )
    assert failed.execution is None

    malformed = replace(
        failed,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("a"),
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"invalid-payload"))),
                ),
            )
        ),
    )
    with pytest.raises(ValueError, match="invalid resume payload"):
        await executor.prepare(
            StepRequest(
                malformed,
                "ordinary",
                ExecutionRequestAttemptId("decode-request"),
                (),
            )
        )
    assert malformed.execution is None


async def test_interrupt_override_is_redelivered_after_error_and_exact_fence() -> None:
    received: list[str] = []

    async def node(node_input: str) -> NodeOutcome[str]:
        received.append(node_input)
        if len(received) == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        if len(received) == 2:
            raise RuntimeError("worker stopped before settlement")
        return NodeSuccess(node_input)

    graph = interrupt_graph(node)
    executor = GraphExecutor(graph)
    interrupted = await settle_pending(executor, started(executor), "initial")
    settlement = interrupted.frontier.nodes[0].settlement
    assert isinstance(settlement, InterruptedGraphNode)
    identity = settlement.interrupt.identity
    resumed = reduce_graph_run(
        interrupted,
        executor.resume(
            ResumeRequest(
                interrupted,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        graph_interrupt_id(
                            identity.run_id,
                            identity.superstep,
                            identity.node_id,
                            identity.execution_generation,
                        ),
                        OverrideNodeInput("approved"),
                    ),
                ),
            )
        ),
    )

    request_id = ExecutionRequestAttemptId("failing-request")
    prepared = await executor.prepare(StepRequest(resumed, "ordinary", request_id, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(resumed, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "ordinary", request_id, ()))
    try:
        with pytest.raises(RuntimeError, match="before settlement"):
            await session.next(claimed)
    finally:
        await session.aclose()
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    pending = frontier_node(fenced.frontier, GraphNodeId("a"))
    assert pending is not None
    assert pending.settlement == PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"approved")))

    settled = await settle_pending(executor, fenced, "different")
    assert settled.execution is None
    assert received == ["initial", "approved", "approved"]


async def test_repeated_interrupt_uses_new_generation_and_consumes_old_identity() -> None:
    async def interrupt(node_input: str) -> NodeInterrupt:
        del node_input
        return NodeInterrupt(GraphInterruptPayload(b"again"))

    graph = interrupt_graph(interrupt)
    executor = GraphExecutor(graph)
    first = await settle_pending(executor, started(executor), "ordinary")
    first_node = frontier_node(first.frontier, GraphNodeId("a"))
    assert first_node is not None and isinstance(first_node.settlement, InterruptedGraphNode)
    first_identity = first_node.settlement.interrupt.identity
    first_id = graph_interrupt_id(
        first_identity.run_id,
        first_identity.superstep,
        first_identity.node_id,
        first_identity.execution_generation,
    )
    resumed = reduce_graph_run(
        first,
        executor.resume(
            ResumeRequest(
                first,
                (ResumeInterruptedNodeRequest(GraphNodeId("a"), first_id, OverrideNodeInput("answer-one")),),
            )
        ),
    )

    second = await settle_pending(executor, resumed, "ignored")
    second_node = frontier_node(second.frontier, GraphNodeId("a"))
    assert second_node is not None and isinstance(second_node.settlement, InterruptedGraphNode)
    second_identity = second_node.settlement.interrupt.identity
    assert second_identity.execution_generation == first_identity.execution_generation + 1
    assert second_identity.superstep == first_identity.superstep
    with pytest.raises(Exception, match="does not match"):
        executor.resume(
            ResumeRequest(
                second,
                (ResumeInterruptedNodeRequest(GraphNodeId("a"), first_id, OverrideNodeInput("stale")),),
            )
        )


async def test_interrupt_resume_then_self_loop_starts_a_clean_activation() -> None:
    received: list[str] = []

    async def cycle(node_input: str) -> NodeOutcome[str]:
        received.append(node_input)
        if len(received) == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        route = "again" if len(received) == 2 else "done"
        return NodeSuccess(node_input, SelectGraphRoute(GraphRouteId(route)))

    codec = Codec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt-loop.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), cycle),),
            (
                ConditionalEdge(GraphNodeId("a"), GraphRouteId("again"), GraphNodeId("a")),
                ConditionalEdge(GraphNodeId("a"), GraphRouteId("done"), END),
            ),
            (GraphNodeId("a"),),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    interrupted = await settle_pending(executor, started(executor), "initial")
    interrupted_node = frontier_node(interrupted.frontier, GraphNodeId("a"))
    assert interrupted_node is not None and isinstance(interrupted_node.settlement, InterruptedGraphNode)
    identity = interrupted_node.settlement.interrupt.identity
    resumed = reduce_graph_run(
        interrupted,
        executor.resume(
            ResumeRequest(
                interrupted,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
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

    loop_settled = await settle_pending(executor, resumed, "ignored")
    looped = await resolve_ready(executor, loop_settled)
    assert looped.superstep == 1
    assert looped.frontier.nodes[0].settlement == PendingGraphNode(UseStepRequestInput())
    final_settled = await settle_pending(executor, looped, "fresh")
    completed = await resolve_ready(executor, final_settled)
    assert completed.status is GraphRunStatus.COMPLETED
    assert received == ["initial", "answer", "fresh"]


async def test_mixed_frontier_selectively_resumes_without_rerunning_success() -> None:
    calls = {"a": 0, "b": 0, "c": 0}

    async def success(node_input: str) -> NodeSuccess[str]:
        calls["a"] += 1
        return NodeSuccess(node_input)

    async def fail(node_input: str) -> NodeFailure:
        calls["b"] += 1
        return NodeFailure(GraphFailure(f"b failed:{node_input}"))

    async def interrupt_once(node_input: str) -> NodeOutcome[str]:
        calls["c"] += 1
        if calls["c"] == 1:
            return NodeInterrupt(GraphInterruptPayload(b"c question"))
        return NodeSuccess(node_input)

    codec = Codec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.mixed"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), success),
                NodeDefinition(GraphNodeId("b"), fail),
                NodeDefinition(GraphNodeId("c"), interrupt_once),
            ),
            tuple(DirectEdge(GraphNodeId(node), END) for node in ("a", "b", "c")),
            tuple(GraphNodeId(node) for node in ("a", "b", "c")),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    awaiting = await settle_pending(executor, started(executor), "ordinary")
    interrupted = frontier_node(awaiting.frontier, GraphNodeId("c"))
    assert interrupted is not None and isinstance(interrupted.settlement, InterruptedGraphNode)
    identity = interrupted.settlement.interrupt.identity
    resumed = reduce_graph_run(
        awaiting,
        executor.resume(
            ResumeRequest(
                awaiting,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("c"),
                        graph_interrupt_id(
                            identity.run_id,
                            identity.superstep,
                            identity.node_id,
                            identity.execution_generation,
                        ),
                        OverrideNodeInput("c answer"),
                    ),
                ),
            )
        ),
    )
    still_failed = await settle_pending(executor, resumed, "ordinary")
    assert calls == {"a": 1, "b": 1, "c": 2}
    skipped = reduce_graph_run(
        still_failed,
        executor.resume(
            ResumeRequest(
                still_failed,
                (SkipFailedNodeRequest(GraphNodeId("b"), GraphSkipReason("operator skip"), ContinueGraphRouting()),),
            )
        ),
    )
    completed = await resolve_ready(executor, skipped)
    assert completed.status is GraphRunStatus.COMPLETED
    assert calls == {"a": 1, "b": 1, "c": 2}


async def test_interrupt_resume_applies_retained_sibling_join_arrival_once() -> None:
    calls = {"a": 0, "b": 0, "joined": 0}

    async def source_a(node_input: str) -> NodeSuccess[str]:
        calls["a"] += 1
        return NodeSuccess(node_input)

    async def source_b(node_input: str) -> NodeOutcome[str]:
        calls["b"] += 1
        if calls["b"] == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        return NodeSuccess(node_input)

    async def joined(node_input: str) -> NodeSuccess[str]:
        calls["joined"] += 1
        return NodeSuccess(node_input)

    codec = Codec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt-join.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), source_a),
                NodeDefinition(GraphNodeId("b"), source_b),
                NodeDefinition(GraphNodeId("joined"), joined),
            ),
            (
                JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("joined")),
                DirectEdge(GraphNodeId("joined"), END),
            ),
            (GraphNodeId("a"), GraphNodeId("b")),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    awaiting = await settle_pending(executor, started(executor), "initial")
    interrupted = frontier_node(awaiting.frontier, GraphNodeId("b"))
    assert interrupted is not None and isinstance(interrupted.settlement, InterruptedGraphNode)
    identity = interrupted.settlement.interrupt.identity
    resumed = reduce_graph_run(
        awaiting,
        executor.resume(
            ResumeRequest(
                awaiting,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("b"),
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
    settled = await settle_pending(executor, resumed, "ignored")
    joined_frontier = await resolve_ready(executor, settled)
    assert tuple(node.node_id for node in joined_frontier.frontier.nodes) == (GraphNodeId("joined"),)
    assert joined_frontier.join_progress == ()
    joined_settled = await settle_pending(executor, joined_frontier, "joined-input")
    completed = await resolve_ready(executor, joined_settled)
    assert completed.status is GraphRunStatus.COMPLETED
    assert calls == {"a": 1, "b": 2, "joined": 1}


async def test_multiple_interrupts_can_be_resumed_together_by_exact_ids() -> None:
    calls = {"a": 0, "b": 0}
    received: dict[str, list[str]] = {"a": [], "b": []}

    def interrupt_once(name: str) -> Node[str, str]:
        async def node(node_input: str) -> NodeOutcome[str]:
            calls[name] += 1
            received[name].append(node_input)
            if calls[name] == 1:
                return NodeInterrupt(GraphInterruptPayload(f"{name}-question".encode()))
            return NodeSuccess(node_input)

        return node

    graph = interrupt_pair_graph(interrupt_once("a"), interrupt_once("b"))
    executor = GraphExecutor(graph)
    awaiting = await settle_pending(executor, started(executor), "ordinary")
    actions: list[ResumeInterruptedNodeRequest[str]] = []
    for node_id, answer in ((GraphNodeId("a"), "a-answer"), (GraphNodeId("b"), "b-answer")):
        node = frontier_node(awaiting.frontier, node_id)
        assert node is not None and isinstance(node.settlement, InterruptedGraphNode)
        identity = node.settlement.interrupt.identity
        actions.append(
            ResumeInterruptedNodeRequest(
                node_id,
                graph_interrupt_id(
                    identity.run_id,
                    identity.superstep,
                    identity.node_id,
                    identity.execution_generation,
                ),
                OverrideNodeInput(answer),
            )
        )
    resumed = reduce_graph_run(awaiting, executor.resume(ResumeRequest(awaiting, tuple(actions))))

    settled = await settle_pending(executor, resumed, "ignored")

    assert calls == {"a": 2, "b": 2}
    assert received == {"a": ["ordinary", "a-answer"], "b": ["ordinary", "b-answer"]}
    assert (await resolve_ready(executor, settled)).status is GraphRunStatus.COMPLETED


async def test_one_resume_request_atomically_resumes_skips_and_answers_nodes() -> None:
    calls = {"a": 0, "b": 0, "c": 0}

    async def resume_failure(node_input: str) -> NodeOutcome[str]:
        calls["a"] += 1
        if calls["a"] == 1:
            return NodeFailure(GraphFailure("a failed"))
        return NodeSuccess(node_input)

    async def skip_failure(node_input: str) -> NodeFailure:
        calls["b"] += 1
        return NodeFailure(GraphFailure(f"b failed:{node_input}"))

    async def answer_interrupt(node_input: str) -> NodeOutcome[str]:
        calls["c"] += 1
        if calls["c"] == 1:
            return NodeInterrupt(GraphInterruptPayload(b"c question"))
        return NodeSuccess(node_input)

    codec = Codec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.atomic-resume"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), resume_failure),
                NodeDefinition(GraphNodeId("b"), skip_failure),
                NodeDefinition(GraphNodeId("c"), answer_interrupt),
            ),
            tuple(DirectEdge(GraphNodeId(node), END) for node in ("a", "b", "c")),
            tuple(GraphNodeId(node) for node in ("a", "b", "c")),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    awaiting = await settle_pending(executor, started(executor), "initial")
    interrupted = frontier_node(awaiting.frontier, GraphNodeId("c"))
    assert interrupted is not None and isinstance(interrupted.settlement, InterruptedGraphNode)
    identity = interrupted.settlement.interrupt.identity
    resumed = reduce_graph_run(
        awaiting,
        executor.resume(
            ResumeRequest(
                awaiting,
                (
                    ResumeFailedNodeRequest(GraphNodeId("a"), UseRequestInput()),
                    SkipFailedNodeRequest(GraphNodeId("b"), GraphSkipReason("operator skip"), ContinueGraphRouting()),
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("c"),
                        graph_interrupt_id(
                            identity.run_id,
                            identity.superstep,
                            identity.node_id,
                            identity.execution_generation,
                        ),
                        OverrideNodeInput("c answer"),
                    ),
                ),
            )
        ),
    )
    resumed_a = frontier_node(resumed.frontier, GraphNodeId("a"))
    resumed_b = frontier_node(resumed.frontier, GraphNodeId("b"))
    resumed_c = frontier_node(resumed.frontier, GraphNodeId("c"))
    assert resumed_a is not None and isinstance(resumed_a.settlement, PendingGraphNode)
    assert resumed_b is not None
    assert resumed_c is not None and isinstance(resumed_c.settlement, PendingGraphNode)

    settled = await settle_pending(executor, resumed, "request-default")
    assert calls == {"a": 2, "b": 1, "c": 2}
    assert (await resolve_ready(executor, settled)).status is GraphRunStatus.COMPLETED


async def test_aborted_override_is_neither_decoded_nor_scheduled() -> None:
    calls = 0

    class ExplodingDecoder(Codec):
        def decode(self, payload: bytes) -> str:
            raise AssertionError(payload)

    async def node(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = interrupt_graph(node, codec=ExplodingDecoder())
    executor = GraphExecutor(graph)
    initial = started(executor)
    diagnostic = replace(
        initial,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("a"),
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"opaque"))),
                ),
            )
        ),
    )
    aborted = reduce_graph_run(diagnostic, AbortGraphRun(0, GraphAbortReason("stop")))

    disposition = await executor.prepare(
        StepRequest(aborted, "ordinary", ExecutionRequestAttemptId("aborted-request"), ())
    )

    assert isinstance(disposition, AbortedGraph)
    assert calls == 0
