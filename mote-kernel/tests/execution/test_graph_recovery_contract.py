import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.node import NodeCallable
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    FenceGraphExecution,
    GraphRunState,
    SettleGraphNode,
    StartGraphRun,
)


class AcknowledgementLostError(RuntimeError):
    pass


class CommitLog:
    def __init__(self) -> None:
        self.transitions: list[Graph.Transition[str]] = []

    async def __call__(self, transition: Graph.Transition[str], /) -> Graph.State:
        self.transitions.append(transition)
        return transition.candidate_state


def encode_empty(_values: Graph.Values[str]) -> bytes:
    return b""


def decode_empty(_payload: bytes) -> Graph.Values[str]:
    return Graph.values()


async def empty(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values()


@pytest.mark.asyncio
@pytest.mark.parametrize(("route", "available"), [("safe", True), ("needs-history", False)])
async def test_recovered_settled_conditional_uses_only_its_authoritative_route(
    route: str,
    available: bool,
) -> None:
    calls = {"source": 0, "safe": 0, "consumer": 0}
    captured: GraphRunState | None = None

    async def choose(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls["source"] += 1
        return Graph.success(Graph.values(value="published"), route=route)

    async def safe(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["safe"] += 1
        return Graph.values()

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["consumer"] += 1
        return Graph.values()

    async def lose_settlement(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, SettleGraphNode):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str](f"recovery.settled-route.{route}")
    graph.add_node("source", choose, inputs={}, outputs={"value": str})
    graph.add_node("safe", safe, inputs={}, outputs={})
    graph.add_node(
        "consumer",
        consume,
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    graph.add_conditional_edge("source", "safe", "safe")
    graph.add_conditional_edge("source", "needs-history", "consumer")
    graph.set_outputs({})

    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(), run_id=f"settled-{route}", commit=lose_settlement)
    assert captured is not None

    if not available:
        with pytest.raises(Graph.ValueUnavailableError, match="historical"):
            await graph.run(state=captured)
        assert calls == {"source": 1, "safe": 0, "consumer": 0}
        return

    result = await graph.run(state=captured)
    assert isinstance(result, Graph.CompletedResult)
    assert calls == {"source": 1, "safe": 1, "consumer": 0}


@pytest.mark.asyncio
async def test_recovery_worklist_merges_parallel_completion_orders_by_full_semantics() -> None:
    captured: GraphRunState | None = None
    calls = {"a": 0, "b": 0, "c": 0}

    def operation(node_id: str) -> NodeCallable[str]:
        async def run(_values: Graph.Values[str]) -> Graph.Values[str]:
            calls[node_id] += 1
            return Graph.values()

        return run

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str]("recovery.parallel-convergence")
    for node_id in calls:
        graph.add_node(node_id, operation(node_id), inputs={}, outputs={})
    graph.set_outputs({})
    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(), commit=lose_start, max_parallel_tasks=3)
    assert captured is not None

    completed = await graph.run(state=captured, max_parallel_tasks=3)

    assert isinstance(completed, Graph.CompletedResult)
    assert calls == {"a": 1, "b": 1, "c": 1}


@pytest.mark.asyncio
async def test_recovered_future_conditional_checks_every_declared_success_route() -> None:
    captured: GraphRunState | None = None
    calls = {"producer": 0, "decision": 0, "consumer": 0}

    async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["producer"] += 1
        return Graph.values(value="historical")

    async def decide(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls["decision"] += 1
        return Graph.success(Graph.values(), route="exit")

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["consumer"] += 1
        return Graph.values()

    async def lose_advance(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, AdvanceGraphFrontier):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str]("recovery.future-route")
    graph.add_node("producer", produce, inputs={}, outputs={"value": str})
    graph.add_node("decision", decide, inputs={}, outputs={})
    graph.add_node(
        "consumer",
        consume,
        inputs={"value": Graph.node_output("producer", "value")},
        outputs={},
    )
    graph.add_edge("producer", "decision")
    graph.add_conditional_edge("decision", "exit", Graph.END)
    graph.add_conditional_edge("decision", "needs-history", "consumer")
    graph.set_outputs({})

    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(), commit=lose_advance)
    assert captured is not None

    with pytest.raises(Graph.ValueUnavailableError, match="historical"):
        await graph.run(state=captured)
    assert calls == {"producer": 1, "decision": 0, "consumer": 0}


