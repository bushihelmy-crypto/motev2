import asyncio
from dataclasses import replace

import pytest
from tests.execution.driver import ATTEMPT_ID, ClaimedStep, apply_claimed, apply_command, execute_step, step_request

from mote_kernel.execution import (
    AbortedGraph,
    AwaitingResume,
    ExecutableFrontier,
    ExecutionRequestAttemptId,
    GraphExecutor,
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
    SkipFailedNodeRequest,
    StepRequest,
    TaskInterrupt,
    TaskSuccess,
    UseRequestInput,
)
from mote_kernel.execution.errors import ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.graph import (
    END,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
    JoinEdge,
    NodeDefinition,
    NodeFailure,
    NodeInterrupt,
    NodeSuccess,
    ResumeInputBinding,
    SelectGraphRoute,
    compile_graph,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ContinueGraphRouting,
    FailedGraphNode,
    FenceGraphExecution,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRunId,
    GraphRunStatus,
    GraphSkipReason,
    GraphStateTransitionError,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    UseStepRequestInput,
    frontier_node,
    graph_interrupt_id,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


class Utf8Codec:
    def encode(self, value: str) -> bytes:
        if not value or value.startswith("invalid"):
            raise ValueError("invalid resume input")
        return value.encode()

    def decode(self, payload: bytes) -> str:
        value = payload.decode()
        if not value or value.startswith("invalid"):
            raise ValueError("invalid resume payload")
        return value


def graph_for(*nodes: NodeDefinition[str, str], codec: Utf8Codec | None = None):
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("interrupt.graph"),
            GraphDefinitionVersion(1),
            nodes,
            tuple(DirectEdge(node.node_id, END) for node in nodes),
            tuple(node.node_id for node in nodes),
            resume_input=(
                ResumeInputBinding(GraphResumeInputCodecId("utf8.v1"), 1, codec, codec) if codec is not None else None
            ),
        )
    )


def started(executor: GraphExecutor[str, str]):
    return reduce_graph_run(None, executor.start_command(GraphRunId("run")))


async def test_node_interrupt_round_trip_uses_typed_override_and_state_owned_identity() -> None:
    received: list[str] = []

    async def interrupt_once(node_input: str):
        received.append(node_input)
        if len(received) == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        return NodeSuccess(node_input)

    graph = graph_for(NodeDefinition(GraphNodeId("a"), interrupt_once), codec=Utf8Codec())
    executor = GraphExecutor(graph)
    initial = started(executor)
    first = await execute_step(step_request(graph, initial, "ordinary"))
    assert isinstance(first, ClaimedStep)
    assert isinstance(first.result.results[0], TaskInterrupt)
    interrupted = apply_claimed(first)
    node = frontier_node(interrupted.frontier, GraphNodeId("a"))
    assert node is not None and isinstance(node.settlement, InterruptedGraphNode)
    identity = node.settlement.interrupt.identity
    interrupt_id = graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )
    assert await executor.prepare(StepRequest(interrupted, "ignored", ATTEMPT_ID, ())) == AwaitingResume(
        (), (GraphNodeId("a"),)
    )

    command = executor.resume(
        ResumeRequest(
            interrupted,
            (
                ResumeInterruptedNodeRequest(
                    GraphNodeId("a"),
                    interrupt_id,
                    OverrideNodeInput("approved"),
                ),
            ),
        )
    )
    resumed = apply_command(interrupted, command)
    pending = frontier_node(resumed.frontier, GraphNodeId("a"))
    assert pending is not None
    assert pending.settlement == PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"approved")))

    second = await execute_step(step_request(graph, resumed, "not-used"))
    assert isinstance(second, ClaimedStep)
    assert received == ["ordinary", "approved"]
    assert apply_claimed(second).status is GraphRunStatus.COMPLETED


