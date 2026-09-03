import asyncio

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.node import NodeCallable
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    GraphAbortReason,
    GraphNodeId,
    GraphResumeInputPayload,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    frontier_node,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


class Codec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


class ValidatingCodec(Codec):
    def encode(self, value: Graph.Values[str]) -> bytes:
        if value["value"].startswith("invalid"):
            raise ValueError("invalid resume input")
        return super().encode(value)

    def decode(self, payload: bytes) -> Graph.Values[str]:
        value = super().decode(payload)
        if value["value"].startswith("invalid"):
            raise ValueError("invalid resume payload")
        return value


class CountingCodec(Codec):
    def __init__(self) -> None:
        self.decode_calls = 0

    def decode(self, payload: bytes) -> Graph.Values[str]:
        self.decode_calls += 1
        return super().decode(payload)


class CommitLog:
    def __init__(self) -> None:
        self.transitions: list[Graph.Transition[str]] = []

    async def __call__(self, transition: Graph.Transition[str], /) -> Graph.State:
        self.transitions.append(transition)
        return transition.candidate_state


def interrupt_graph(
    operation: NodeCallable[str],
    *,
    codec: Codec | None = None,
    publish_output: bool = False,
) -> Graph[str]:
    effective_codec = Codec() if codec is None else codec
    graph = Graph[str]("interrupt.graph")
    graph.set_resume_codec(
        "input.v1",
        1,
        effective_codec.encode,
        effective_codec.decode,
    )
    graph.add_node(
        "a",
        operation,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.set_outputs({"value": Graph.node_output("a", "value")} if publish_output else {})
    return graph


def pair_graph(
    first: NodeCallable[str],
    second: NodeCallable[str],
    *,
    codec: Codec | None = None,
) -> Graph[str]:
    effective_codec = Codec() if codec is None else codec
    graph = Graph[str]("interrupt.pair")
    graph.set_resume_codec(
        "input.v1",
        1,
        effective_codec.encode,
        effective_codec.decode,
    )
    for node_id, operation in (("a", first), ("b", second)):
        graph.add_node(
            node_id,
            operation,
            inputs={"value": Graph.graph_input("value", str)},
            outputs={"value": str},
        )
    graph.set_outputs({})
    return graph


async def test_interrupt_is_a_node_completion_and_creates_awaiting_resume_state() -> None:
    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"question")

    graph = interrupt_graph(interrupt)
    result = await graph.run(Graph.values(value="input"), run_id="run")

    assert isinstance(result, Graph.AwaitingResumeResult)
    assert len(result.interrupts) == 1
    assert result.interrupts[0].node_id == "a"
    assert result.interrupts[0].request_payload == b"question"


async def test_interrupt_identity_is_coordinate_scoped_and_stale_ids_fail_closed() -> None:
    calls = 0

    async def interrupt_twice(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return Graph.interrupt(f"question-{calls}".encode())
        return values

    graph = interrupt_graph(interrupt_twice)
    first = await graph.run(Graph.values(value="input"), run_id="run")
    assert isinstance(first, Graph.AwaitingResumeResult)
    first_id = first.interrupts[0].interrupt_id
    second = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                first_id,
                Graph.values(value="first-answer"),
            ),
        ),
    )
    assert isinstance(second, Graph.AwaitingResumeResult)
    second_id = second.interrupts[0].interrupt_id
    assert second_id != first_id

    with pytest.raises(Graph.SnapshotMismatchError, match="does not match"):
        await graph.run(
            state=second.state,
            continuation=second.continuation,
            resume=(
                graph.resume_interrupted(
                    "a",
                    first_id,
                    Graph.values(value="stale"),
                ),
            ),
        )


async def test_resume_reuses_same_activation_coordinates_with_new_execution_generation() -> None:
    calls = 0

    async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Graph.interrupt(b"question")
        return values

    graph = interrupt_graph(interrupt_once, publish_output=True)
    first = await graph.run(Graph.values(value="input"), run_id="same-run")
    assert isinstance(first, Graph.AwaitingResumeResult)
    resumed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                first.interrupts[0].interrupt_id,
                Graph.values(value="answer"),
            ),
        ),
    )

    assert isinstance(resumed, Graph.CompletedResult)
    assert resumed.state.run_id == first.state.run_id
    assert resumed.outputs["value"] == "answer"
    assert calls == 2