@pytest.mark.asyncio
async def test_recovered_control_target_rejects_a_lost_graph_input_before_mutation() -> None:
    captured: GraphRunState | None = None
    calls = {"source": 0, "consumer": 0}

    async def choose(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls["source"] += 1
        return Graph.success(Graph.values(), route="consume")

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["consumer"] += 1
        return Graph.values()

    async def lose_settlement(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, SettleGraphNode):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str]("recovery.lost-graph-input-target")
    graph.add_node("source", choose, inputs={}, outputs={})
    graph.add_node(
        "consumer",
        consume,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={},
    )
    graph.add_conditional_edge("source", "consume", "consumer")
    graph.set_outputs({})
    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(value="lost"), commit=lose_settlement)
    assert captured is not None
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match="historical"):
        await graph.run(state=captured, commit=commits)

    assert commits.transitions == []
    assert calls == {"source": 1, "consumer": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("input_source", ["graph-input", "publication"])
async def test_active_recovery_rejects_a_lost_pending_input_before_fence(input_source: str) -> None:
    captured: GraphRunState | None = None
    calls = {"producer": 0, "consumer": 0}

    async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["producer"] += 1
        return Graph.values(value="published")

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["consumer"] += 1
        return Graph.values()

    async def lose_consumer_claim(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, ClaimGraphExecution) and any(
            node.node_id == "consumer" for node in transition.candidate_state.frontier.nodes
        ):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str](f"recovery.active-lost-pending-input.{input_source}")
    if input_source == "publication":
        graph.add_node("producer", produce, inputs={}, outputs={"value": str})
        graph.add_node(
            "consumer",
            consume,
            inputs={"value": Graph.node_output("producer", "value")},
            outputs={},
        )
        graph.add_edge("producer", "consumer")
        values = Graph.values()
    else:
        graph.add_node(
            "consumer",
            consume,
            inputs={"value": Graph.graph_input("value", str)},
            outputs={},
        )
        values = Graph.values(value="lost")
    graph.set_outputs({})

    with pytest.raises(AcknowledgementLostError):
        await graph.run(values, commit=lose_consumer_claim)
    assert captured is not None
    assert captured.execution is not None
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match="historical"):
        await graph.run(state=captured, commit=commits)

    assert commits.transitions == []
    assert calls == {
        "producer": 1 if input_source == "publication" else 0,
        "consumer": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("output_source", ["graph-input", "node-output"])
async def test_recovered_completion_rejects_its_missing_output_history(output_source: str) -> None:
    captured: GraphRunState | None = None

    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    async def lose_settlement(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, SettleGraphNode):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str](f"recovery.lost-completion.{output_source}")
    graph.add_node("source", publish, inputs={}, outputs={"value": str})
    if output_source == "graph-input":
        graph.set_outputs({"value": Graph.graph_input("value", str)})
    else:
        graph.set_outputs({"value": Graph.node_output("source", "value")})
    values = Graph.values(value="input") if output_source == "graph-input" else Graph.values()
    with pytest.raises(AcknowledgementLostError):
        await graph.run(values, commit=lose_settlement)
    assert captured is not None

    with pytest.raises(Graph.ValueUnavailableError, match="historical"):
        await graph.run(state=captured)


@pytest.mark.asyncio
async def test_completed_recovered_seed_requires_its_graph_output_history() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    graph = Graph[str]("recovery.completed-output-history")
    graph.add_node("source", publish, inputs={}, outputs={"value": str})
    graph.set_outputs({"value": Graph.node_output("source", "value")})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)

    with pytest.raises(Graph.ValueUnavailableError, match="output history"):
        await graph.run(state=completed.state)