async def test_interrupt_request_and_resume_payloads_remain_distinct() -> None:
    async def interrupt(node_input: str):
        del node_input
        return NodeInterrupt(GraphInterruptPayload(b"request-question"))

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), interrupt),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    first = await execute_step(step_request(graph, started(executor), "ordinary"))
    assert isinstance(first, ClaimedStep)
    interrupted = apply_claimed(first)
    node = frontier_node(interrupted.frontier, GraphNodeId("a"))
    assert node is not None and isinstance(node.settlement, InterruptedGraphNode)
    identity = node.settlement.interrupt.identity
    interrupt_id = graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )

    command = executor.resume(
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
    )
    resumed = apply_command(interrupted, command)
    pending = frontier_node(resumed.frontier, GraphNodeId("a"))

    assert node.settlement.interrupt.request_payload == GraphInterruptPayload(b"request-question")
    assert pending is not None
    assert pending.settlement == PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"resume-answer")))
    assert isinstance(pending.settlement, PendingGraphNode)
    assert isinstance(pending.settlement.input, OverrideGraphNodeInput)
    assert bytes(node.settlement.interrupt.request_payload) != bytes(pending.settlement.input.payload)


async def test_repeated_interrupt_uses_new_generation_and_consumes_old_identity() -> None:
    async def interrupt(node_input: str) -> NodeInterrupt:
        del node_input
        return NodeInterrupt(GraphInterruptPayload(b"again"))

    graph = graph_for(NodeDefinition(GraphNodeId("a"), interrupt), codec=Utf8Codec())
    executor = GraphExecutor(graph)
    first_step = await execute_step(step_request(graph, started(executor), "ordinary"))
    assert isinstance(first_step, ClaimedStep)
    first = apply_claimed(first_step)
    first_node = frontier_node(first.frontier, GraphNodeId("a"))
    assert first_node is not None and isinstance(first_node.settlement, InterruptedGraphNode)
    first_identity = first_node.settlement.interrupt.identity
    first_id = graph_interrupt_id(
        first_identity.run_id,
        first_identity.superstep,
        first_identity.node_id,
        first_identity.execution_generation,
    )
    resumed = apply_command(
        first,
        executor.resume(
            ResumeRequest(
                first,
                (ResumeInterruptedNodeRequest(GraphNodeId("a"), first_id, OverrideNodeInput("answer-one")),),
            )
        ),
    )
    second_step = await execute_step(step_request(graph, resumed, "ignored"))
    assert isinstance(second_step, ClaimedStep)
    second = apply_claimed(second_step)
    second_node = frontier_node(second.frontier, GraphNodeId("a"))
    assert second_node is not None and isinstance(second_node.settlement, InterruptedGraphNode)
    second_identity = second_node.settlement.interrupt.identity
    assert second_identity.execution_generation == first_identity.execution_generation + 1
    assert second_identity.superstep == first_identity.superstep
    with pytest.raises(SnapshotMismatchError, match="does not match"):
        executor.resume(
            ResumeRequest(
                second,
                (ResumeInterruptedNodeRequest(GraphNodeId("a"), first_id, OverrideNodeInput("stale")),),
            )
        )