async def test_interrupt_result_payload_remains_opaque_bytes() -> None:
    payload = b"\x00\xffopaque\x00"

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(payload)

    graph = interrupt_graph(interrupt)
    result = await graph.run(Graph.values(value="input"))

    assert isinstance(result, Graph.AwaitingResumeResult)
    assert result.interrupts[0].request_payload == payload
    assert type(result.interrupts[0].request_payload) is bytes


async def test_interrupt_waits_for_a_started_sibling_to_reach_quiescence() -> None:
    sibling_started = asyncio.Event()
    release_sibling = asyncio.Event()

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"question")

    async def sibling(values: Graph.Values[str]) -> Graph.Values[str]:
        sibling_started.set()
        await release_sibling.wait()
        return values

    graph = pair_graph(interrupt, sibling)
    running = asyncio.create_task(graph.run(Graph.values(value="input")))
    await sibling_started.wait()
    await asyncio.sleep(0)
    assert not running.done()
    release_sibling.set()
    result = await running

    assert isinstance(result, Graph.AwaitingResumeResult)
    assert tuple(view.node_id for view in result.interrupts) == ("a",)


async def test_interrupt_completion_does_not_wait_for_a_slow_sibling() -> None:
    sibling_started = asyncio.Event()
    release_sibling = asyncio.Event()
    interrupt_committed = asyncio.Event()

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"question")

    async def sibling(values: Graph.Values[str]) -> Graph.Values[str]:
        sibling_started.set()
        await release_sibling.wait()
        return values

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        if isinstance(transition.result, Graph.InterruptResult):
            interrupt_committed.set()
        return transition.candidate_state

    graph = pair_graph(interrupt, sibling)
    running = asyncio.create_task(graph.run(Graph.values(value="input"), commit=commit))
    await sibling_started.wait()
    await asyncio.wait_for(interrupt_committed.wait(), timeout=1)
    assert not running.done()
    release_sibling.set()
    result = await running

    assert isinstance(result, Graph.AwaitingResumeResult)
    assert tuple(view.node_id for view in result.interrupts) == ("a",)