@pytest.mark.asyncio
async def test_recovered_new_child_rejects_a_lost_historical_input() -> None:
    captured: GraphRunState | None = None
    calls = {"producer": 0, "controller": 0, "child": 0}

    async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["producer"] += 1
        return Graph.values(value="lost")

    async def control(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["controller"] += 1
        return Graph.values()

    async def child_leaf(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["child"] += 1
        return Graph.values()

    async def lose_first_advance(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, AdvanceGraphFrontier):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    child = Graph[str]("recovery.new-child-input.child")
    child.add_node(
        "leaf",
        child_leaf,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={},
    )
    child.set_outputs({})
    parent = Graph[str]("recovery.new-child-input.parent")
    parent.add_node("producer", produce, inputs={}, outputs={"value": str})
    parent.add_node("controller", control, inputs={}, outputs={})
    parent.add_node(
        "child",
        child,
        inputs={"value": Graph.node_output("producer", "value")},
    )
    parent.add_edge("producer", "controller")
    parent.add_edge("controller", "child")
    parent.set_outputs({})
    with pytest.raises(AcknowledgementLostError):
        await parent.run(Graph.values(), commit=lose_first_advance)
    assert captured is not None

    with pytest.raises(Graph.ValueUnavailableError, match="historical"):
        await parent.run(state=captured)

    assert calls == {"producer": 1, "controller": 0, "child": 0}


@pytest.mark.asyncio
async def test_recovered_nested_settlement_checks_ordinary_sibling_history() -> None:
    captured: GraphRunState | None = None
    calls = {"producer": 0, "controller": 0, "child": 0, "consumer": 0}

    async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["producer"] += 1
        return Graph.values(value="lost")

    async def control(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["controller"] += 1
        return Graph.values()

    async def child_leaf(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["child"] += 1
        return Graph.values()

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["consumer"] += 1
        return Graph.values()

    async def lose_first_advance(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, AdvanceGraphFrontier):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    child = Graph[str]("recovery.sibling-history.child")
    child.add_node("leaf", child_leaf, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("recovery.sibling-history.parent")
    parent.add_node("producer", produce, inputs={}, outputs={"value": str})
    parent.add_node("controller", control, inputs={}, outputs={})
    parent.add_node("child", child, inputs={})
    parent.add_node(
        "consumer",
        consume,
        inputs={"value": Graph.node_output("producer", "value")},
        outputs={},
    )
    parent.add_edge("producer", "controller")
    parent.add_edge("controller", "child")
    parent.add_edge("controller", "consumer")
    parent.set_outputs({})
    with pytest.raises(AcknowledgementLostError):
        await parent.run(Graph.values(), commit=lose_first_advance)
    assert captured is not None

    with pytest.raises(Graph.ValueUnavailableError, match="historical"):
        await parent.run(state=captured)

    assert calls == {"producer": 1, "controller": 0, "child": 0, "consumer": 0}


def loop_graph(operation: NodeCallable[str], definition_id: str) -> Graph[str]:
    graph = Graph[str](definition_id)
    graph.add_node("loop", operation, inputs={}, outputs={})
    graph.add_edge(Graph.START, "loop")
    graph.add_conditional_edge("loop", "again", "loop")
    graph.add_conditional_edge("loop", "done", Graph.END)
    graph.set_outputs({})
    return graph


@pytest.mark.asyncio
async def test_recovered_future_exit_stops_before_an_alternative_limit_branch() -> None:
    captured: GraphRunState | None = None
    calls = 0

    async def exit_now(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.success(Graph.values(), route="done")

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = loop_graph(exit_now, "recovery.loop-exit")
    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(), commit=lose_start)
    assert captured is not None

    result = await graph.run(state=captured, max_supersteps=1)

    assert isinstance(result, Graph.CompletedResult)
    assert calls == 1


@pytest.mark.asyncio
async def test_recovered_concrete_backedge_raises_at_the_exact_planner_limit() -> None:
    captured: GraphRunState | None = None
    calls = 0

    async def continue_loop(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.success(Graph.values(), route="again")

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = loop_graph(continue_loop, "recovery.loop-limit")
    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(), commit=lose_start)
    assert captured is not None

    with pytest.raises(Graph.ExecutionLimitError, match="superstep limit"):
        await graph.run(state=captured, max_supersteps=1)
    assert calls == 1


@pytest.mark.asyncio
async def test_quiescent_recovered_seed_at_limit_has_no_mutation() -> None:
    captured: GraphRunState | None = None
    calls = 0

    async def continue_loop(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.success(Graph.values(), route="again")

    async def lose_advance(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, AdvanceGraphFrontier):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = loop_graph(continue_loop, "recovery.quiescent-limit")
    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(), commit=lose_advance)
    assert captured is not None and captured.superstep == 1
    commits = CommitLog()

    with pytest.raises(Graph.ExecutionLimitError, match="superstep limit"):
        await graph.run(state=captured, max_supersteps=1, commit=commits)

    assert commits.transitions == []
    assert calls == 1


@pytest.mark.asyncio
async def test_active_recovered_seed_fences_before_the_same_limit_boundary() -> None:
    captured: GraphRunState | None = None
    claims = 0
    calls = 0

    async def continue_loop(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.success(Graph.values(), route="again")

    async def lose_second_claim(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured, claims
        if isinstance(transition.command, ClaimGraphExecution):
            claims += 1
            if claims == 2:
                captured = transition.candidate_state
                raise AcknowledgementLostError
        return transition.candidate_state

    graph = loop_graph(continue_loop, "recovery.active-limit")
    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(), commit=lose_second_claim)
    assert captured is not None and captured.superstep == 1 and captured.execution is not None
    commits = CommitLog()

    with pytest.raises(Graph.ExecutionLimitError, match="superstep limit"):
        await graph.run(state=captured, max_supersteps=1, commit=commits)

    assert [type(transition.command) for transition in commits.transitions] == [FenceGraphExecution]
    assert calls == 1


def repeated_child_graph() -> Graph[str]:
    child = Graph[str]("recovery.repeated-child")
    child.add_node("complete", empty, inputs={}, outputs={})
    child.set_outputs({"query": Graph.graph_input("query", str)})
    return child


@pytest.mark.asyncio
@pytest.mark.parametrize("lineage", ["complete", "recovered"])
async def test_repeated_nested_path_keeps_distinct_child_runs_and_latest_boundary(lineage: str) -> None:
    calls = 0
    captured: GraphRunState | None = None

    async def produce(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Graph.success(Graph.values(query="first"), route="child")
        if calls == 2:
            return Graph.interrupt(b"continue between child runs")
        if calls == 3:
            return Graph.success(Graph.values(query="second"), route="child")
        return Graph.success(Graph.values(query="unused"), route="done")

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str](f"recovery.repeated-parent.{lineage}")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("produce", produce, inputs={}, outputs={"query": str})
    graph.add_node(
        "child",
        repeated_child_graph(),
        inputs={"query": Graph.node_output("produce", "query")},
    )
    graph.add_edge(Graph.START, "produce")
    graph.add_conditional_edge("produce", "child", "child")
    graph.add_conditional_edge("produce", "done", Graph.END)
    graph.add_edge("child", "produce")
    graph.set_outputs({})
    first_commits = CommitLog()

    if lineage == "complete":
        paused = await graph.run(Graph.values(), max_supersteps=8, commit=first_commits)
    else:
        with pytest.raises(AcknowledgementLostError):
            await graph.run(Graph.values(), commit=lose_start, max_supersteps=8)
        assert captured is not None
        paused = await graph.run(state=captured, max_supersteps=8, commit=first_commits)
    assert isinstance(paused, Graph.AwaitingResumeResult)

    second_commits = CommitLog()
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            graph.resume_interrupted(
                "produce",
                paused.interrupts[0].interrupt_id,
                Graph.values(),
            ),
        ),
        max_supersteps=8,
        commit=second_commits,
    )

    assert isinstance(completed, Graph.CompletedResult)
    child_outputs = tuple(
        transition.writes.settlement.output["query"]
        for transition in (*first_commits.transitions, *second_commits.transitions)
        if transition.scope == ()
        and isinstance(transition.writes.settlement, Graph.SuccessResult)
        and transition.writes.settlement.node_id == "child"
    )
    assert child_outputs == ("first", "second")
    assert calls == 4


@pytest.mark.asyncio
async def test_recovered_existing_nested_activation_requires_its_exact_child_snapshot() -> None:
    captured: GraphRunState | None = None
    child = repeated_child_graph()
    parent = Graph[str]("recovery.missing-child")
    parent.add_node("child", child, inputs={"query": Graph.graph_input("query", str)})
    parent.set_outputs({})

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    with pytest.raises(AcknowledgementLostError):
        await parent.run(Graph.values(query="lost"), commit=lose_start)
    assert captured is not None
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match="child snapshot"):
        await parent.run(state=captured, commit=commits)

    assert commits.transitions == []


def leaf_graph(definition_id: str, operation: NodeCallable[str]) -> Graph[str]:
    child = Graph[str](definition_id)
    child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    child.add_node("leaf", operation, inputs={}, outputs={"value": str})
    child.set_outputs({"value": Graph.node_output("leaf", "value")})
    return child


@pytest.mark.asyncio
async def test_recovered_family_drives_resource_waiter_to_quiescence_and_retains_sibling_output() -> None:
    captured: GraphRunState | None = None
    calls = {"a": 0, "b": 0}

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        calls["a"] += 1
        if calls["a"] == 1:
            return Graph.interrupt(b"continue-a")
        return Graph.values(value="a")

    async def succeed(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["b"] += 1
        return Graph.values(value="b")

    async def combine(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=f"{values['a']}|{values['b']}")

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str]("recovery.resource-waiter")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("a", interrupt_once, inputs={}, outputs={"value": str}, resources=("exclusive",))
    graph.add_node("b", succeed, inputs={}, outputs={"value": str}, resources=("exclusive",))
    graph.add_node(
        "final",
        combine,
        inputs={
            "a": Graph.node_output("a", "value"),
            "b": Graph.node_output("b", "value"),
        },
        outputs={"value": str},
    )
    graph.add_join(("a", "b"), "final")
    graph.set_outputs({"value": Graph.node_output("final", "value")})
    with pytest.raises(AcknowledgementLostError):
        await graph.run(Graph.values(), commit=lose_start, max_parallel_tasks=2)
    assert captured is not None

    paused = await graph.run(state=captured, max_parallel_tasks=2)
    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert calls == {"a": 1, "b": 1}
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                paused.interrupts[0].interrupt_id,
                Graph.values(),
            ),
        ),
        max_parallel_tasks=2,
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "a|b"
    assert calls == {"a": 2, "b": 1}


@pytest.mark.asyncio
async def test_recovered_family_drives_runnable_child_while_sibling_child_is_parked() -> None:
    captured: GraphRunState | None = None
    calls = {"starter": 0, "parked": 0, "runnable": 0}

    async def starter(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["starter"] += 1
        return Graph.values()

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        calls["parked"] += 1
        if calls["parked"] == 1:
            return Graph.interrupt(b"continue-parked")
        return Graph.values(value="resumed")

    async def succeed(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["runnable"] += 1
        return Graph.values(value="completed")

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    parent = Graph[str]("recovery.multiple-children")
    parent.add_node("starter", starter, inputs={}, outputs={})
    parent.add_node("parked", leaf_graph("recovery.parked-child", interrupt_once), inputs={})
    parent.add_node("runnable", leaf_graph("recovery.runnable-child", succeed), inputs={})
    parent.add_edge("starter", "parked")
    parent.add_edge("starter", "runnable")
    parent.set_outputs({})
    with pytest.raises(AcknowledgementLostError):
        await parent.run(Graph.values(), commit=lose_start)
    assert captured is not None

    paused = await parent.run(state=captured)
    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert calls == {"starter": 1, "parked": 1, "runnable": 1}
    parked = next(interrupt for interrupt in paused.interrupts if interrupt.scope == ("parked",))
    completed = await parent.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            parent.resume_interrupted(
                "leaf",
                parked.interrupt_id,
                Graph.values(),
                scope=("parked",),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert calls == {"starter": 1, "parked": 2, "runnable": 1}


@pytest.mark.asyncio
async def test_nested_child_limit_propagates_without_parent_failure_or_abort() -> None:
    calls = 0

    async def continue_loop(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.success(Graph.values(), route="again")

    child = loop_graph(continue_loop, "recovery.limited-child")
    parent = Graph[str]("recovery.limited-parent")
    parent.add_node("child", child, inputs={})
    parent.set_outputs({})
    commits = CommitLog()

    with pytest.raises(Graph.ExecutionLimitError, match="superstep limit"):
        await parent.run(Graph.values(), max_supersteps=1, commit=commits)

    assert calls == 1
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in commits.transitions)
    assert not any(
        isinstance(transition.command, SettleGraphNode) and transition.scope == () for transition in commits.transitions
    )


@pytest.mark.asyncio
async def test_recovered_new_child_limit_is_the_same_root_invocation_boundary() -> None:
    captured: GraphRunState | None = None
    calls = {"starter": 0, "child": 0}

    async def starter(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["starter"] += 1
        return Graph.values()

    async def continue_child(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls["child"] += 1
        return Graph.success(Graph.values(), route="again")

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise AcknowledgementLostError
        return transition.candidate_state

    child = loop_graph(continue_child, "recovery.new-limited-child")
    parent = Graph[str]("recovery.new-limited-parent")
    parent.add_node("starter", starter, inputs={}, outputs={})
    parent.add_node("child", child, inputs={})
    parent.add_edge("starter", "child")
    parent.set_outputs({})
    with pytest.raises(AcknowledgementLostError):
        await parent.run(Graph.values(), commit=lose_start, max_supersteps=2)
    assert captured is not None
    commits = CommitLog()

    with pytest.raises(Graph.ExecutionLimitError, match="superstep limit"):
        await parent.run(state=captured, max_supersteps=2, commit=commits)

    assert calls == {"starter": 1, "child": 2}
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in commits.transitions)