async def test_interrupt_resume_then_self_loop_starts_a_clean_activation() -> None:
    received: list[str] = []

    async def cycle(node_input: str):
        received.append(node_input)
        if len(received) == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        route = "again" if len(received) == 2 else "done"
        return NodeSuccess(node_input, SelectGraphRoute(GraphRouteId(route)))

    codec = Utf8Codec()
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
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("utf8.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    first_step = await execute_step(step_request(graph, started(executor), "initial"))
    assert isinstance(first_step, ClaimedStep)
    interrupted = apply_claimed(first_step)
    interrupted_node = frontier_node(interrupted.frontier, GraphNodeId("a"))
    assert interrupted_node is not None and isinstance(interrupted_node.settlement, InterruptedGraphNode)
    identity = interrupted_node.settlement.interrupt.identity
    resumed = apply_command(
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

    loop_step = await execute_step(step_request(graph, resumed, "ignored"))
    assert isinstance(loop_step, ClaimedStep)
    looped = apply_claimed(loop_step)
    next_activation = frontier_node(looped.frontier, GraphNodeId("a"))
    assert looped.superstep == 1
    assert next_activation is not None
    assert next_activation.settlement == PendingGraphNode(UseStepRequestInput())

    final_step = await execute_step(step_request(graph, looped, "fresh"))
    assert isinstance(final_step, ClaimedStep)
    assert apply_claimed(final_step).status is GraphRunStatus.COMPLETED
    assert received == ["initial", "answer", "fresh"]


async def test_mixed_frontier_selectively_resumes_nodes_without_rerunning_success() -> None:
    calls = {"a": 0, "b": 0, "c": 0}

    async def success(node_input: str) -> NodeSuccess[str]:
        calls["a"] += 1
        return NodeSuccess(node_input)

    async def fail(node_input: str) -> NodeFailure:
        calls["b"] += 1
        if calls["b"] == 1:
            return NodeFailure(GraphFailure("b failed"))
        return NodeFailure(GraphFailure(f"b still failed:{node_input}"))

    async def interrupt(node_input: str):
        calls["c"] += 1
        if calls["c"] == 1:
            return NodeInterrupt(GraphInterruptPayload(b"c question"))
        return NodeSuccess(node_input)

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), success),
        NodeDefinition(GraphNodeId("b"), fail),
        NodeDefinition(GraphNodeId("c"), interrupt),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    first_step = await execute_step(step_request(graph, started(executor), "ordinary"))
    assert isinstance(first_step, ClaimedStep)
    awaiting = apply_claimed(first_step)
    c_node = frontier_node(awaiting.frontier, GraphNodeId("c"))
    assert c_node is not None and isinstance(c_node.settlement, InterruptedGraphNode)
    c_identity = c_node.settlement.interrupt.identity
    c_id = graph_interrupt_id(
        c_identity.run_id,
        c_identity.superstep,
        c_identity.node_id,
        c_identity.execution_generation,
    )
    resumed_c = apply_command(
        awaiting,
        executor.resume(
            ResumeRequest(
                awaiting,
                (ResumeInterruptedNodeRequest(GraphNodeId("c"), c_id, OverrideNodeInput("c answer")),),
            )
        ),
    )
    second_step = await execute_step(step_request(graph, resumed_c, "ordinary"))
    assert isinstance(second_step, ClaimedStep)
    still_failed = apply_claimed(second_step)
    assert calls == {"a": 1, "b": 1, "c": 2}
    b_node = frontier_node(still_failed.frontier, GraphNodeId("b"))
    assert b_node is not None and isinstance(b_node.settlement, FailedGraphNode)

    completed = apply_command(
        still_failed,
        executor.resume(
            ResumeRequest(
                still_failed,
                (SkipFailedNodeRequest(GraphNodeId("b"), GraphSkipReason("operator skip"), ContinueGraphRouting()),),
            )
        ),
    )
    assert completed.status is GraphRunStatus.COMPLETED
    assert calls == {"a": 1, "b": 1, "c": 2}


async def test_typed_interrupt_waits_for_slow_sibling_and_settles_both() -> None:
    interrupted = asyncio.Event()
    completed: list[str] = []

    async def slow(node_input: str) -> NodeSuccess[str]:
        await interrupted.wait()
        await asyncio.sleep(0)
        completed.append("a")
        return NodeSuccess(node_input)

    async def interrupt(node_input: str) -> NodeInterrupt:
        del node_input
        completed.append("b")
        interrupted.set()
        return NodeInterrupt(GraphInterruptPayload(b"question"))

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), slow),
        NodeDefinition(GraphNodeId("b"), interrupt),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)

    step = await execute_step(step_request(graph, started(executor), "input"))

    assert isinstance(step, ClaimedStep)
    assert completed == ["b", "a"]
    settled = apply_claimed(step)
    assert settled.execution is None
    assert await executor.prepare(StepRequest(settled, "ignored", ATTEMPT_ID, ())) == AwaitingResume(
        (),
        (GraphNodeId("b"),),
    )