async def test_multiple_interrupts_can_be_resumed_one_at_a_time_by_exact_identity() -> None:
    calls = {"a": 0, "b": 0}

    def operation(node_id: str):
        async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
            calls[node_id] += 1
            if calls[node_id] == 1:
                return Graph.interrupt(f"question-{node_id}".encode())
            return values

        return interrupt_once

    graph = pair_graph(operation("a"), operation("b"))
    first = await graph.run(Graph.values(value="input"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    by_node = {view.node_id: view for view in first.interrupts}

    after_a = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                by_node["a"].interrupt_id,
                Graph.values(value="answer-a"),
            ),
        ),
    )
    assert isinstance(after_a, Graph.AwaitingResumeResult)
    assert tuple(view.node_id for view in after_a.interrupts) == ("b",)
    completed = await graph.run(
        state=after_a.state,
        continuation=after_a.continuation,
        resume=(
            graph.resume_interrupted(
                "b",
                after_a.interrupts[0].interrupt_id,
                Graph.values(value="answer-b"),
            ),
        ),
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert calls == {"a": 2, "b": 2}


async def test_doubly_nested_interrupt_resumes_by_exact_scope() -> None:
    calls: list[str] = []

    async def leaf(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        calls.append(values["value"])
        if len(calls) == 1:
            return Graph.interrupt(b"question")
        return values

    grandchild = Graph[str]("interrupt.doubly-nested.grandchild")
    codec = Codec()
    grandchild.set_resume_codec("input.v1", 1, codec.encode, codec.decode)
    grandchild.add_node(
        "leaf",
        leaf,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    grandchild.set_outputs({"value": Graph.node_output("leaf", "value")})

    child = Graph[str]("interrupt.doubly-nested.child")
    child.add_node(
        "grandchild",
        grandchild,
        inputs={"value": Graph.graph_input("value", str)},
    )
    child.set_outputs({"value": Graph.node_output("grandchild", "value")})

    root = Graph[str]("interrupt.doubly-nested.root")
    root.add_node(
        "child",
        child,
        inputs={"value": Graph.graph_input("value", str)},
    )
    root.set_outputs({"value": Graph.node_output("child", "value")})

    paused = await root.run(Graph.values(value="initial"))

    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert tuple((view.scope, view.node_id, view.request_payload) for view in paused.interrupts) == (
        (("child", "grandchild"), "leaf", b"question"),
    )

    completed = await root.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            root.resume_interrupted(
                "leaf",
                paused.interrupts[0].interrupt_id,
                Graph.values(value="answer"),
                scope=("child", "grandchild"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "answer"
    assert calls == ["initial", "answer"]


async def test_reused_child_interrupts_remain_isolated_by_sibling_scope() -> None:
    calls: list[str] = []

    async def leaf(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        value = values["value"]
        calls.append(value)
        if value.startswith("initial-"):
            return Graph.interrupt(value.encode())
        return values

    child = Graph[str]("interrupt.reused-child.child")
    codec = Codec()
    child.set_resume_codec("input.v1", 1, codec.encode, codec.decode)
    child.add_node(
        "leaf",
        leaf,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    child.set_outputs({"value": Graph.node_output("leaf", "value")})

    async def finish(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    root = Graph[str]("interrupt.reused-child.root")
    root.add_node(
        "left",
        child,
        inputs={"value": Graph.graph_input("left", str)},
    )
    root.add_node(
        "right",
        child,
        inputs={"value": Graph.graph_input("right", str)},
    )
    root.add_node(
        "finish",
        finish,
        inputs={
            "left": Graph.node_output("left", "value"),
            "right": Graph.node_output("right", "value"),
        },
        outputs={"left": str, "right": str},
    )
    root.add_join(("left", "right"), "finish")
    root.set_outputs(
        {
            "left": Graph.node_output("finish", "left"),
            "right": Graph.node_output("finish", "right"),
        }
    )

    first = await root.run(Graph.values(left="initial-left", right="initial-right"))

    assert isinstance(first, Graph.AwaitingResumeResult)
    assert tuple((view.scope, view.node_id) for view in first.interrupts) == (
        (("left",), "leaf"),
        (("right",), "leaf"),
    )
    assert first.interrupts[0].interrupt_id != first.interrupts[1].interrupt_id

    after_left = await root.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            root.resume_interrupted(
                "leaf",
                first.interrupts[0].interrupt_id,
                Graph.values(value="left-answer"),
                scope=("left",),
            ),
        ),
    )

    assert isinstance(after_left, Graph.AwaitingResumeResult)
    assert tuple((view.scope, view.node_id) for view in after_left.interrupts) == ((("right",), "leaf"),)

    completed = await root.run(
        state=after_left.state,
        continuation=after_left.continuation,
        resume=(
            root.resume_interrupted(
                "leaf",
                after_left.interrupts[0].interrupt_id,
                Graph.values(value="right-answer"),
                scope=("right",),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs.items() == (("left", "left-answer"), ("right", "right-answer"))
    assert calls == ["initial-left", "initial-right", "left-answer", "right-answer"]


async def test_interrupt_round_trip_keeps_request_and_resume_payloads_distinct() -> None:
    received: list[str] = []

    async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        received.append(values["value"])
        if len(received) == 1:
            return Graph.interrupt(b"request-payload")
        return values

    graph = interrupt_graph(interrupt_once, publish_output=True)
    first = await graph.run(Graph.values(value="initial"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    assert first.interrupts[0].request_payload == b"request-payload"
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                first.interrupts[0].interrupt_id,
                Graph.values(value="resume-value"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "resume-value"
    assert received == ["initial", "resume-value"]


async def test_interrupt_resume_delivers_distinct_inputs_per_node() -> None:
    received: dict[str, list[str]] = {"a": [], "b": []}

    def operation(node_id: str):
        async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
            received[node_id].append(values["value"])
            if len(received[node_id]) == 1:
                return Graph.interrupt(f"question-{node_id}".encode())
            return values

        return interrupt_once

    graph = pair_graph(operation("a"), operation("b"))
    first = await graph.run(Graph.values(value="initial"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    by_node = {interrupt.node_id: interrupt for interrupt in first.interrupts}
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                by_node["a"].interrupt_id,
                Graph.values(value="initial"),
            ),
            graph.resume_interrupted(
                "b",
                by_node["b"].interrupt_id,
                Graph.values(value="override"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert received == {"a": ["initial", "initial"], "b": ["initial", "override"]}


async def test_resume_codec_errors_before_claim_leave_state_quiescent() -> None:
    calls = 0

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.interrupt(b"question")

    graph = interrupt_graph(interrupt, codec=ValidatingCodec())
    first = await graph.run(Graph.values(value="input"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ValueAdmissionError, match="encoder rejected"):
        await graph.run(
            state=first.state,
            continuation=first.continuation,
            resume=(
                graph.resume_interrupted(
                    "a",
                    first.interrupts[0].interrupt_id,
                    Graph.values(value="invalid-answer"),
                ),
            ),
            commit=commits,
        )

    assert commits.transitions == []
    assert calls == 1


async def test_interrupt_override_is_redelivered_after_error_and_exact_fence() -> None:
    calls: list[str] = []
    should_error = True

    async def operation(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal should_error
        calls.append(values["value"])
        if len(calls) == 1:
            return Graph.interrupt(b"question")
        if should_error:
            raise RuntimeError("transient error")
        return values

    graph = interrupt_graph(operation, publish_output=True)
    first = await graph.run(Graph.values(value="initial"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    commits = CommitLog()
    with pytest.raises(RuntimeError, match="transient error"):
        await graph.run(
            state=first.state,
            continuation=first.continuation,
            resume=(
                graph.resume_interrupted(
                    "a",
                    first.interrupts[0].interrupt_id,
                    Graph.values(value="override"),
                ),
            ),
            commit=commits,
        )
    fenced = commits.transitions[-1].candidate_state
    assert fenced.execution is None

    should_error = False
    recovered = await graph.run(state=fenced)

    assert isinstance(recovered, Graph.CompletedResult)
    assert recovered.outputs["value"] == "override"
    assert calls == ["initial", "override", "override"]


async def test_repeated_interrupt_rejects_a_second_input_at_the_same_stable_activation() -> None:
    calls = 0

    async def operation(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return Graph.interrupt(f"question-{calls}".encode())
        return values

    graph = interrupt_graph(operation, publish_output=True)
    first = await graph.run(Graph.values(value="initial"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    second = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                first.interrupts[0].interrupt_id,
                Graph.values(value="first-answer"),
            ),
        ),
    )
    assert isinstance(second, Graph.AwaitingResumeResult)
    assert second.interrupts[0].interrupt_id != first.interrupts[0].interrupt_id
    with pytest.raises(Graph.ValuePublicationError, match="admitted more than once"):
        await graph.run(
            state=second.state,
            continuation=second.continuation,
            resume=(
                graph.resume_interrupted(
                    "a",
                    second.interrupts[0].interrupt_id,
                    Graph.values(value="second-answer"),
                ),
            ),
        )


async def test_repeated_interrupt_uses_new_generation_and_consumes_old_identity() -> None:
    calls = 0

    async def operation(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.interrupt(f"question-{calls}".encode())

    graph = interrupt_graph(operation)
    first = await graph.run(Graph.values(value="initial"), run_id="repeated-run")
    assert isinstance(first, Graph.AwaitingResumeResult)
    first_node = frontier_node(first.state.frontier, GraphNodeId("a"))
    assert first_node is not None
    assert isinstance(first_node.settlement, InterruptedGraphNode)

    second = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                first.interrupts[0].interrupt_id,
                Graph.values(value="first-answer"),
            ),
        ),
    )
    assert isinstance(second, Graph.AwaitingResumeResult)
    second_node = frontier_node(second.state.frontier, GraphNodeId("a"))
    assert second_node is not None
    assert isinstance(second_node.settlement, InterruptedGraphNode)
    assert (
        second_node.settlement.interrupt.identity.execution_generation
        == first_node.settlement.interrupt.identity.execution_generation + 1
    )
    assert second.interrupts[0].interrupt_id != first.interrupts[0].interrupt_id

    with pytest.raises(Graph.SnapshotMismatchError, match="does not match"):
        await graph.run(
            state=second.state,
            continuation=second.continuation,
            resume=(
                graph.resume_interrupted(
                    "a",
                    first.interrupts[0].interrupt_id,
                    Graph.values(value="stale"),
                ),
            ),
        )


async def test_interrupt_resume_then_self_loop_starts_a_clean_activation() -> None:
    received: list[str] = []

    async def operation(values: Graph.Values[str]) -> Graph.Outcome[str]:
        received.append(values["value"])
        if len(received) == 1:
            return Graph.interrupt(b"question")
        if len(received) == 2:
            return Graph.success(values, route="again")
        return Graph.success(values, route="done")

    graph = interrupt_graph(operation)
    graph.add_edge(Graph.START, "a")
    graph.add_conditional_edge("a", "again", "a")
    graph.add_conditional_edge("a", "done", Graph.END)
    first = await graph.run(Graph.values(value="initial"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                first.interrupts[0].interrupt_id,
                Graph.values(value="answer"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert received == ["initial", "answer", "initial"]


async def test_mixed_frontier_selectively_resumes_interrupt_without_rerunning_success() -> None:
    calls = {"a": 0, "b": 0}

    async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        calls["a"] += 1
        if calls["a"] == 1:
            return Graph.interrupt(b"question")
        return values

    async def succeed(values: Graph.Values[str]) -> Graph.Values[str]:
        calls["b"] += 1
        return values

    async def finish(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    graph = Graph[str]("interrupt.mixed")
    codec = Codec()
    graph.set_resume_codec("input.v1", 1, codec.encode, codec.decode)
    graph.add_node(
        "a",
        interrupt_once,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.add_node(
        "b",
        succeed,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.add_node(
        "final",
        finish,
        inputs={"value": Graph.node_output("b", "value")},
        outputs={"value": str},
    )
    graph.add_join(("a", "b"), "final")
    graph.set_outputs({"value": Graph.node_output("final", "value")})
    first = await graph.run(Graph.values(value="input"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                first.interrupts[0].interrupt_id,
                Graph.values(value="input"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "input"
    assert calls == {"a": 2, "b": 1}


async def test_interrupt_resume_applies_retained_sibling_join_arrival_once() -> None:
    a_calls = 0

    async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal a_calls
        a_calls += 1
        if a_calls == 1:
            return Graph.interrupt(b"question")
        return values

    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    async def joined(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=f"{values['a']}|{values['b']}")

    graph = Graph[str]("interrupt.join")
    codec = Codec()
    graph.set_resume_codec("input.v1", 1, codec.encode, codec.decode)
    graph.add_node(
        "a",
        interrupt_once,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.add_node(
        "b",
        echo,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.add_node(
        "joined",
        joined,
        inputs={
            "a": Graph.node_output("a", "value"),
            "b": Graph.node_output("b", "value"),
        },
        outputs={"value": str},
    )
    graph.add_join(("a", "b"), "joined")
    graph.set_outputs({"value": Graph.node_output("joined", "value")})
    first = await graph.run(Graph.values(value="input"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                first.interrupts[0].interrupt_id,
                Graph.values(value="answer"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "answer|input"
    assert a_calls == 2


async def test_falsy_sibling_output_survives_interrupt_resume_and_join() -> None:
    producer_calls = 0
    pause_calls = 0
    finish_calls = 0

    def encode_bool(values: Graph.Values[bool]) -> bytes:
        return b"1" if values["value"] else b"0"

    def decode_bool(payload: bytes) -> Graph.Values[bool]:
        return Graph.values(value=payload == b"1")

    async def produce(_values: Graph.Values[bool]) -> Graph.Values[bool]:
        nonlocal producer_calls
        producer_calls += 1
        return Graph.values(value=False)

    async def pause(values: Graph.Values[bool]) -> Graph.Values[bool] | Graph.Outcome[bool]:
        nonlocal pause_calls
        pause_calls += 1
        if pause_calls == 1:
            return Graph.interrupt(b"question")
        return values

    async def finish(values: Graph.Values[bool]) -> Graph.Values[bool]:
        nonlocal finish_calls
        finish_calls += 1
        assert values["producer"] is False
        assert values["pause"] is True
        return Graph.values(value=values["producer"])

    graph = Graph[bool]("interrupt.falsy-join")
    graph.set_resume_codec("bool.v1", 1, encode_bool, decode_bool)
    graph.add_node("producer", produce, inputs={}, outputs={"value": bool})
    graph.add_node(
        "pause",
        pause,
        inputs={"value": Graph.graph_input("value", bool)},
        outputs={"value": bool},
    )
    graph.add_node(
        "finish",
        finish,
        inputs={
            "producer": Graph.node_output("producer", "value"),
            "pause": Graph.node_output("pause", "value"),
        },
        outputs={"value": bool},
    )
    graph.add_join(("producer", "pause"), "finish")
    graph.set_outputs({"value": Graph.node_output("finish", "value")})

    paused = await graph.run(Graph.values(value=False))

    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert producer_calls == 1

    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            graph.resume_interrupted(
                "pause",
                paused.interrupts[0].interrupt_id,
                Graph.values(value=True),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] is False
    assert (producer_calls, pause_calls, finish_calls) == (1, 2, 1)


async def test_multiple_interrupts_can_be_resumed_together_by_exact_ids() -> None:
    calls = {"a": 0, "b": 0}

    def operation(node_id: str):
        async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
            calls[node_id] += 1
            if calls[node_id] == 1:
                return Graph.interrupt(node_id.encode())
            return values

        return interrupt_once

    graph = pair_graph(operation("a"), operation("b"))
    first = await graph.run(Graph.values(value="input"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    by_node = {view.node_id: view for view in first.interrupts}
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                by_node["a"].interrupt_id,
                Graph.values(value="answer-a"),
            ),
            graph.resume_interrupted(
                "b",
                by_node["b"].interrupt_id,
                Graph.values(value="answer-b"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert calls == {"a": 2, "b": 2}


async def test_one_resume_request_atomically_answers_multiple_nodes() -> None:
    calls = {"a": 0, "b": 0, "c": 0}

    def operation(node_id: str) -> NodeCallable[str]:
        async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
            calls[node_id] += 1
            if calls[node_id] == 1:
                return Graph.interrupt(f"question-{node_id}".encode())
            return values

        return interrupt_once

    graph = Graph[str]("interrupt.atomic")
    codec = Codec()
    graph.set_resume_codec("input.v1", 1, codec.encode, codec.decode)
    for node_id in calls:
        graph.add_node(
            node_id,
            operation(node_id),
            inputs={"value": Graph.graph_input("value", str)},
            outputs={"value": str},
        )
    graph.set_outputs({})
    first = await graph.run(Graph.values(value="input"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    by_node = {interrupt.node_id: interrupt for interrupt in first.interrupts}
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=tuple(
            graph.resume_interrupted(
                node_id,
                by_node[node_id].interrupt_id,
                Graph.values(value=f"answer-{node_id}"),
            )
            for node_id in calls
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert calls == {"a": 2, "b": 2, "c": 2}


async def test_aborted_override_is_neither_decoded_nor_scheduled() -> None:
    codec = CountingCodec()
    calls = 0

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.interrupt(b"question")

    graph = interrupt_graph(interrupt, codec=codec)
    first = await graph.run(Graph.values(value="input"), run_id="aborted-run")
    assert isinstance(first, Graph.AwaitingResumeResult)
    resumed_state = reduce_graph_run(
        first.state,
        ResumeGraphNodes(
            first.state.revision,
            (
                ResumeInterruptedNode(
                    GraphNodeId("a"),
                    first.interrupts[0].interrupt_id,
                    OverrideGraphNodeInput(GraphResumeInputPayload(b"opaque-answer")),
                ),
            ),
        ),
    )
    aborted_state = reduce_graph_run(
        resumed_state,
        AbortGraphRun(resumed_state.revision, GraphAbortReason("operator abort")),
    )

    aborted = await graph.run(state=aborted_state)

    assert isinstance(aborted, Graph.AbortedResult)
    assert codec.decode_calls == 0
    assert calls == 1