async def test_interrupt_resume_applies_retained_sibling_arrival_to_join_once() -> None:
    calls = {"a": 0, "b": 0, "joined": 0}

    async def source_a(node_input: str) -> NodeSuccess[str]:
        calls["a"] += 1
        return NodeSuccess(node_input)

    async def source_b(node_input: str):
        calls["b"] += 1
        if calls["b"] == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        return NodeSuccess(node_input)

    async def joined(node_input: str) -> NodeSuccess[str]:
        calls["joined"] += 1
        return NodeSuccess(node_input)

    codec = Utf8Codec()
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
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("utf8.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    first = await execute_step(step_request(graph, started(executor), "initial"))
    assert isinstance(first, ClaimedStep)
    awaiting = apply_claimed(first)
    interrupted_node = frontier_node(awaiting.frontier, GraphNodeId("b"))
    assert interrupted_node is not None and isinstance(interrupted_node.settlement, InterruptedGraphNode)
    identity = interrupted_node.settlement.interrupt.identity
    resumed = apply_command(
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

    second = await execute_step(step_request(graph, resumed, "ignored"))
    assert isinstance(second, ClaimedStep)
    joined_frontier = apply_claimed(second)
    assert tuple(node.node_id for node in joined_frontier.frontier.nodes) == (GraphNodeId("joined"),)
    assert joined_frontier.join_progress == ()
    third = await execute_step(step_request(graph, joined_frontier, "joined-input"))
    assert isinstance(third, ClaimedStep)
    assert apply_claimed(third).status is GraphRunStatus.COMPLETED
    assert calls == {"a": 1, "b": 2, "joined": 1}


async def test_multiple_interrupts_can_be_resumed_by_exact_id_one_at_a_time() -> None:
    calls = {"a": 0, "b": 0}

    def interrupt_once(name: str):
        async def execute(node_input: str):
            calls[name] += 1
            if calls[name] == 1:
                return NodeInterrupt(GraphInterruptPayload(f"{name}-question".encode()))
            return NodeSuccess(node_input)

        return execute

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), interrupt_once("a")),
        NodeDefinition(GraphNodeId("b"), interrupt_once("b")),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    first = await execute_step(step_request(graph, started(executor), "ordinary"))
    assert isinstance(first, ClaimedStep)
    awaiting = apply_claimed(first)
    a_node = frontier_node(awaiting.frontier, GraphNodeId("a"))
    b_node = frontier_node(awaiting.frontier, GraphNodeId("b"))
    assert a_node is not None and isinstance(a_node.settlement, InterruptedGraphNode)
    assert b_node is not None and isinstance(b_node.settlement, InterruptedGraphNode)
    a_identity = a_node.settlement.interrupt.identity
    b_identity = b_node.settlement.interrupt.identity
    a_id = graph_interrupt_id(
        a_identity.run_id,
        a_identity.superstep,
        a_identity.node_id,
        a_identity.execution_generation,
    )
    b_id = graph_interrupt_id(
        b_identity.run_id,
        b_identity.superstep,
        b_identity.node_id,
        b_identity.execution_generation,
    )

    resumed_a = apply_command(
        awaiting,
        executor.resume(
            ResumeRequest(
                awaiting,
                (ResumeInterruptedNodeRequest(GraphNodeId("a"), a_id, OverrideNodeInput("a-answer")),),
            )
        ),
    )
    second = await execute_step(step_request(graph, resumed_a, "ignored"))
    assert isinstance(second, ClaimedStep)
    still_waiting = apply_claimed(second)
    assert calls == {"a": 2, "b": 1}
    b_current = frontier_node(still_waiting.frontier, GraphNodeId("b"))
    assert b_current is not None and isinstance(b_current.settlement, InterruptedGraphNode)

    resumed_b = apply_command(
        still_waiting,
        executor.resume(
            ResumeRequest(
                still_waiting,
                (ResumeInterruptedNodeRequest(GraphNodeId("b"), b_id, OverrideNodeInput("b-answer")),),
            )
        ),
    )
    final = await execute_step(step_request(graph, resumed_b, "ignored"))
    assert isinstance(final, ClaimedStep)
    assert calls == {"a": 2, "b": 2}
    assert apply_claimed(final).status is GraphRunStatus.COMPLETED


async def test_multiple_interrupts_can_be_resumed_together_by_exact_ids() -> None:
    calls = {"a": 0, "b": 0}

    def interrupt_once(name: str):
        async def execute(node_input: str):
            calls[name] += 1
            if calls[name] == 1:
                return NodeInterrupt(GraphInterruptPayload(f"{name}-question".encode()))
            return NodeSuccess(node_input)

        return execute

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), interrupt_once("a")),
        NodeDefinition(GraphNodeId("b"), interrupt_once("b")),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    first = await execute_step(step_request(graph, started(executor), "ordinary"))
    assert isinstance(first, ClaimedStep)
    awaiting = apply_claimed(first)
    requested: list[ResumeInterruptedNodeRequest[str]] = []
    for node_id, answer in ((GraphNodeId("a"), "a-answer"), (GraphNodeId("b"), "b-answer")):
        node = frontier_node(awaiting.frontier, node_id)
        assert node is not None and isinstance(node.settlement, InterruptedGraphNode)
        identity = node.settlement.interrupt.identity
        requested.append(
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
    resumed = apply_command(awaiting, executor.resume(ResumeRequest(awaiting, tuple(requested))))

    second = await execute_step(step_request(graph, resumed, "ignored"))

    assert isinstance(second, ClaimedStep)
    assert tuple(result.output for result in second.result.results if isinstance(result, TaskSuccess)) == (
        "a-answer",
        "b-answer",
    )
    assert calls == {"a": 2, "b": 2}
    assert apply_claimed(second).status is GraphRunStatus.COMPLETED


async def test_failure_resume_delivers_distinct_default_and_override_inputs_in_one_batch() -> None:
    received: dict[str, list[str]] = {"a": [], "b": []}

    def fail_once(name: str):
        async def execute(node_input: str):
            received[name].append(node_input)
            if len(received[name]) == 1:
                return NodeFailure(GraphFailure(f"{name} failed"))
            return NodeSuccess(node_input)

        return execute

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), fail_once("a")),
        NodeDefinition(GraphNodeId("b"), fail_once("b")),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    first = await execute_step(step_request(graph, started(executor), "initial"))
    assert isinstance(first, ClaimedStep)
    failed = apply_claimed(first)
    resumed = apply_command(
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
    second = await execute_step(step_request(graph, resumed, "request-default"))
    assert isinstance(second, ClaimedStep)
    assert received == {"a": ["initial", "request-default"], "b": ["initial", "override-b"]}
    assert apply_claimed(second).status is GraphRunStatus.COMPLETED


async def test_one_public_resume_request_atomically_resumes_skips_and_answers_nodes() -> None:
    calls = {"a": 0, "b": 0, "c": 0}
    received: dict[str, list[str]] = {"a": [], "b": [], "c": []}

    async def resume_failure(node_input: str):
        calls["a"] += 1
        received["a"].append(node_input)
        if calls["a"] == 1:
            return NodeFailure(GraphFailure("a failed"))
        return NodeSuccess(node_input)

    async def skip_failure(node_input: str) -> NodeFailure:
        calls["b"] += 1
        received["b"].append(node_input)
        return NodeFailure(GraphFailure("b failed"))

    async def answer_interrupt(node_input: str):
        calls["c"] += 1
        received["c"].append(node_input)
        if calls["c"] == 1:
            return NodeInterrupt(GraphInterruptPayload(b"c question"))
        return NodeSuccess(node_input)

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), resume_failure),
        NodeDefinition(GraphNodeId("b"), skip_failure),
        NodeDefinition(GraphNodeId("c"), answer_interrupt),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    first = await execute_step(step_request(graph, started(executor), "initial"))
    assert isinstance(first, ClaimedStep)
    awaiting = apply_claimed(first)
    interrupted = frontier_node(awaiting.frontier, GraphNodeId("c"))
    assert interrupted is not None and isinstance(interrupted.settlement, InterruptedGraphNode)
    identity = interrupted.settlement.interrupt.identity

    command = executor.resume(
        ResumeRequest(
            awaiting,
            (
                ResumeFailedNodeRequest(GraphNodeId("a"), UseRequestInput()),
                SkipFailedNodeRequest(
                    GraphNodeId("b"),
                    GraphSkipReason("operator skip"),
                    ContinueGraphRouting(),
                ),
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
    )
    resumed = apply_command(awaiting, command)

    second = await execute_step(step_request(graph, resumed, "request-default"))

    assert isinstance(second, ClaimedStep)
    assert calls == {"a": 2, "b": 1, "c": 2}
    assert received == {
        "a": ["initial", "request-default"],
        "b": ["initial"],
        "c": ["initial", "c answer"],
    }
    assert apply_claimed(second).status is GraphRunStatus.COMPLETED


async def test_encode_and_preclaim_decode_errors_create_no_command_or_lease() -> None:
    async def fail(node_input: str) -> NodeFailure:
        return NodeFailure(GraphFailure(node_input))

    graph = graph_for(NodeDefinition(GraphNodeId("a"), fail), codec=Utf8Codec())
    executor = GraphExecutor(graph)
    first = await execute_step(step_request(graph, started(executor), "initial"))
    assert isinstance(first, ClaimedStep)
    failed = apply_claimed(first)
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
        await executor.prepare(StepRequest(malformed, "ordinary", ATTEMPT_ID, ()))
    assert malformed.execution is None


async def test_claim_postdecode_error_keeps_lease_and_override_for_fence() -> None:
    received: list[str] = []

    async def record(node_input: str) -> NodeSuccess[str]:
        received.append(node_input)
        return NodeSuccess(node_input)

    graph = graph_for(NodeDefinition(GraphNodeId("a"), record), codec=Utf8Codec())
    executor = GraphExecutor(graph)
    initial = started(executor)
    initial = replace(
        initial,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("a"),
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"valid"))),
                ),
            )
        ),
    )
    prepared = await executor.prepare(StepRequest(initial, "ordinary", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)
    malformed = replace(
        claimed,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("a"),
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"invalid-post-claim"))),
                ),
            )
        ),
    )
    with pytest.raises(ValueError, match="invalid resume payload"):
        await executor.execute(prepared.claim, StepRequest(malformed, "ordinary", ATTEMPT_ID, ()))
    assert malformed.execution is not None
    assert received == []
    fenced = apply_command(
        malformed,
        FenceGraphExecution(malformed.revision, malformed.execution.token),
    )
    node = frontier_node(fenced.frontier, GraphNodeId("a"))
    assert node is not None
    assert node.settlement == PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"invalid-post-claim")))


async def test_claim_postguard_codec_mismatch_keeps_exact_lease() -> None:
    async def record(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), record),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "ordinary", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)
    mismatched = replace(
        claimed,
        resume_input_codec=GraphResumeInputCodec(
            GraphResumeInputCodecId("other.codec"),
            1,
        ),
    )

    with pytest.raises(SnapshotMismatchError, match="codec"):
        await executor.execute(
            prepared.claim,
            StepRequest(mismatched, "ordinary", ATTEMPT_ID, ()),
        )

    assert mismatched.execution is claimed.execution
    assert not prepared.claim.consumed


async def test_interrupt_override_is_redelivered_after_exception_and_fence_until_settlement() -> None:
    received: list[str] = []

    async def interrupt_then_fail_once(node_input: str):
        received.append(node_input)
        if len(received) == 1:
            return NodeInterrupt(GraphInterruptPayload(b"question"))
        if len(received) == 2:
            raise RuntimeError("worker stopped before settlement")
        return NodeSuccess(node_input)

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), interrupt_then_fail_once),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    interrupted_step = await execute_step(step_request(graph, initial, "initial"))
    assert isinstance(interrupted_step, ClaimedStep)
    interrupted = apply_claimed(interrupted_step)
    interrupted_node = frontier_node(interrupted.frontier, GraphNodeId("a"))
    assert interrupted_node is not None and isinstance(interrupted_node.settlement, InterruptedGraphNode)
    identity = interrupted_node.settlement.interrupt.identity
    resumed = apply_command(
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
    pending = frontier_node(resumed.frontier, GraphNodeId("a"))
    assert pending is not None
    assert pending.settlement == PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"approved")))
    first = await executor.prepare(StepRequest(resumed, "ordinary", ATTEMPT_ID, ()))
    assert isinstance(first, ExecutableFrontier) and first.claim is not None
    first_claimed = apply_command(resumed, first.claim.command)

    with pytest.raises(RuntimeError, match="before settlement"):
        await executor.execute(first.claim, StepRequest(first_claimed, "ordinary", ATTEMPT_ID, ()))
    assert first_claimed.execution is not None
    fenced = apply_command(
        first_claimed,
        FenceGraphExecution(first_claimed.revision, first_claimed.execution.token),
    )
    pending = frontier_node(fenced.frontier, GraphNodeId("a"))
    assert pending is not None
    assert pending.settlement == PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"approved")))

    retry = await executor.prepare(StepRequest(fenced, "different", ATTEMPT_ID, ()))
    assert isinstance(retry, ExecutableFrontier) and retry.claim is not None
    retry_claimed = apply_command(fenced, retry.claim.command)
    result = await executor.execute(retry.claim, StepRequest(retry_claimed, "different", ATTEMPT_ID, ()))
    completed = apply_command(retry_claimed, result.command)

    assert received == ["initial", "approved", "approved"]
    assert completed.status is GraphRunStatus.COMPLETED


async def test_aborted_override_is_neither_decoded_nor_scheduled() -> None:
    calls = 0

    class ExplodingDecoder(Utf8Codec):
        def decode(self, payload: bytes) -> str:
            raise AssertionError(payload)

    async def execute(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = graph_for(NodeDefinition(GraphNodeId("a"), execute), codec=ExplodingDecoder())
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
    assert isinstance(await executor.prepare(StepRequest(aborted, "ordinary", ATTEMPT_ID, ())), AbortedGraph)
    assert calls == 0


async def test_resume_service_rejects_noncanonical_wrong_type_and_nonquiescent_actions() -> None:
    async def fail(node_input: str) -> NodeFailure:
        return NodeFailure(GraphFailure(node_input))

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), fail),
        NodeDefinition(GraphNodeId("b"), fail),
        codec=Utf8Codec(),
    )
    executor = GraphExecutor(graph)
    first = await execute_step(step_request(graph, started(executor), "failed"))
    assert isinstance(first, ClaimedStep)
    failed = apply_claimed(first)
    with pytest.raises(SnapshotMismatchError, match="canonical"):
        executor.resume(
            ResumeRequest(
                failed,
                (
                    ResumeFailedNodeRequest(GraphNodeId("b"), UseRequestInput()),
                    ResumeFailedNodeRequest(GraphNodeId("a"), UseRequestInput()),
                ),
            )
        )
    with pytest.raises(SnapshotMismatchError, match="interrupted"):
        executor.resume(
            ResumeRequest(
                failed,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        graph_interrupt_id(failed.run_id, 0, GraphNodeId("a"), 1),
                        OverrideNodeInput("answer"),
                    ),
                ),
            )
        )

    resume_command = executor.resume(
        ResumeRequest(
            failed,
            (ResumeFailedNodeRequest(GraphNodeId("a"), UseRequestInput()),),
        )
    )
    partially_resumed = apply_command(failed, resume_command)
    prepared = await executor.prepare(StepRequest(partially_resumed, "retry", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(partially_resumed, prepared.claim.command)
    with pytest.raises(SnapshotMismatchError, match="quiescent"):
        executor.resume(
            ResumeRequest(
                claimed,
                (ResumeFailedNodeRequest(GraphNodeId("b"), UseRequestInput()),),
            )
        )

    admitted = replace(failed, resources=ResourceSnapshot((ResourceLock(ResourceId("reserved")),)))
    with pytest.raises(GraphStateTransitionError, match="current pending nodes"):
        executor.resume(
            ResumeRequest(
                admitted,
                (ResumeFailedNodeRequest(GraphNodeId("a"), UseRequestInput()),),
            )
        )


async def test_prepared_claim_is_owned_by_exact_executor_instance() -> None:
    graph = graph_for(NodeDefinition(GraphNodeId("a"), _success))
    owner = GraphExecutor(graph)
    other = GraphExecutor(graph)
    initial = started(owner)
    prepared = await owner.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)

    with pytest.raises(SnapshotMismatchError, match="not owned"):
        foreign = replace(claimed, definition_id=type(claimed.definition_id)("foreign"))
        await other.execute(prepared.claim, StepRequest(foreign, "input", ATTEMPT_ID, ()))

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await other.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    assert not prepared.claim.consumed


async def test_claim_validation_uses_canonical_node_order_for_different_lengths() -> None:
    graph = graph_for(
        NodeDefinition(GraphNodeId("aa"), _success),
        NodeDefinition(GraphNodeId("z"), _success),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    assert prepared.claim.command.node_ids == (GraphNodeId("aa"), GraphNodeId("z"))
    claimed = apply_command(initial, prepared.claim.command)

    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))

    assert tuple(item.task.node_id for item in result.results) == (GraphNodeId("aa"), GraphNodeId("z"))


async def _success(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


async def test_prepared_claim_is_bound_to_request_attempt_identity() -> None:
    calls = 0

    async def record(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = graph_for(NodeDefinition(GraphNodeId("a"), record))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(
            prepared.claim,
            StepRequest(claimed, "input", ExecutionRequestAttemptId("other-request"), ()),
        )
    assert not prepared.claim.consumed
    assert calls == 0

    result = await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    assert result.results
    assert calls == 1


async def test_fenced_unstarted_claim_cannot_execute_or_consume() -> None:
    calls = 0

    async def record(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = graph_for(NodeDefinition(GraphNodeId("a"), record))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)
    assert claimed.execution is not None
    fenced = apply_command(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(prepared.claim, StepRequest(fenced, "input", ATTEMPT_ID, ()))
    assert not prepared.claim.consumed
    assert calls == 0


async def test_late_settlement_cannot_overwrite_reclaimed_generation() -> None:
    graph = graph_for(NodeDefinition(GraphNodeId("a"), _success))
    executor = GraphExecutor(graph)
    initial = started(executor)
    first = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(first, ExecutableFrontier) and first.claim is not None
    first_state = apply_command(initial, first.claim.command)
    first_result = await executor.execute(first.claim, StepRequest(first_state, "input", ATTEMPT_ID, ()))
    assert first_state.execution is not None
    fenced = apply_command(first_state, FenceGraphExecution(first_state.revision, first_state.execution.token))
    second = await executor.prepare(StepRequest(fenced, "input", ATTEMPT_ID, ()))
    assert isinstance(second, ExecutableFrontier) and second.claim is not None
    second_state = apply_command(fenced, second.claim.command)

    with pytest.raises(ValueError, match="stale revision"):
        apply_command(second_state, first_result.command)
    assert second_state.execution is not None
    assert second_state.execution.token.generation == 2


async def test_cancelled_claim_retains_exact_lease_for_fencing_and_reclaim() -> None:
    entered = asyncio.Event()
    blocked = asyncio.Event()

    async def wait(node_input: str) -> NodeSuccess[str]:
        entered.set()
        await blocked.wait()
        return NodeSuccess(node_input)

    graph = graph_for(NodeDefinition(GraphNodeId("a"), wait))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)
    running = asyncio.create_task(executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ())))
    await asyncio.wait_for(entered.wait(), timeout=2)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert prepared.claim.consumed
    assert claimed.execution is not None
    fenced = apply_command(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    retry = await executor.prepare(StepRequest(fenced, "retry", ATTEMPT_ID, ()))
    assert isinstance(retry, ExecutableFrontier) and retry.claim is not None
    retried = apply_command(fenced, retry.claim.command)
    assert retried.execution is not None
    assert retried.execution.token.generation == 2


async def test_node_initiated_cancellation_waits_for_sibling_quiescence() -> None:
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

    graph = graph_for(
        NodeDefinition(GraphNodeId("a"), finish),
        NodeDefinition(GraphNodeId("b"), cancel),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(prepared.claim, StepRequest(claimed, "input", ATTEMPT_ID, ()))
    assert completed == ["b", "a"]
    assert claimed.execution is not None


async def test_claim_guard_rejects_forged_committed_attempt_token() -> None:
    graph = graph_for(NodeDefinition(GraphNodeId("a"), _success))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", ATTEMPT_ID, ()))
    assert isinstance(prepared, ExecutableFrontier) and prepared.claim is not None
    claimed = apply_command(initial, prepared.claim.command)
    assert claimed.execution is not None
    forged = replace(
        claimed,
        execution=replace(
            claimed.execution,
            token=replace(claimed.execution.token, attempt_id=GraphExecutionAttemptId("forged")),
        ),
    )

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(prepared.claim, StepRequest(forged, "input", ATTEMPT_ID, ()))
    assert not prepared.claim.consumed
