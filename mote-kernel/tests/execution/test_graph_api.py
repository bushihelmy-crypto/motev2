import asyncio
import copy
import pickle
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol, cast

import pytest

import mote_kernel.execution as public_execution
import mote_kernel.execution.family_driver as family_driver_module
from mote_kernel.execution import Graph
from mote_kernel.execution.claim import PreparedExecutionClaim
from mote_kernel.execution.engine.claim_stage import project_claim_command
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.errors import FrameInstallationInvariantError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import _frame_value
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.invocation import PlannedResume
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ChildStateBinding,
    ContinuationSnapshot,
    ScopedFrameIndex,
    _admit_continuation,
    _CompiledFamilyIdentity,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    FenceGraphExecution,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphFrontierState,
    GraphNodeId,
    GraphRunState,
    GraphRunStatus,
    PendingGraphNode,
    ResumeGraphNodes,
    SettleGraphNode,
    StartGraphRun,
    SucceededGraphNode,
    UseStepRequestInput,
    reduce_graph_run,
)


class CommitLog:
    def __init__(self) -> None:
        self.transitions: list[Graph.Transition[str]] = []

    async def __call__(self, transition: Graph.Transition[str], /) -> Graph.State:
        self.transitions.append(transition)
        assert transition.candidate_state == reduce_graph_run(transition.previous_state, transition.command)
        writes = transition.writes
        assert writes.commit_key.run_id == transition.candidate_state.run_id
        assert writes.commit_key.revision == transition.candidate_state.revision
        if isinstance(transition.command, StartGraphRun):
            assert len(writes.graph_inputs) == 1
            assert writes.settlement is None
        else:
            assert writes.graph_inputs == ()
        if isinstance(transition.command, SettleGraphNode):
            settlement = writes.settlement
            assert settlement is not None
            assert settlement.node_id == transition.command.outcome.node_id
            if isinstance(settlement, Graph.SuccessResult):
                assert len(writes.publications) == 1
                assert writes.publications[0] is settlement.publication
            else:
                assert writes.publications == ()
        else:
            assert writes.settlement is None
            assert writes.publications == ()
        return transition.candidate_state


def fail_owner_construction(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    *,
    scope_depth: int,
) -> None:
    original = family_driver_module.require_scoped_snapshot_matches_graph

    def reject(
        graph: CompiledGraph[str],
        state: GraphRunState,
        scope_run: ScopeRunCoordinate,
    ) -> None:
        if len(scope_run.scope) == scope_depth:
            raise error
        original(graph, state, scope_run)

    monkeypatch.setattr(family_driver_module, "require_scoped_snapshot_matches_graph", reject)


class TextSubclass(str):
    pass


class BytesSubclass(bytes):
    pass


class StringTupleSubclass(tuple[str, ...]):
    pass


def test_value_carrying_public_factories_reject_noncanonical_values() -> None:
    invalid = cast(Graph.Values[str], "not-values")
    graph = Graph[str]("public.factory-values")

    with pytest.raises(Graph.ValueAdmissionError, match=r"Graph\.values"):
        Graph.success(invalid)
    with pytest.raises(Graph.ValueAdmissionError, match=r"Graph\.values"):
        graph.resume_interrupted("node", "interrupt", invalid)


class _RuntimeRun(Protocol):
    async def __call__(
        self,
        values: Graph.Values[str] | None = None,
        /,
        *,
        state: Graph.State | None = None,
        continuation: Graph.Continuation[str] | None = None,
        resume: tuple[Graph.ResumeAction[str], ...] = (),
        run_id: str | None = None,
    ) -> Graph.Result[str]: ...


class _CompiledOwnerView(Protocol):
    graph: CompiledGraph[str]
    family_identity: _CompiledFamilyIdentity


class _GraphOwnerView(Protocol):
    _compiled_owner: _CompiledOwnerView | None

    @staticmethod
    def read(graph: Graph[str]) -> _CompiledOwnerView | None:
        return cast(_GraphOwnerView, graph)._compiled_owner


class _ContinuationView(Protocol):
    _snapshot: ContinuationSnapshot[str]

    @staticmethod
    def read(continuation: Graph.Continuation[str]) -> ContinuationSnapshot[str]:
        return cast(_ContinuationView, continuation)._snapshot


def _require_compiled_owner(graph: Graph[str]) -> _CompiledOwnerView:
    owner = _GraphOwnerView.read(graph)
    assert owner is not None
    return owner


def _continuation_snapshot(continuation: Graph.Continuation[str]) -> ContinuationSnapshot[str]:
    return _ContinuationView.read(continuation)


def _require_partial_commit(error: Graph.Error) -> Graph.PartialCommitError[str]:
    if not isinstance(error, Graph.PartialCommitError):
        raise AssertionError("expected a partial commit error")
    return cast(Graph.PartialCommitError[str], error)


def encode_text(value: Graph.Values[str]) -> bytes:
    return value["value"].encode()


def decode_text(payload: bytes) -> Graph.Values[str]:
    return Graph.values(value=payload.decode())


def encode_empty(_value: Graph.Values[str]) -> bytes:
    return b""


def decode_empty(_payload: bytes) -> Graph.Values[str]:
    return Graph.values()


def interrupt_resume(
    graph: Graph[str],
    paused: Graph.AwaitingResumeResult[str],
    node_id: str,
    values: Graph.Values[str],
    *,
    scope: tuple[str, ...] = (),
) -> Graph.ResumeAction[str]:
    matches = tuple(
        interrupt for interrupt in paused.interrupts if interrupt.scope == scope and interrupt.node_id == node_id
    )
    assert len(matches) == 1
    return graph.resume_interrupted(
        node_id,
        matches[0].interrupt_id,
        values,
        scope=scope,
    )


def interrupt_once_child(definition_id: str) -> Graph[str]:
    attempts = 0

    async def interrupt_once(values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Graph.interrupt(b"question")
        return Graph.success(values)

    graph = Graph[str](definition_id)
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node(
        "leaf",
        interrupt_once,
        inputs={"value": input_ref()},
        outputs={"value": str},
    )
    graph.set_outputs({"value": Graph.node_output("leaf", "value")})
    return graph


def input_ref():
    return Graph.graph_input("value", str)


@pytest.mark.asyncio
async def test_graph_output_can_project_and_rename_an_admitted_graph_input() -> None:
    async def complete(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    graph = Graph[str]("input-passthrough.graph")
    graph.add_node("complete", complete, inputs={}, outputs={})
    graph.set_outputs({"renamed": Graph.graph_input("source", str)})

    result = await graph.run(Graph.values(source="input"), run_id="passthrough-run")

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs.items() == (("renamed", "input"),)


@pytest.mark.asyncio
async def test_conditional_callable_rejects_an_unknown_declared_route_before_settlement() -> None:
    async def choose_unknown(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(values, route="unknown")

    graph = Graph[str]("unknown-route.graph")
    graph.add_node(
        "choose",
        choose_unknown,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.add_conditional_edge("choose", "known", Graph.END)
    graph.set_outputs({"value": Graph.node_output("choose", "value")})

    with pytest.raises(Graph.RoutingError, match="unknown conditional route"):
        await graph.run(Graph.values(value="input"))


@pytest.mark.asyncio
async def test_public_feedback_reads_the_immediately_previous_activation_until_exit() -> None:
    seen: list[int] = []

    async def increment(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(
            Graph.values(value=value + 1),
            route="done" if value == 2 else "again",
        )

    graph = Graph[int]("public.feedback")
    graph.add_node(
        "loop",
        increment,
        inputs={
            "value": Graph.feedback(
                initial=Graph.graph_input("seed", int),
                repeat=Graph.node_output("loop", "value"),
            )
        },
        outputs={"value": int},
    )
    graph.add_edge(Graph.START, "loop")
    graph.add_conditional_edge("loop", "again", "loop")
    graph.add_conditional_edge("loop", "done", Graph.END)
    graph.set_outputs({"value": Graph.node_output("loop", "value")})

    result = await graph.run(Graph.values(seed=0), run_id="public-feedback-run")

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [0, 1, 2]
    assert result.outputs["value"] == 3


@pytest.mark.asyncio
async def test_graph_is_the_single_public_execution_facade_and_runs_plain_node_outputs() -> None:
    async def uppercase(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=values["value"].upper())

    graph = Graph[str]("public.graph")
    graph.add_node(
        "uppercase",
        uppercase,
        inputs={"value": input_ref()},
        outputs={"value": str},
    )
    graph.set_outputs({"value": Graph.node_output("uppercase", "value")})
    assert public_execution.__all__ == ["Graph"]
    assert public_execution.Graph is Graph

    commits = CommitLog()
    result: Graph.Result[str] = await graph.run(
        Graph.values(value="hello"),
        run_id="public-run",
        commit=commits,
    )

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "HELLO"
    assert result.continuation is not None
    repeated = await graph.run(state=result.state, continuation=result.continuation)
    assert isinstance(repeated, Graph.CompletedResult)
    assert repeated.outputs["value"] == "HELLO"
    with pytest.raises(Graph.ValidationError, match="immutable"):
        graph.add_node("late", uppercase, inputs={}, outputs={})

    empty = Graph.values()
    assert isinstance(empty, Graph.Values)
    assert len(empty) == 0
    assert tuple(empty) == ()
    assert empty.keys() == ()
    assert empty.values() == ()
    assert empty.items() == ()
    assert "value" not in empty
    with pytest.raises(KeyError, match="missing"):
        empty["missing"]
    with pytest.raises(Graph.ValueAdmissionError, match="canonical owner construction"):
        replace(empty, _construction=1, _seal=1)

    with pytest.raises(Graph.Error, match=r"Graph\.success"):
        replace(Graph.success(empty), _seal=1)
    with pytest.raises(Graph.Error, match=r"Graph\.failure"):
        replace(Graph.failure("failed"), _seal=1)
    with pytest.raises(Graph.Error, match=r"Graph\.interrupt"):
        replace(Graph.interrupt(b"review"), _seal=1)
    for invalid_text in (TextSubclass("route"), "", " route", "route\n", "route\r"):
        with pytest.raises(Graph.Error, match="non-empty trimmed"):
            Graph.success(empty, route=invalid_text)
        with pytest.raises(Graph.Error, match="non-empty trimmed"):
            Graph.failure(invalid_text)
    with pytest.raises(Graph.Error, match="must be bytes"):
        Graph.interrupt(BytesSubclass(b"review"))

    first_transition = commits.transitions[0]
    with pytest.raises(Graph.SnapshotMismatchError, match="execution commit owner"):
        replace(first_transition, _seal=1)
    successful_settlement = next(
        transition.writes.settlement
        for transition in commits.transitions
        if isinstance(transition.writes.settlement, Graph.SuccessResult)
    )
    with pytest.raises(Graph.Error, match="settlement admission"):
        replace(successful_settlement, _seal=1)

    with pytest.raises(Graph.SnapshotMismatchError, match="Graph result"):
        replace(result.continuation, _seal=1)
    with pytest.raises(Graph.SnapshotMismatchError, match="copy contract"):
        copy.copy(result.continuation)
    with pytest.raises(Graph.SnapshotMismatchError, match="serialization contract"):
        copy.deepcopy(result.continuation)
    with pytest.raises(Graph.SnapshotMismatchError, match="serialization contract"):
        pickle.dumps(result.continuation)

    with pytest.raises(Graph.Error, match="family driver"):
        replace(result, _seal=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("outgoing", ["conditional", "end"])
async def test_missing_control_fails_before_side_effects_and_leaves_the_builder_mutable(outgoing: str) -> None:
    calls: list[str] = []

    async def source(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append("source")
        return Graph.success(
            Graph.values(value="published"),
            route="go" if outgoing == "conditional" else None,
        )

    async def hidden(values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append(f"hidden:{values['value']}")
        return Graph.values()

    async def visible(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("visible")
        return Graph.values()

    graph = Graph[str](f"public.explicit-activation.{outgoing}")
    graph.add_node("source", source, inputs={}, outputs={"value": str})
    graph.add_node(
        "hidden",
        hidden,
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    if outgoing == "conditional":
        graph.add_node("visible", visible, inputs={}, outputs={})
        graph.add_conditional_edge("source", "go", "visible")
    else:
        graph.add_edge("source", Graph.END)
    graph.set_outputs({})
    commits = CommitLog()

    with pytest.raises(
        Graph.ValidationError,
        match=r"node 'hidden' consumes node outputs from \('source',\) but has no incoming control edge",
    ):
        await graph.run(Graph.values(), commit=commits)

    assert calls == []
    assert commits.transitions == []
    assert _GraphOwnerView.read(graph) is None

    graph.add_edge("source", "hidden")
    completed = await graph.run(Graph.values())

    assert isinstance(completed, Graph.CompletedResult)
    assert calls.count("source") == 1
    assert calls.count("hidden:published") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("route", "expected"), [("go", ["published"]), ("stop", [])])
async def test_conditional_control_alone_selects_a_node_output_consumer(route: str, expected: list[str]) -> None:
    consumed: list[str] = []

    async def choose(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(Graph.values(value="published"), route=route)

    async def consume(values: Graph.Values[str]) -> Graph.Values[str]:
        consumed.append(values["value"])
        return Graph.values()

    graph = Graph[str](f"public.explicit-conditional.{route}")
    graph.add_node("choose", choose, inputs={}, outputs={"value": str})
    graph.add_node(
        "consume",
        consume,
        inputs={"value": Graph.node_output("choose", "value")},
        outputs={},
    )
    graph.add_conditional_edge("choose", "go", "consume")
    graph.add_conditional_edge("choose", "stop", Graph.END)
    graph.set_outputs({})

    result = await graph.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert consumed == expected


@pytest.mark.asyncio
async def test_one_compiled_facade_runs_independent_states_concurrently() -> None:
    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        await asyncio.sleep(0)
        return values

    graph = Graph[str]("public.concurrent")
    graph.add_node("node", echo, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({"value": Graph.node_output("node", "value")})

    first, second = await asyncio.gather(
        graph.run(Graph.values(value="first"), run_id="first-run"),
        graph.run(Graph.values(value="second"), run_id="second-run"),
    )

    assert isinstance(first, Graph.CompletedResult)
    assert isinstance(second, Graph.CompletedResult)
    assert first.state.run_id == "first-run"
    assert second.state.run_id == "second-run"
    assert first.outputs["value"] == "first"
    assert second.outputs["value"] == "second"


@pytest.mark.asyncio
async def test_run_commits_each_resource_node_transition_and_immediately_admits_the_waiter() -> None:
    async def complete(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    graph = Graph[str]("public.resources")
    for node_id in ("b", "a"):
        graph.add_node(
            node_id,
            complete,
            inputs={"value": input_ref()},
            outputs={"value": str},
            resources=("exclusive",),
        )
    graph.set_outputs({})
    commits = CommitLog()

    result = await graph.run(
        Graph.values(value="value"),
        run_id="resource-run",
        commit=commits,
        max_parallel_tasks=2,
    )

    assert isinstance(result, Graph.CompletedResult)
    assert [type(transition.command) for transition in commits.transitions] == [
        StartGraphRun,
        ClaimGraphExecution,
        SettleGraphNode,
        SettleGraphNode,
        CompleteGraphFrontier,
    ]
    settlements = [transition for transition in commits.transitions if isinstance(transition.command, SettleGraphNode)]
    admitted = [transition.writes.settlement for transition in settlements]
    assert all(isinstance(item, Graph.SuccessResult) for item in admitted)
    assert [item.node_id for item in admitted if isinstance(item, Graph.SuccessResult)] == ["a", "b"]
    first_resources = settlements[0].candidate_state.resources
    assert first_resources is not None
    assert first_resources.acquisitions[0].node_id == "b"
    assert first_resources.acquisitions[0].admitted
    assert result.outputs.keys() == ()


@pytest.mark.asyncio
async def test_node_resources_register_once_in_deterministic_first_seen_order() -> None:
    async def complete(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    graph = Graph[str]("public.resource-order")
    graph.add_node(
        "a",
        complete,
        inputs={"value": input_ref()},
        outputs={"value": str},
        resources=("beta", "alpha"),
    )
    graph.add_node(
        "b",
        complete,
        inputs={"value": input_ref()},
        outputs={"value": str},
        resources=("alpha", "gamma"),
    )
    graph.set_outputs({})
    commits = CommitLog()

    result = await graph.run(Graph.values(value="value"), commit=commits, max_parallel_tasks=2)

    assert isinstance(result, Graph.CompletedResult)
    claim = commits.transitions[1]
    assert isinstance(claim.command, ClaimGraphExecution)
    resources = claim.candidate_state.resources
    assert resources is not None
    assert tuple(lock.resource_id for lock in resources.resources) == ("beta", "alpha", "gamma")
    assert tuple(acquisition.required for acquisition in resources.acquisitions) == (
        ("beta", "alpha"),
        ("alpha", "gamma"),
    )

    with pytest.raises(Graph.ValidationError, match="exact positive"):
        Graph[str]("public.invalid-version", version=True)
    with pytest.raises(Graph.ValidationError, match="exact positive"):
        Graph[str]("public.invalid-version", version=0)

    async def empty(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    entry = Graph[str]("public.builder-entry")
    input_bindings = {"value": input_ref()}
    output_declarations = {"value": str}
    with pytest.raises(Graph.ValidationError, match="repeat one resource"):
        entry.add_node(
            "failed",
            complete,
            inputs=input_bindings,
            outputs=output_declarations,
            resources=("database", "database"),
        )
    entry.add_node(
        "node",
        complete,
        inputs=input_bindings,
        outputs=output_declarations,
        resources=("network",),
    )
    input_bindings.clear()
    output_declarations.clear()
    with pytest.raises(Graph.ValidationError, match="START must target"):
        entry.add_edge(Graph.START, Graph.END)
    with pytest.raises(Graph.ValidationError, match="non-empty trimmed"):
        entry.add_edge("", "node")
    graph_outputs = {"value": Graph.node_output("node", "value")}
    with pytest.raises(Graph.ValidationError, match="graph output name"):
        entry.set_outputs({" bad": Graph.node_output("node", "value")})
    entry.set_outputs(graph_outputs)
    graph_outputs.clear()
    with pytest.raises(Graph.ValidationError, match="exact positive"):
        entry.set_resume_codec("text", False, encode_text, decode_text)
    entry.set_resume_codec("text", 1, encode_text, decode_text)
    with pytest.raises(Graph.ValidationError, match="exactly once"):
        entry.set_resume_codec("text-again", 1, encode_text, decode_text)
    entry_commits = CommitLog()
    entry_result = await entry.run(Graph.values(value="retained"), commit=entry_commits)
    assert isinstance(entry_result, Graph.CompletedResult)
    assert entry_result.outputs["value"] == "retained"
    entry_claim = next(
        transition.candidate_state.resources
        for transition in entry_commits.transitions
        if isinstance(transition.command, ClaimGraphExecution)
    )
    assert entry_claim is not None
    assert tuple(lock.resource_id for lock in entry_claim.resources) == ("network",)

    conditional = Graph[str]("public.builder-conditional")

    async def choose(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(Graph.values(), route="next")

    conditional.add_node("choose", choose, inputs={}, outputs={})
    conditional.add_node("target", empty, inputs={}, outputs={})
    with pytest.raises(Graph.ValidationError, match="invalid boundary direction"):
        conditional.add_conditional_edge(Graph.START, "next", "target")
    conditional.add_conditional_edge("choose", "next", "target")
    conditional.set_outputs({})
    assert isinstance(await conditional.run(Graph.values()), Graph.CompletedResult)

    joined = Graph[str]("public.builder-join")
    for node_id in ("a", "b", "target"):
        joined.add_node(node_id, empty, inputs={}, outputs={})
    with pytest.raises(Graph.ValidationError, match="must be a tuple"):
        joined.add_join(StringTupleSubclass(("a", "b")), "target")
    with pytest.raises(Graph.ValidationError, match="invalid boundary direction"):
        joined.add_join((Graph.START, "b"), "target")
    joined.add_join(("a", "b"), "target")
    joined.set_outputs({})
    assert isinstance(await joined.run(Graph.values()), Graph.CompletedResult)

    retry_after_compile_failure = Graph[str]("public.builder-compile-retry")
    retry_after_compile_failure.add_node("node", empty, inputs={}, outputs={})
    with pytest.raises(Graph.ValidationError, match="set_outputs"):
        await retry_after_compile_failure.run(Graph.values())
    retry_after_compile_failure.set_outputs({})
    assert isinstance(await retry_after_compile_failure.run(Graph.values()), Graph.CompletedResult)


@pytest.mark.asyncio
async def test_public_builder_supports_conditional_routing_and_joins() -> None:
    async def decision(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(Graph.values(value=f"decision:{values['value']}"), route="left")

    async def plain(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    async def combine(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=f"{values['left']}|{values['side']}")

    graph = Graph[str]("public.routing")
    graph.add_node("decision", decision, inputs={"value": input_ref()}, outputs={"value": str})
    graph.add_node("side", plain, inputs={"value": input_ref()}, outputs={"value": str})
    graph.add_node(
        "left",
        plain,
        inputs={"value": Graph.node_output("decision", "value")},
        outputs={"value": str},
    )
    graph.add_node(
        "joined",
        combine,
        inputs={
            "left": Graph.node_output("left", "value"),
            "side": Graph.node_output("side", "value"),
        },
        outputs={"value": str},
    )
    graph.add_conditional_edge("decision", "left", "left")
    graph.add_join(("left", "side"), "joined")
    graph.set_outputs({"value": Graph.node_output("joined", "value")})

    result = await graph.run(Graph.values(value="input"))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "decision:input|input"


@pytest.mark.asyncio
async def test_fanout_conditional_branches_and_join_share_one_activation() -> None:
    calls: list[str] = []

    async def choose(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append("choose")
        return Graph.success(Graph.values(value="chosen"), route="left")

    def branch(name: str):
        async def run(values: Graph.Values[str]) -> Graph.Outcome[str]:
            calls.append(name)
            return Graph.success(Graph.values(value=f"{values['value']}:{name}"), route="go")

        return run

    def branch_result(name: str):
        async def run(_values: Graph.Values[str]) -> Graph.Values[str]:
            calls.append(name)
            return Graph.values(value=name)

        return run

    async def merge(values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("merge")
        return Graph.values(value=f"{values['left']}|{values['right']}")

    graph = Graph[str]("public.fanout-conditional-join")
    graph.add_node("choose", choose, inputs={}, outputs={"value": str})
    graph.add_node(
        "left",
        branch("left"),
        inputs={"value": Graph.node_output("choose", "value")},
        outputs={"value": str},
    )
    graph.add_node(
        "right",
        branch("right"),
        inputs={"value": Graph.node_output("choose", "value")},
        outputs={"value": str},
    )
    graph.add_node(
        "shared",
        branch_result("shared"),
        inputs={},
        outputs={"value": str},
    )
    graph.add_node(
        "left-result",
        branch_result("left-result"),
        inputs={},
        outputs={"value": str},
    )
    graph.add_node(
        "right-result",
        branch_result("right-result"),
        inputs={},
        outputs={"value": str},
    )
    graph.add_node(
        "merge",
        merge,
        inputs={
            "left": Graph.node_output("left-result", "value"),
            "right": Graph.node_output("right-result", "value"),
        },
        outputs={"value": str},
    )
    graph.add_conditional_edge("choose", "left", "left")
    graph.add_conditional_edge("choose", "right", "right")
    graph.add_conditional_edge("left", "go", "shared")
    graph.add_conditional_edge("right", "go", "shared")
    graph.add_edge("shared", "left-result")
    graph.add_edge("shared", "right-result")
    graph.add_join(("left-result", "right-result"), "merge")
    graph.set_outputs({"value": Graph.node_output("merge", "value")})

    result = await graph.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "left-result|right-result"
    assert calls.count("choose") == 1
    assert calls.count("left") == 1
    assert calls.count("right") == 0
    assert calls.count("shared") == 1
    assert calls.count("left-result") == calls.count("right-result") == 1
    assert calls.count("merge") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_route", ["left", "right"])
async def test_mutually_exclusive_routes_converge_without_repeating_the_shared_node(selected_route: str) -> None:
    calls: list[str] = []

    async def choose(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append("choose")
        return Graph.success(Graph.values(), route=selected_route)

    def branch(node_id: str):
        async def run(_values: Graph.Values[str]) -> Graph.Outcome[str]:
            calls.append(node_id)
            return Graph.success(Graph.values(), route="go")

        return run

    async def ordinary(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("ordinary")
        return Graph.values()

    async def shared(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("shared")
        return Graph.values()

    async def target(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("target")
        return Graph.values()

    graph = Graph[str](f"public.mutually-exclusive-{selected_route}")
    graph.add_node("choose", choose, inputs={}, outputs={})
    graph.add_node("left", branch("left"), inputs={}, outputs={})
    graph.add_node("right", branch("right"), inputs={}, outputs={})
    graph.add_node("ordinary", ordinary, inputs={}, outputs={})
    graph.add_node("shared", shared, inputs={}, outputs={})
    graph.add_node("target", target, inputs={}, outputs={})
    graph.add_edge("choose", "ordinary")
    graph.add_conditional_edge("choose", "left", "left")
    graph.add_conditional_edge("choose", "right", "right")
    graph.add_conditional_edge("left", "go", "shared")
    graph.add_conditional_edge("right", "go", "shared")
    graph.add_join(("ordinary", "shared"), "target")
    graph.set_outputs({})

    result = await graph.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert calls.count("shared") == 1
    assert calls.count("target") == 1
    assert calls.count(selected_route) == 1
    assert calls.count("left" if selected_route == "right" else "right") == 0


@pytest.mark.asyncio
async def test_noncyclic_join_result_is_independent_of_branch_completion_order() -> None:
    async def run_in_order(order: tuple[str, str]) -> tuple[str, tuple[str, ...]]:
        started = {node_id: asyncio.Event() for node_id in ("left", "right")}
        finished = {node_id: asyncio.Event() for node_id in ("left", "right")}
        release = {node_id: asyncio.Event() for node_id in ("left", "right")}
        calls: list[str] = []

        def branch(node_id: str):
            async def execute(_values: Graph.Values[str]) -> Graph.Values[str]:
                started[node_id].set()
                await release[node_id].wait()
                calls.append(node_id)
                finished[node_id].set()
                return Graph.values(value=node_id)

            return execute

        async def combine(values: Graph.Values[str]) -> Graph.Values[str]:
            calls.append("join")
            return Graph.values(value=f"{values['left']}|{values['right']}")

        graph = Graph[str](f"public.join-order-{order[0]}-{order[1]}")
        graph.add_node("left", branch("left"), inputs={}, outputs={"value": str})
        graph.add_node("right", branch("right"), inputs={}, outputs={"value": str})
        graph.add_node(
            "join",
            combine,
            inputs={
                "left": Graph.node_output("left", "value"),
                "right": Graph.node_output("right", "value"),
            },
            outputs={"value": str},
        )
        graph.add_join(("left", "right"), "join")
        graph.set_outputs({"value": Graph.node_output("join", "value")})

        running = asyncio.create_task(graph.run(Graph.values(), max_parallel_tasks=2))
        await asyncio.gather(*(started[node_id].wait() for node_id in started))
        for node_id in order:
            release[node_id].set()
            await finished[node_id].wait()
        result = await running

        assert isinstance(result, Graph.CompletedResult)
        return result.outputs["value"], tuple(calls)

    first = await run_in_order(("left", "right"))
    second = await run_in_order(("right", "left"))

    assert first[0] == second[0] == "left|right"
    assert first[1][-1] == second[1][-1] == "join"
    assert first[1][:2] == ("left", "right")
    assert second[1][:2] == ("right", "left")


@pytest.mark.asyncio
async def test_interrupt_resume_actions_share_one_canonical_scope_commit() -> None:
    attempts = {"a": 0, "b": 0}

    def operation(node_id: str):
        async def interrupt_once(values: Graph.Values[str]) -> Graph.Outcome[str]:
            attempts[node_id] += 1
            if attempts[node_id] == 1:
                return Graph.interrupt(f"question-{node_id}".encode())
            return Graph.success(values)

        return interrupt_once

    async def combine(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=f"{values['a']}|{values['b']}")

    graph = Graph[str]("public.resume")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    for node_id in ("a", "b"):
        graph.add_node(
            node_id,
            operation(node_id),
            inputs={"value": input_ref()},
            outputs={"value": str},
        )
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

    paused = await graph.run(Graph.values(value="initial"), run_id="resume-run")

    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert tuple(view.node_id for view in paused.interrupts) == ("a", "b")
    with pytest.raises(Graph.Error, match="family driver"):
        replace(paused, _seal=1)
    commits = CommitLog()
    resumed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            interrupt_resume(graph, paused, "a", Graph.values(value="initial")),
            interrupt_resume(graph, paused, "b", Graph.values(value="override")),
        ),
        commit=commits,
    )

    assert isinstance(resumed, Graph.CompletedResult)
    assert isinstance(commits.transitions[0].command, ResumeGraphNodes)
    assert tuple(action.node_id for action in commits.transitions[0].command.actions) == ("a", "b")
    assert resumed.outputs["value"] == "initial|override"


@pytest.mark.asyncio
async def test_same_scope_resume_inputs_install_as_one_frame_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"a": 0, "b": 0}

    def operation(node_id: str):
        async def interrupt_once(values: Graph.Values[str]) -> Graph.Outcome[str]:
            attempts[node_id] += 1
            if attempts[node_id] == 1:
                return Graph.interrupt(f"question-{node_id}".encode())
            return Graph.success(values)

        return interrupt_once

    graph = Graph[str]("public.atomic-resume-frames")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    for node_id in ("a", "b"):
        graph.add_node(
            node_id,
            operation(node_id),
            inputs={"value": input_ref()},
            outputs={"value": str},
        )
    graph.set_outputs({})
    paused = await graph.run(Graph.values(value="initial"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    observed: list[tuple[ScopedFrameIndex[str], ScopedFrameIndex[str]]] = []
    original = family_driver_module.project_resume_frames

    def capture(
        frames: ScopedFrameIndex[str],
        planned: PlannedResume[str],
    ) -> ScopedFrameIndex[str]:
        installed = original(frames, planned)
        with pytest.raises(FrameInstallationInvariantError, match="owner-local projection"):
            original(installed, planned)
        observed.append((frames, installed))
        return installed

    monkeypatch.setattr(family_driver_module, "project_resume_frames", capture)

    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            interrupt_resume(graph, paused, "a", Graph.values(value="first")),
            interrupt_resume(graph, paused, "b", Graph.values(value="second")),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert len(observed) == 1
    before, installed = observed[0]
    assert before.resume_inputs == ()
    assert len(installed.resume_inputs) == 2
    assert installed.publications == before.publications


@pytest.mark.asyncio
async def test_sibling_scope_resume_inputs_materialize_without_cross_talk() -> None:
    observed: list[tuple[str, str]] = []

    def child(definition_id: str, label: str) -> Graph[str]:
        attempts = 0

        async def interrupt_once(values: Graph.Values[str]) -> Graph.Outcome[str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return Graph.interrupt(f"question-{label}".encode())
            return Graph.success(values)

        async def consume(values: Graph.Values[str]) -> Graph.Values[str]:
            observed.append((label, values["value"]))
            return Graph.values()

        graph = Graph[str](definition_id)
        graph.set_resume_codec("text", 1, encode_text, decode_text)
        graph.add_node(
            "leaf",
            interrupt_once,
            inputs={"value": input_ref()},
            outputs={"value": str},
        )
        graph.add_node(
            "consume",
            consume,
            inputs={"value": Graph.node_output("leaf", "value")},
            outputs={},
        )
        graph.add_edge("leaf", "consume")
        graph.set_outputs({})
        return graph

    parent = Graph[str]("public.sibling-resume-input-identity")
    parent.add_node(
        "left",
        child("public.sibling.left", "left"),
        inputs={"value": input_ref()},
    )
    parent.add_node(
        "right",
        child("public.sibling.right", "right"),
        inputs={"value": input_ref()},
    )
    parent.set_outputs({})
    paused = await parent.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)

    completed = await parent.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            interrupt_resume(
                parent,
                paused,
                "leaf",
                Graph.values(value="L"),
                scope=("left",),
            ),
            interrupt_resume(
                parent,
                paused,
                "leaf",
                Graph.values(value="R"),
                scope=("right",),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert sorted(observed) == [("left", "L"), ("right", "R")]
    owner = _require_compiled_owner(parent)
    context = _admit_continuation(owner.family_identity, completed.state, completed.continuation)
    assert tuple(record.coordinate.activation.scope_run.scope for record in context.frames.resume_inputs) == (
        (GraphNodeId("left"),),
        (GraphNodeId("right"),),
    )
    assert tuple(_frame_value(record.frame, "value") for record in context.frames.resume_inputs) == ("L", "R")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_second", [False, True], ids=("error", "cancellation"))
async def test_multi_scope_resume_keeps_first_confirmed_install_when_second_commit_fails(
    cancel_second: bool,
) -> None:
    class SecondScopeCommitError(RuntimeError):
        pass

    parent = Graph[str]("public.multi-scope-partial-confirmation")
    parent.add_node(
        "left",
        interrupt_once_child("public.multi-scope.left"),
        inputs={"value": input_ref()},
    )
    parent.add_node(
        "right",
        interrupt_once_child("public.multi-scope.right"),
        inputs={"value": input_ref()},
    )
    parent.set_outputs({})
    paused = await parent.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    transitions: list[Graph.Transition[str]] = []

    old_snapshot = _continuation_snapshot(paused.continuation)
    original_error: BaseException = asyncio.CancelledError() if cancel_second else SecondScopeCommitError()

    async def fail_second_scope(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        if transition.scope == ("right",):
            raise original_error
        return transition.candidate_state

    try:
        await parent.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                interrupt_resume(
                    parent,
                    paused,
                    "leaf",
                    Graph.values(value="left"),
                    scope=("left",),
                ),
                interrupt_resume(
                    parent,
                    paused,
                    "leaf",
                    Graph.values(value="right"),
                    scope=("right",),
                ),
            ),
            commit=fail_second_scope,
        )
    except Graph.Error as error:
        partial = _require_partial_commit(error)
    else:
        pytest.fail("second scope failure must produce an explicit partial handoff")
    assert partial.cause is original_error
    assert partial.__cause__ is original_error
    assert partial.failed_scope == ("right",)
    assert _continuation_snapshot(paused.continuation) is old_snapshot

    assert tuple(transition.scope for transition in transitions) == (("left",), ("right",))
    assert all(isinstance(transition.command, ResumeGraphNodes) for transition in transitions)
    compiled_owner = _require_compiled_owner(parent)
    checkpoint = _admit_continuation(
        compiled_owner.family_identity,
        partial.state,
        partial.continuation,
    )
    left = next(binding for binding in checkpoint.child_states if binding.coordinate.scope == (GraphNodeId("left"),))
    left_input = next(
        record
        for record in checkpoint.frames.resume_inputs
        if record.coordinate.activation.scope_run.scope == (GraphNodeId("left"),)
    )
    assert left.state == transitions[0].candidate_state
    assert _frame_value(left_input.frame, "value") == "left"
    assert not any(
        record.coordinate.activation.scope_run.scope == (GraphNodeId("right"),)
        for record in checkpoint.frames.resume_inputs
    )

    retried = await parent.run(
        state=partial.state,
        continuation=partial.continuation,
        resume=(
            interrupt_resume(
                parent,
                paused,
                "leaf",
                Graph.values(value="right"),
                scope=("right",),
            ),
        ),
    )
    assert isinstance(retried, Graph.CompletedResult)


@pytest.mark.asyncio
async def test_multi_scope_resume_keeps_first_install_when_second_confirmation_is_non_exact() -> None:
    parent = Graph[str]("public.multi-scope-non-exact")
    parent.add_node(
        "left",
        interrupt_once_child("public.multi-scope-non-exact.left"),
        inputs={"value": input_ref()},
    )
    parent.add_node(
        "right",
        interrupt_once_child("public.multi-scope-non-exact.right"),
        inputs={"value": input_ref()},
    )
    parent.set_outputs({})
    paused = await parent.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)

    async def non_exact_second(transition: Graph.Transition[str], /) -> Graph.State:
        if transition.scope == ("right",):
            return replace(transition.candidate_state, revision=transition.candidate_state.revision + 1)
        return transition.candidate_state

    old_snapshot = _continuation_snapshot(paused.continuation)
    try:
        await parent.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                interrupt_resume(
                    parent,
                    paused,
                    "leaf",
                    Graph.values(value="left"),
                    scope=("left",),
                ),
                interrupt_resume(
                    parent,
                    paused,
                    "leaf",
                    Graph.values(value="right"),
                    scope=("right",),
                ),
            ),
            commit=non_exact_second,
        )
    except Graph.Error as error:
        partial = _require_partial_commit(error)
    else:
        pytest.fail("non-exact second scope must produce an explicit partial handoff")
    assert isinstance(partial.cause, Graph.SnapshotMismatchError)
    assert "exact authoritative" in str(partial.cause)
    assert partial.__cause__ is partial.cause
    assert partial.failed_scope == ("right",)
    assert _continuation_snapshot(paused.continuation) is old_snapshot

    checkpoint = _admit_continuation(
        _require_compiled_owner(parent).family_identity,
        partial.state,
        partial.continuation,
    )
    assert tuple(record.coordinate.activation.scope_run.scope for record in checkpoint.frames.resume_inputs) == (
        (GraphNodeId("left"),),
    )
    retried = await parent.run(
        state=partial.state,
        continuation=partial.continuation,
        resume=(
            interrupt_resume(
                parent,
                paused,
                "leaf",
                Graph.values(value="right"),
                scope=("right",),
            ),
        ),
    )
    assert isinstance(retried, Graph.CompletedResult)


@pytest.mark.asyncio
async def test_second_scope_frame_install_failure_hands_off_only_the_first_installed_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = Graph[str]("public.multi-scope-frame-install-failure")
    parent.add_node(
        "left",
        interrupt_once_child("public.frame-install.left"),
        inputs={"value": input_ref()},
    )
    parent.add_node(
        "right",
        interrupt_once_child("public.frame-install.right"),
        inputs={"value": input_ref()},
    )
    parent.set_outputs({})
    paused = await parent.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    old_snapshot = _continuation_snapshot(paused.continuation)
    owner = _require_compiled_owner(parent)
    original_install = family_driver_module.project_resume_frames
    transitions: list[Graph.Transition[str]] = []

    def reject_right_install(
        frames: ScopedFrameIndex[str],
        planned: PlannedResume[str],
    ) -> ScopedFrameIndex[str]:
        if planned.scope_run.scope == (GraphNodeId("right"),):
            raise FrameInstallationInvariantError("right frame installation failed")
        return original_install(frames, planned)

    async def record(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        return transition.candidate_state

    monkeypatch.setattr(family_driver_module, "project_resume_frames", reject_right_install)
    try:
        await parent.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                interrupt_resume(
                    parent,
                    paused,
                    "leaf",
                    Graph.values(value="left"),
                    scope=("left",),
                ),
                interrupt_resume(
                    parent,
                    paused,
                    "leaf",
                    Graph.values(value="right"),
                    scope=("right",),
                ),
            ),
            commit=record,
        )
    except Graph.Error as error:
        partial = _require_partial_commit(error)
    else:
        pytest.fail("second scope frame installation failure must explicitly hand off the first scope")

    assert isinstance(partial.cause, FrameInstallationInvariantError)
    assert partial.failed_scope == ("right",)
    assert _continuation_snapshot(paused.continuation) is old_snapshot
    assert tuple(transition.scope for transition in transitions) == (("left",),)
    handed_off = _admit_continuation(owner.family_identity, partial.state, partial.continuation)
    left_input = next(
        record
        for record in handed_off.frames.resume_inputs
        if record.coordinate.activation.scope_run.scope == (GraphNodeId("left"),)
    )
    assert _frame_value(left_input.frame, "value") == "left"
    assert not any(
        record.coordinate.activation.scope_run.scope == (GraphNodeId("right"),)
        for record in handed_off.frames.resume_inputs
    )
    left = next(binding for binding in handed_off.child_states if binding.coordinate.scope == (GraphNodeId("left"),))
    right = next(binding for binding in handed_off.child_states if binding.coordinate.scope == (GraphNodeId("right"),))
    assert left.state == transitions[0].candidate_state
    assert right.state == old_snapshot.child_states[1].state


@pytest.mark.asyncio
async def test_root_resume_then_child_commit_failure_hands_off_a_pairable_latest_root_snapshot() -> None:
    root_attempts = 0

    async def interrupt_root_once(values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal root_attempts
        root_attempts += 1
        if root_attempts == 1:
            return Graph.interrupt(b"root-question")
        return Graph.success(values)

    child = interrupt_once_child("public.root-child-partial.child")
    parent = Graph[str]("public.root-child-partial.parent")
    parent.set_resume_codec("text", 1, encode_text, decode_text)
    parent.add_node(
        "root",
        interrupt_root_once,
        inputs={"value": input_ref()},
        outputs={"value": str},
    )
    parent.add_node(
        "child",
        child,
        inputs={"value": input_ref()},
    )
    parent.set_outputs({})
    paused = await parent.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    old_snapshot = _continuation_snapshot(paused.continuation)
    original = RuntimeError("child commit failed")
    transitions: list[Graph.Transition[str]] = []

    async def fail_child(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        if transition.scope == ("child",):
            raise original
        return transition.candidate_state

    try:
        await parent.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                interrupt_resume(
                    parent,
                    paused,
                    "root",
                    Graph.values(value="root"),
                ),
                interrupt_resume(
                    parent,
                    paused,
                    "leaf",
                    Graph.values(value="child"),
                    scope=("child",),
                ),
            ),
            commit=fail_child,
        )
    except Graph.Error as error:
        partial = _require_partial_commit(error)
    else:
        pytest.fail("child failure after root confirmation must explicitly hand off the latest root snapshot")

    assert partial.cause is original
    assert partial.failed_scope == ("child",)
    assert partial.state == transitions[0].candidate_state
    assert _continuation_snapshot(paused.continuation) is old_snapshot
    owner = _require_compiled_owner(parent)
    handed_off = _admit_continuation(owner.family_identity, partial.state, partial.continuation)
    assert handed_off.root_state == partial.state
    child_binding = next(
        binding for binding in handed_off.child_states if binding.coordinate.scope == (GraphNodeId("child"),)
    )
    old_child = next(
        binding for binding in old_snapshot.child_states if binding.coordinate.scope == (GraphNodeId("child"),)
    )
    assert child_binding.state == old_child.state

    retried = await parent.run(
        state=partial.state,
        continuation=partial.continuation,
        resume=(
            interrupt_resume(
                parent,
                paused,
                "leaf",
                Graph.values(value="child"),
                scope=("child",),
            ),
        ),
    )
    assert isinstance(retried, Graph.CompletedResult)


@pytest.mark.asyncio
async def test_first_resume_scope_failure_propagates_original_error_without_partial_handoff() -> None:
    class FirstScopeCommitError(RuntimeError):
        pass

    attempts = 0

    async def interrupt_once(values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Graph.interrupt(b"question")
        return Graph.success(values)

    graph = Graph[str]("public.first-scope-failure")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node(
        "source",
        interrupt_once,
        inputs={"value": input_ref()},
        outputs={"value": str},
    )
    graph.set_outputs({})
    paused = await graph.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    original = FirstScopeCommitError()

    async def reject(_transition: Graph.Transition[str], /) -> Graph.State:
        raise original

    with pytest.raises(FirstScopeCommitError) as raised:
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                interrupt_resume(
                    graph,
                    paused,
                    "source",
                    Graph.values(value="answer"),
                ),
            ),
            commit=reject,
        )

    assert raised.value is original


@pytest.mark.asyncio
async def test_failure_after_exact_fence_explicitly_hands_off_the_fenced_snapshot() -> None:
    class SecondFenceError(RuntimeError):
        pass

    graph = Graph[str]("public.fence-partial-handoff")
    graph.add_node(
        "left",
        interrupt_once_child("public.fence-partial.left"),
        inputs={"value": input_ref()},
    )
    graph.add_node(
        "right",
        interrupt_once_child("public.fence-partial.right"),
        inputs={"value": input_ref()},
    )
    graph.set_outputs({})
    paused = await graph.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    snapshot = _continuation_snapshot(paused.continuation)
    active_children: list[ChildStateBinding] = []
    for binding in snapshot.child_states:
        pending = replace(
            binding.state,
            frontier=GraphFrontierState(
                tuple(
                    replace(node, settlement=PendingGraphNode(UseStepRequestInput()))
                    for node in binding.state.frontier.nodes
                )
            ),
        )
        active = reduce_graph_run(
            pending,
            project_claim_command(
                pending,
                GraphExecutionAttemptId(f"{binding.coordinate.scope[-1]}-active"),
                None,
            ),
        )
        active_children.append(replace(binding, state=active))
    object.__setattr__(paused.continuation, "_snapshot", replace(snapshot, child_states=tuple(active_children)))
    original = SecondFenceError()
    transitions: list[Graph.Transition[str]] = []

    async def fail_second_fence(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        if transition.scope == ("right",):
            raise original
        return transition.candidate_state

    try:
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            commit=fail_second_fence,
        )
    except Graph.Error as error:
        partial = _require_partial_commit(error)
    else:
        pytest.fail("second fence failure must hand off the first confirmed fence")

    assert [type(transition.command) for transition in transitions] == [FenceGraphExecution, FenceGraphExecution]
    assert partial.cause is original
    assert partial.failed_scope == ("right",)
    owner = _require_compiled_owner(graph)
    handed_off = _admit_continuation(owner.family_identity, partial.state, partial.continuation)
    left = next(binding for binding in handed_off.child_states if binding.coordinate.scope == (GraphNodeId("left"),))
    right = next(binding for binding in handed_off.child_states if binding.coordinate.scope == (GraphNodeId("right"),))
    assert left.state == transitions[0].candidate_state
    assert right.state == active_children[1].state


@pytest.mark.asyncio
async def test_first_fence_failure_propagates_original_error_without_partial_handoff() -> None:
    class FirstFenceError(RuntimeError):
        pass

    async def operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    graph = Graph[str]("public.first-fence-failure")
    graph.add_node("source", operation, inputs={}, outputs={})
    graph.set_outputs({})
    captured: GraphRunState | None = None

    class LoseClaimError(RuntimeError):
        pass

    async def lose_claim(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, ClaimGraphExecution):
            captured = transition.candidate_state
            raise LoseClaimError
        return transition.candidate_state

    with pytest.raises(LoseClaimError):
        await graph.run(Graph.values(), commit=lose_claim)
    assert captured is not None
    original = FirstFenceError()

    async def reject(_transition: Graph.Transition[str], /) -> Graph.State:
        raise original

    with pytest.raises(FirstFenceError) as raised:
        await graph.run(state=captured, commit=reject)

    assert raised.value is original


@pytest.mark.asyncio
async def test_continuation_rejects_a_running_descendant_below_a_terminal_ancestor() -> None:
    async def complete(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    grandchild = Graph[str]("public.orphan-descendant.grandchild")
    grandchild.add_node("leaf", complete, inputs={}, outputs={})
    grandchild.set_outputs({})
    child = Graph[str]("public.orphan-descendant.child")
    child.add_node("grandchild", grandchild, inputs={})
    child.set_outputs({})
    parent = Graph[str]("public.orphan-descendant.parent")
    parent.add_node("child", child, inputs={})
    parent.set_outputs({})
    completed = await parent.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    snapshot = _continuation_snapshot(completed.continuation)
    descendant = next(
        binding
        for binding in snapshot.child_states
        if binding.coordinate.scope == (GraphNodeId("child"), GraphNodeId("grandchild"))
    )
    parent_activation = descendant.state.parent
    assert parent_activation is not None
    compiled = _require_compiled_owner(parent).graph
    compiled_grandchild = compiled.nested_graphs[GraphNodeId("child")].nested_graphs[GraphNodeId("grandchild")]
    running = reduce_graph_run(
        None,
        project_start_graph_command(
            compiled_grandchild,
            descendant.coordinate.graph_run_id,
            parent_activation,
        ),
    )
    leased = reduce_graph_run(
        running,
        project_claim_command(running, GraphExecutionAttemptId("orphan-descendant"), None),
    )
    child_states = tuple(
        replace(binding, state=leased) if binding.coordinate == descendant.coordinate else binding
        for binding in snapshot.child_states
    )
    frames = replace(
        snapshot.frames,
        publications=tuple(
            record
            for record in snapshot.frames.publications
            if record.coordinate.activation.scope_run != descendant.coordinate
        ),
        resume_inputs=tuple(
            record
            for record in snapshot.frames.resume_inputs
            if record.coordinate.activation.scope_run != descendant.coordinate
        ),
        child_boundaries=tuple(
            record
            for record in snapshot.frames.child_boundaries
            if record.coordinate.child_scope_run != descendant.coordinate
        ),
    )
    object.__setattr__(
        completed.continuation,
        "_snapshot",
        replace(snapshot, child_states=child_states, frames=frames),
    )
    transitions: list[Graph.Transition[str]] = []

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        return transition.candidate_state

    with pytest.raises(Graph.SnapshotMismatchError, match="running child lineage"):
        await parent.run(
            state=completed.state,
            continuation=completed.continuation,
            commit=commit,
        )

    assert transitions == []


@pytest.mark.asyncio
async def test_normal_resume_never_mutates_the_input_continuation_snapshot() -> None:
    attempts = 0

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Graph.interrupt(b"question")
        return Graph.success(Graph.values())

    graph = Graph[str]("public.immutable-input-continuation")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node(
        "source",
        interrupt_once,
        inputs={"value": input_ref()},
        outputs={},
    )
    graph.set_outputs({})
    paused = await graph.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    old_snapshot = _continuation_snapshot(paused.continuation)

    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            interrupt_resume(
                graph,
                paused,
                "source",
                Graph.values(value="answer"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert _continuation_snapshot(paused.continuation) is old_snapshot
    assert completed.continuation is not paused.continuation
    owner = _require_compiled_owner(graph)
    restored = _admit_continuation(owner.family_identity, paused.state, paused.continuation)
    assert restored.root_state == paused.state


@pytest.mark.asyncio
async def test_shared_input_continuation_is_not_modified_by_independent_invocations() -> None:
    attempts = 0

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Graph.interrupt(b"question")
        return Graph.success(Graph.values())

    graph = Graph[str]("public.shared-immutable-continuation")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node(
        "source",
        interrupt_once,
        inputs={"value": input_ref()},
        outputs={},
    )
    graph.set_outputs({})
    paused = await graph.run(Graph.values(value="seed"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    old_snapshot = _continuation_snapshot(paused.continuation)

    first, second = await asyncio.gather(
        graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                interrupt_resume(
                    graph,
                    paused,
                    "source",
                    Graph.values(value="first"),
                ),
            ),
        ),
        graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                interrupt_resume(
                    graph,
                    paused,
                    "source",
                    Graph.values(value="second"),
                ),
            ),
        ),
    )

    assert isinstance(first, Graph.CompletedResult)
    assert isinstance(second, Graph.CompletedResult)
    assert _continuation_snapshot(paused.continuation) is old_snapshot
    assert first.continuation is not second.continuation


@pytest.mark.asyncio
async def test_interrupt_resume_is_an_exact_action_inside_run() -> None:
    calls = 0

    async def interrupt_once(values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Graph.interrupt(b"approve?")
        return Graph.success(values)

    graph = Graph[str]("public.interrupt")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node("review", interrupt_once, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({"value": Graph.node_output("review", "value")})

    interrupted = await graph.run(Graph.values(value="draft"), run_id="interrupt-run")

    assert isinstance(interrupted, Graph.AwaitingResumeResult)
    assert len(interrupted.interrupts) == 1
    view = interrupted.interrupts[0]
    with pytest.raises(Graph.SnapshotMismatchError, match="does not match"):
        await graph.run(
            state=interrupted.state,
            continuation=interrupted.continuation,
            resume=(graph.resume_interrupted("review", "stale", Graph.values(value="approved")),),
        )

    action = graph.resume_interrupted(
        "review",
        view.interrupt_id,
        Graph.values(value="approved"),
    )
    with pytest.raises(Graph.SnapshotMismatchError, match="duplicated"):
        await graph.run(
            state=interrupted.state,
            continuation=interrupted.continuation,
            resume=(action, action),
        )

    resumed = await graph.run(
        state=interrupted.state,
        continuation=interrupted.continuation,
        resume=(action,),
    )

    assert isinstance(resumed, Graph.CompletedResult)
    assert resumed.outputs["value"] == "approved"


@pytest.mark.asyncio
async def test_run_rejects_a_continuation_bound_to_another_root_state() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    graph = Graph[str]("public.invalid-resume")
    graph.add_node("node", fail, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({})
    first = await graph.run(Graph.values(value="first"), run_id="first-run")
    second = await graph.run(Graph.values(value="second"), run_id="second-run")
    assert isinstance(first, Graph.FailedResult)
    assert isinstance(second, Graph.FailedResult)
    with pytest.raises(Graph.SnapshotMismatchError, match="same compiled graph lineage"):
        await graph.run(
            state=first.state,
            continuation=second.continuation,
        )


@pytest.mark.asyncio
async def test_run_rejects_resume_without_state_and_on_a_terminal_failed_run() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    graph = Graph[str]("public.invalid-resume-variants")
    graph.add_node(
        "node",
        fail,
        inputs={"value": input_ref()},
        outputs={"value": str},
    )
    graph.set_outputs({})
    run = cast(_RuntimeRun, graph.run)

    with pytest.raises(Graph.SnapshotMismatchError, match="new graph run cannot carry"):
        await run(
            Graph.values(value="input"),
            resume=(
                graph.resume_interrupted(
                    "node",
                    "interrupt",
                    Graph.values(value="answer"),
                ),
            ),
        )

    failed = await graph.run(Graph.values(value="input"))
    assert isinstance(failed, Graph.FailedResult)
    with pytest.raises(Graph.SnapshotMismatchError, match="quiescent running"):
        await graph.run(
            state=failed.state,
            continuation=failed.continuation,
            resume=(
                graph.resume_interrupted(
                    "node",
                    "not-an-interrupt",
                    Graph.values(value="answer"),
                ),
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_supersteps", "max_parallel_tasks"),
    [(0, 64), (-1, 64), (1_000, 0), (1_000, -1)],
    ids=("zero-supersteps", "negative-supersteps", "zero-parallel", "negative-parallel"),
)
async def test_invalid_limits_reject_a_new_run_before_compilation_or_commit(
    max_supersteps: int,
    max_parallel_tasks: int,
) -> None:
    calls = 0

    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        return values

    graph = Graph[str]("public.invalid-limits.new")
    graph.add_node("node", echo, inputs={"value": input_ref()}, outputs={"value": str})
    commits = CommitLog()

    with pytest.raises(Graph.ExecutionLimitError, match="exact positive"):
        await graph.run(
            Graph.values(value="input"),
            commit=commits,
            max_supersteps=max_supersteps,
            max_parallel_tasks=max_parallel_tasks,
        )

    assert commits.transitions == []
    assert calls == 0
    assert graph.set_outputs({}) is graph


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_supersteps", "max_parallel_tasks"),
    [(0, 64), (-1, 64), (1_000, 0), (1_000, -1)],
    ids=("zero-supersteps", "negative-supersteps", "zero-parallel", "negative-parallel"),
)
async def test_invalid_limits_reject_active_recovery_before_fence_or_execution(
    max_supersteps: int,
    max_parallel_tasks: int,
) -> None:
    calls = 0
    active_state: GraphRunState | None = None

    async def echo(_values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        return Graph.values()

    async def capture_claim(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal active_state
        if isinstance(transition.command, ClaimGraphExecution):
            active_state = transition.candidate_state
            raise CommitAcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str]("public.invalid-limits.active")
    graph.add_node("node", echo, inputs={}, outputs={})
    graph.set_outputs({})
    with pytest.raises(CommitAcknowledgementLostError):
        await graph.run(Graph.values(), run_id="active-run", commit=capture_claim)
    assert active_state is not None and active_state.execution is not None
    before = active_state
    commits = CommitLog()

    with pytest.raises(Graph.ExecutionLimitError, match="exact positive"):
        await graph.run(
            state=active_state,
            commit=commits,
            max_supersteps=max_supersteps,
            max_parallel_tasks=max_parallel_tasks,
        )

    assert commits.transitions == []
    assert active_state == before
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_supersteps", "max_parallel_tasks"),
    [(0, 64), (-1, 64), (1_000, 0), (1_000, -1)],
    ids=("zero-supersteps", "negative-supersteps", "zero-parallel", "negative-parallel"),
)
async def test_invalid_limits_reject_resume_before_consuming_the_settlement(
    max_supersteps: int,
    max_parallel_tasks: int,
) -> None:
    calls = 0

    async def interrupt(values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.interrupt(f"question:{values['value']}".encode())

    graph = Graph[str]("public.invalid-limits.resume")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node("node", interrupt, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({})
    paused = await graph.run(Graph.values(value="first"), run_id="resume-run")
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ExecutionLimitError, match="exact positive"):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                interrupt_resume(
                    graph,
                    paused,
                    "node",
                    Graph.values(value="answer"),
                ),
            ),
            commit=commits,
            max_supersteps=max_supersteps,
            max_parallel_tasks=max_parallel_tasks,
        )

    assert commits.transitions == []
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_confirmation", ["wrong-type", "wrong-revision"])
async def test_run_requires_exact_authoritative_commit_confirmation(
    wrong_confirmation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    graph = Graph[str]("public.commit-mismatch")
    graph.add_node("node", echo, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({})

    seen: list[Graph.Transition[str]] = []

    async def reject(transition: Graph.Transition[str], /) -> Graph.State:
        seen.append(transition)
        if wrong_confirmation == "wrong-type":
            return cast(GraphRunState, "not-state")
        return replace(transition.candidate_state, revision=transition.candidate_state.revision + 1)

    installed = 0
    original_add_graph_input = cast(
        Callable[[ScopedFrameIndex[str], AdmittedGraphInput[str]], ScopedFrameIndex[str]],
        ScopedFrameIndex[str].add_graph_input,
    )

    def record_graph_input(
        frames: ScopedFrameIndex[str],
        record: AdmittedGraphInput[str],
    ) -> ScopedFrameIndex[str]:
        nonlocal installed
        installed += 1
        return original_add_graph_input(frames, record)

    monkeypatch.setattr(ScopedFrameIndex, "add_graph_input", record_graph_input)

    with pytest.raises(Graph.SnapshotMismatchError, match="exact authoritative"):
        await graph.run(Graph.values(value="input"), commit=reject)
    assert len(seen) == 1
    assert len(seen[0].writes.graph_inputs) == 1
    assert installed == 0


class CommitAcknowledgementLostError(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_run_fences_an_authoritative_unacknowledged_claim_before_recovery() -> None:
    calls = 0
    captured: GraphRunState | None = None

    async def echo(_values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        return Graph.values()

    async def lose_claim_ack(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, ClaimGraphExecution):
            captured = transition.candidate_state
            raise CommitAcknowledgementLostError
        return transition.candidate_state

    graph = Graph[str]("public.claim-recovery")
    graph.add_node("node", echo, inputs={}, outputs={})
    graph.set_outputs({})
    with pytest.raises(CommitAcknowledgementLostError):
        await graph.run(Graph.values(), run_id="recover-run", commit=lose_claim_ack)

    assert captured is not None and captured.execution is not None
    recovered = await graph.run(state=captured)
    assert isinstance(recovered, Graph.CompletedResult)
    assert calls == 1
    with pytest.raises(Graph.SnapshotMismatchError, match="forbid run_id"):
        await graph.run(state=recovered.state, run_id="wrong-run")


@pytest.mark.asyncio
async def test_waiter_cancellation_aborts_standalone_root_after_quiescence() -> None:
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def wait(_values: Graph.Values[str]) -> Graph.Values[str]:
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cleaned.set()
        return Graph.values()

    graph = Graph[str]("public.waiter-cancellation")
    graph.add_node("node", wait, inputs={}, outputs={})
    graph.set_outputs({})
    commits = CommitLog()
    running = asyncio.create_task(graph.run(Graph.values(), commit=commits))
    await entered.wait()

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert cleaned.is_set()
    assert isinstance(commits.transitions[-2].command, FenceGraphExecution)
    assert isinstance(commits.transitions[-1].command, AbortGraphRun)
    assert commits.transitions[-1].scope == ()


@pytest.mark.asyncio
async def test_waiter_cancellation_preserves_a_committed_sibling_settlement() -> None:
    slow_started = asyncio.Event()
    slow_cleaned = asyncio.Event()
    fast_committed = asyncio.Event()
    never = asyncio.Event()
    transitions: list[Graph.Transition[str]] = []

    async def fast(_values: Graph.Values[str]) -> Graph.Values[str]:
        await slow_started.wait()
        return Graph.values()

    async def slow(_values: Graph.Values[str]) -> Graph.Values[str]:
        slow_started.set()
        try:
            await never.wait()
        finally:
            slow_cleaned.set()
        return Graph.values()

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        if isinstance(transition.command, SettleGraphNode) and transition.command.outcome.node_id == GraphNodeId(
            "fast"
        ):
            fast_committed.set()
        return transition.candidate_state

    graph = Graph[str]("public.partial-settlement-cancellation")
    graph.add_node("fast", fast, inputs={}, outputs={})
    graph.add_node("slow", slow, inputs={}, outputs={})
    graph.set_outputs({})
    running = asyncio.create_task(graph.run(Graph.values(), commit=commit))
    await asyncio.wait_for(fast_committed.wait(), timeout=1)

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert slow_cleaned.is_set()
    assert isinstance(transitions[-2].command, FenceGraphExecution)
    assert isinstance(transitions[-1].command, AbortGraphRun)
    fast_frontier = next(
        node for node in transitions[-1].candidate_state.frontier.nodes if node.node_id == GraphNodeId("fast")
    )
    assert isinstance(fast_frontier.settlement, SucceededGraphNode)


@pytest.mark.asyncio
async def test_root_node_origin_cancellation_rethrows_without_invocation_abort() -> None:
    async def cancel(_values: Graph.Values[str]) -> Graph.Values[str]:
        raise asyncio.CancelledError

    graph = Graph[str]("public.root-node-cancellation")
    graph.add_node("node", cancel, inputs={}, outputs={})
    graph.set_outputs({})
    commits = CommitLog()

    with pytest.raises(asyncio.CancelledError):
        await graph.run(Graph.values(), commit=commits)

    assert tuple(type(transition.command) for transition in commits.transitions) == (
        StartGraphRun,
        ClaimGraphExecution,
    )
    assert commits.transitions[-1].candidate_state.execution is not None
    assert not any(isinstance(transition.command, FenceGraphExecution) for transition in commits.transitions)
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in commits.transitions)


@pytest.mark.asyncio
async def test_nested_node_origin_cancellation_becomes_a_typed_parent_failure() -> None:
    async def cancel(_values: Graph.Values[str]) -> Graph.Values[str]:
        raise asyncio.CancelledError

    child = Graph[str]("public.nested-node-cancellation.child")
    child.add_node("leaf", cancel, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.nested-node-cancellation.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    commits = CommitLog()

    result = await parent.run(Graph.values(), commit=commits)

    assert isinstance(result, Graph.FailedResult)
    assert tuple((failure.scope, failure.node_id, failure.failure) for failure in result.failures) == (
        ((), "nested", "nested graph node was cancelled"),
    )
    abort_scopes = tuple(
        transition.scope for transition in commits.transitions if isinstance(transition.command, AbortGraphRun)
    )
    assert abort_scopes == (("nested",),)


@pytest.mark.asyncio
async def test_root_node_origin_cancellation_preserves_active_child_lease() -> None:
    child_started = asyncio.Event()
    never = asyncio.Event()
    child_cleaned = asyncio.Event()
    original = asyncio.CancelledError("root node cancelled")
    authoritative: dict[tuple[str, ...], Graph.State] = {}
    transitions: list[Graph.Transition[str]] = []

    async def child_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        child_started.set()
        try:
            await never.wait()
        finally:
            child_cleaned.set()
        return Graph.values()

    async def parent_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        await child_started.wait()
        raise original

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        authoritative[transition.scope] = transition.candidate_state
        return transition.candidate_state

    child = Graph[str]("public.root-node-cancellation-family.child")
    child.add_node("leaf", child_operation, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.root-node-cancellation-family.parent")
    parent.add_node("child", child, inputs={})
    parent.add_node("ordinary", parent_operation, inputs={}, outputs={})
    parent.set_outputs({})

    with pytest.raises(asyncio.CancelledError) as raised:
        await asyncio.wait_for(parent.run(Graph.values(), commit=commit, max_parallel_tasks=2), timeout=1)

    assert raised.value is original
    assert child_cleaned.is_set()
    assert authoritative[()].execution is not None
    assert authoritative[("child",)].execution is not None
    assert not any(isinstance(transition.command, FenceGraphExecution) for transition in transitions)
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in transitions)


@pytest.mark.asyncio
async def test_root_owner_setup_failure_aborts_the_confirmed_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RootSetupError(RuntimeError):
        pass

    original = RootSetupError("root frame installation failed")

    def reject_input(
        _frames: ScopedFrameIndex[str],
        _record: AdmittedGraphInput[str],
    ) -> ScopedFrameIndex[str]:
        raise original

    async def complete(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    monkeypatch.setattr(ScopedFrameIndex, "add_graph_input", reject_input)
    graph = Graph[str]("public.root-owner-setup-failure")
    graph.add_node("node", complete, inputs={}, outputs={})
    graph.set_outputs({})
    commits = CommitLog()

    with pytest.raises(RootSetupError) as raised:
        await graph.run(Graph.values(), commit=commits)

    assert raised.value is original
    assert isinstance(commits.transitions[0].command, StartGraphRun)
    assert isinstance(commits.transitions[-1].command, AbortGraphRun)
    assert commits.transitions[-1].scope == ()


@pytest.mark.asyncio
async def test_root_owner_construction_failure_aborts_the_confirmed_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RootConstructionError(RuntimeError):
        pass

    original = RootConstructionError("root owner construction failed")

    fail_owner_construction(monkeypatch, original, scope_depth=0)

    async def complete(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    graph = Graph[str]("public.root-owner-construction-failure")
    graph.add_node("node", complete, inputs={}, outputs={})
    graph.set_outputs({})
    commits = CommitLog()

    with pytest.raises(RootConstructionError) as raised:
        await graph.run(Graph.values(), commit=commits)

    assert raised.value is original
    assert isinstance(commits.transitions[0].command, StartGraphRun)
    assert isinstance(commits.transitions[-1].command, AbortGraphRun)
    assert commits.transitions[-1].scope == ()


@pytest.mark.asyncio
async def test_continued_root_construction_failure_aborts_the_admitted_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RootConstructionError(RuntimeError):
        pass

    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    graph = Graph[str]("public.continued-root-construction-failure")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("node", interrupt, inputs={}, outputs={})
    graph.set_outputs({})
    awaiting = await graph.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    original = RootConstructionError("continued root owner construction failed")

    fail_owner_construction(monkeypatch, original, scope_depth=0)
    commits = CommitLog()

    with pytest.raises(RootConstructionError) as raised:
        await graph.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            commit=commits,
        )

    assert raised.value is original
    assert tuple(
        transition.scope for transition in commits.transitions if isinstance(transition.command, AbortGraphRun)
    ) == ((),)


@pytest.mark.asyncio
async def test_child_owner_setup_failure_aborts_only_the_confirmed_child_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChildSetupError(RuntimeError):
        pass

    original_add = ScopedFrameIndex[str].add_graph_input
    original = ChildSetupError("child frame installation failed")
    calls = 0

    def reject_child_input(
        frames: ScopedFrameIndex[str],
        record: AdmittedGraphInput[str],
    ) -> ScopedFrameIndex[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise original
        return original_add(frames, record)

    monkeypatch.setattr(ScopedFrameIndex, "add_graph_input", reject_child_input)

    async def complete(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    child = Graph[str]("public.child-owner-setup-failure.child")
    child.add_node("leaf", complete, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.child-owner-setup-failure.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    commits = CommitLog()

    with pytest.raises(ChildSetupError) as raised:
        await parent.run(Graph.values(), commit=commits)

    assert raised.value is original
    abort_scopes = tuple(
        transition.scope for transition in commits.transitions if isinstance(transition.command, AbortGraphRun)
    )
    assert abort_scopes == (("nested",),)


@pytest.mark.asyncio
async def test_child_owner_construction_failure_aborts_only_the_confirmed_child_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChildConstructionError(RuntimeError):
        pass

    original = ChildConstructionError("child owner construction failed")
    fail_owner_construction(monkeypatch, original, scope_depth=1)

    async def complete(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    child = Graph[str]("public.child-owner-construction-failure.child")
    child.add_node("leaf", complete, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.child-owner-construction-failure.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    commits = CommitLog()

    with pytest.raises(ChildConstructionError) as raised:
        await parent.run(Graph.values(), commit=commits)

    assert raised.value is original
    assert tuple(
        transition.scope for transition in commits.transitions if isinstance(transition.command, AbortGraphRun)
    ) == (("nested",),)


@pytest.mark.asyncio
async def test_continued_child_construction_failure_aborts_child_then_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChildConstructionError(RuntimeError):
        pass

    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    child = Graph[str]("public.continued-child-construction-failure.child")
    child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    child.add_node("leaf", interrupt, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.continued-child-construction-failure.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    awaiting = await parent.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    original = ChildConstructionError("continued child owner construction failed")
    fail_owner_construction(monkeypatch, original, scope_depth=1)
    commits = CommitLog()

    with pytest.raises(ChildConstructionError) as raised:
        await parent.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            commit=commits,
        )

    assert raised.value is original
    assert tuple(
        transition.scope for transition in commits.transitions if isinstance(transition.command, AbortGraphRun)
    ) == (("nested",), ())


@pytest.mark.asyncio
async def test_continued_root_construction_failure_aborts_only_root_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RootConstructionError(RuntimeError):
        pass

    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    child = Graph[str]("public.continued-root-child-cleanup.child")
    child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    child.add_node("leaf", interrupt, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.continued-root-child-cleanup.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    awaiting = await parent.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    original = RootConstructionError("continued root owner construction failed")

    fail_owner_construction(monkeypatch, original, scope_depth=0)
    commits = CommitLog()

    with pytest.raises(RootConstructionError) as raised:
        await parent.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            commit=commits,
        )

    assert raised.value is original
    assert tuple(
        transition.scope for transition in commits.transitions if isinstance(transition.command, AbortGraphRun)
    ) == ((),)


@pytest.mark.asyncio
async def test_child_exact_completion_survives_parent_settlement_failure() -> None:
    class ParentSettlementError(RuntimeError):
        pass

    async def complete(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    child = Graph[str]("public.parent-settlement-failure.child")
    child.add_node("leaf", complete, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.parent-settlement-failure.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    original = ParentSettlementError("parent settlement failed")
    transitions: list[Graph.Transition[str]] = []

    async def reject_parent_settlement(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        if transition.scope == () and isinstance(transition.command, SettleGraphNode):
            raise original
        return transition.candidate_state

    with pytest.raises(ParentSettlementError) as raised:
        await parent.run(Graph.values(), commit=reject_parent_settlement)

    assert raised.value is original
    assert any(
        transition.scope == ("nested",) and isinstance(transition.command, CompleteGraphFrontier)
        for transition in transitions
    )
    assert not any(
        transition.scope == ("nested",) and isinstance(transition.command, AbortGraphRun) for transition in transitions
    )


@pytest.mark.asyncio
async def test_node_exception_closes_and_fences_before_propagation() -> None:
    should_fail = True

    async def operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        if should_fail:
            raise ValueError("node failed")
        return Graph.values()

    graph = Graph[str]("public.node-error")
    graph.add_node("node", operation, inputs={}, outputs={})
    graph.set_outputs({})
    commits = CommitLog()
    with pytest.raises(ValueError, match="node failed"):
        await graph.run(Graph.values(), run_id="error-run", commit=commits)

    assert isinstance(commits.transitions[-1].command, FenceGraphExecution)
    fenced = commits.transitions[-1].candidate_state
    assert fenced.execution is None
    should_fail = False
    recovered = await graph.run(state=fenced)
    assert isinstance(recovered, Graph.CompletedResult)


@pytest.mark.asyncio
async def test_session_creation_error_fences_the_committed_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    def fail_issue_session(
        self: GraphExecutor[str],
        claim: PreparedExecutionClaim[str],
        state: GraphRunState,
    ) -> GraphExecutionSession[str]:
        del self, claim, state
        raise RuntimeError("session creation failed")

    monkeypatch.setattr(GraphExecutor, "issue_session", fail_issue_session)
    graph = Graph[str]("public.session-error")
    graph.add_node("node", echo, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({})
    commits = CommitLog()

    with pytest.raises(RuntimeError, match="session creation"):
        await graph.run(Graph.values(value="input"), commit=commits)

    assert isinstance(commits.transitions[-1].command, FenceGraphExecution)
    assert commits.transitions[-1].candidate_state.execution is None


@pytest.mark.asyncio
async def test_facade_drives_nested_graph_through_the_same_execution_owner() -> None:
    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    child = Graph[str]("public.child")
    child.add_node("leaf", echo, inputs={"value": input_ref()}, outputs={"value": str})
    child.set_outputs({"value": Graph.node_output("leaf", "value")})
    parent = Graph[str]("public.parent")
    parent.add_node("child", child, inputs={"value": input_ref()})
    parent.set_outputs({"value": Graph.node_output("child", "value")})
    commits = CommitLog()

    result = await parent.run(Graph.values(value="nested"), commit=commits)

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "nested"
    child_starts = tuple(
        transition
        for transition in commits.transitions
        if transition.scope == ("child",) and isinstance(transition.command, StartGraphRun)
    )
    assert len(child_starts) == 1
    assert len(child_starts[0].writes.graph_inputs) == 1
    assert child_starts[0].writes.graph_inputs[0].coordinate.scope_run.scope == ("child",)


@pytest.mark.asyncio
async def test_parent_worker_failure_fences_active_descendants_without_terminalizing_them() -> None:
    grandchild_started = asyncio.Event()
    grandchild_cleaned = asyncio.Event()
    child_started = asyncio.Event()
    child_cleaned = asyncio.Event()
    never = asyncio.Event()
    original = RuntimeError("parent ordinary failure")
    authoritative: dict[tuple[str, ...], Graph.State] = {}
    transitions: list[Graph.Transition[str]] = []

    async def grandchild_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        grandchild_started.set()
        try:
            await never.wait()
        finally:
            grandchild_cleaned.set()
        return Graph.values()

    async def parent_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        await child_started.wait()
        await grandchild_started.wait()
        raise original

    async def child_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        child_started.set()
        await grandchild_started.wait()
        try:
            await never.wait()
        finally:
            child_cleaned.set()
        return Graph.values()

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        authoritative[transition.scope] = transition.candidate_state
        return transition.candidate_state

    grandchild = Graph[str]("public.fan-in-failure.parent-child-grandchild")
    grandchild.add_node("leaf", grandchild_operation, inputs={}, outputs={})
    grandchild.set_outputs({})
    child = Graph[str]("public.fan-in-failure.parent-child")
    child.add_node("grandchild", grandchild, inputs={})
    child.add_node("ordinary", child_operation, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.fan-in-failure.parent")
    parent.add_node("child", child, inputs={})
    parent.add_node("ordinary", parent_operation, inputs={}, outputs={})
    parent.set_outputs({})

    with pytest.raises(RuntimeError) as raised:
        await asyncio.wait_for(parent.run(Graph.values(), commit=commit), timeout=1)

    assert raised.value is original
    assert grandchild_cleaned.is_set()
    assert child_cleaned.is_set()
    expected_scopes = ((), ("child",), ("child", "grandchild"))
    for scope in expected_scopes:
        assert any(
            transition.scope == scope and isinstance(transition.command, ClaimGraphExecution)
            for transition in transitions
        )
        assert authoritative[scope].execution is None
        assert authoritative[scope].status is GraphRunStatus.RUNNING
    assert {transition.scope for transition in transitions if isinstance(transition.command, FenceGraphExecution)} == {
        (),
        ("child",),
        ("child", "grandchild"),
    }
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in transitions)


@pytest.mark.asyncio
async def test_child_worker_failure_fences_an_active_parent_without_terminalizing_it() -> None:
    child_started = asyncio.Event()
    parent_started = asyncio.Event()
    parent_cleaned = asyncio.Event()
    never = asyncio.Event()
    original = RuntimeError("child ordinary failure")
    authoritative: dict[tuple[str, ...], Graph.State] = {}
    transitions: list[Graph.Transition[str]] = []

    async def child_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        child_started.set()
        raise original

    async def parent_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        parent_started.set()
        try:
            await child_started.wait()
            await never.wait()
        finally:
            parent_cleaned.set()
        return Graph.values()

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        authoritative[transition.scope] = transition.candidate_state
        return transition.candidate_state

    child = Graph[str]("public.fan-in-failure.child-error")
    child.add_node("leaf", child_operation, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.fan-in-failure.child-parent")
    parent.add_node("child", child, inputs={})
    parent.add_node("ordinary", parent_operation, inputs={}, outputs={})
    parent.set_outputs({})

    with pytest.raises(RuntimeError) as raised:
        await asyncio.wait_for(
            parent.run(Graph.values(), commit=commit, max_parallel_tasks=2),
            timeout=1,
        )

    assert raised.value is original
    assert parent_started.is_set()
    assert child_started.is_set()
    assert parent_cleaned.is_set()
    assert any(
        transition.scope == ("child",) and isinstance(transition.command, ClaimGraphExecution)
        for transition in transitions
    )
    assert any(
        transition.scope == () and isinstance(transition.command, ClaimGraphExecution) for transition in transitions
    )
    assert authoritative[()].execution is None
    assert authoritative[("child",)].execution is None
    assert authoritative[()].status is GraphRunStatus.RUNNING
    assert authoritative[("child",)].status is GraphRunStatus.RUNNING
    assert {transition.scope for transition in transitions if isinstance(transition.command, FenceGraphExecution)} == {
        (),
        ("child",),
    }
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in transitions)


@pytest.mark.asyncio
async def test_child_worker_failure_fences_a_sibling_child_without_terminalizing_it() -> None:
    sibling_started = asyncio.Event()
    sibling_cleaned = asyncio.Event()
    never = asyncio.Event()
    original = RuntimeError("child sibling failure")
    authoritative: dict[tuple[str, ...], Graph.State] = {}
    transitions: list[Graph.Transition[str]] = []

    async def failing_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        await sibling_started.wait()
        raise original

    async def sibling_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        sibling_started.set()
        try:
            await never.wait()
        finally:
            sibling_cleaned.set()
        return Graph.values()

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        authoritative[transition.scope] = transition.candidate_state
        return transition.candidate_state

    failing = Graph[str]("public.fan-in-failure.sibling-failing")
    failing.add_node("leaf", failing_operation, inputs={}, outputs={})
    failing.set_outputs({})
    sibling = Graph[str]("public.fan-in-failure.sibling-blocked")
    sibling.add_node("leaf", sibling_operation, inputs={}, outputs={})
    sibling.set_outputs({})
    parent = Graph[str]("public.fan-in-failure.sibling-parent")
    parent.add_node("failing", failing, inputs={})
    parent.add_node("sibling", sibling, inputs={})
    parent.set_outputs({})

    with pytest.raises(RuntimeError) as raised:
        await asyncio.wait_for(
            parent.run(Graph.values(), commit=commit, max_parallel_tasks=2),
            timeout=1,
        )

    assert raised.value is original
    assert sibling_cleaned.is_set()
    for scope in (("failing",), ("sibling",)):
        assert authoritative[scope].execution is None
        assert authoritative[scope].status is GraphRunStatus.RUNNING
    assert {transition.scope for transition in transitions if isinstance(transition.command, FenceGraphExecution)} == {
        ("failing",),
        ("sibling",),
    }
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in transitions)


@pytest.mark.asyncio
async def test_commit_origin_cancellation_preserves_active_family_leases() -> None:
    child_started = asyncio.Event()
    never = asyncio.Event()
    child_cleaned = asyncio.Event()
    original = asyncio.CancelledError("commit-origin cancellation")
    authoritative: dict[tuple[str, ...], Graph.State] = {}
    transitions: list[Graph.Transition[str]] = []

    async def child_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        child_started.set()
        try:
            await never.wait()
        finally:
            child_cleaned.set()
        return Graph.values()

    async def parent_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        await child_started.wait()
        return Graph.values()

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        authoritative[transition.scope] = transition.candidate_state
        if (
            transition.scope == ()
            and isinstance(transition.command, SettleGraphNode)
            and transition.writes.settlement is not None
            and transition.writes.settlement.node_id == "ordinary"
        ):
            raise original
        return transition.candidate_state

    child = Graph[str]("public.commit-origin-family.child")
    child.add_node("leaf", child_operation, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.commit-origin-family.parent")
    parent.add_node("child", child, inputs={})
    parent.add_node("ordinary", parent_operation, inputs={}, outputs={})
    parent.set_outputs({})

    with pytest.raises(asyncio.CancelledError) as raised:
        await asyncio.wait_for(parent.run(Graph.values(), commit=commit, max_parallel_tasks=2), timeout=1)

    assert raised.value is original
    assert child_cleaned.is_set()
    assert authoritative[()].execution is not None
    assert authoritative[("child",)].execution is not None
    assert not any(isinstance(transition.command, FenceGraphExecution) for transition in transitions)
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in transitions)


@pytest.mark.asyncio
async def test_failed_result_views_preserve_canonical_root_to_child_scope_order() -> None:
    async def fail_root(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("root-failed")

    async def interrupt_root(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"root")

    def failed_child(name: str) -> Graph[str]:
        async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
            return Graph.failure(f"{name}-failed")

        child = Graph[str](f"public.failed-views.{name}")
        child.add_node("failed", fail, inputs={}, outputs={})
        child.set_outputs({})
        return child

    parent = Graph[str]("public.failed-views")
    parent.set_resume_codec("empty", 1, encode_empty, decode_empty)
    parent.add_node("right", failed_child("right"), inputs={})
    parent.add_node("left", failed_child("left"), inputs={})
    parent.add_node("root-failed", fail_root, inputs={}, outputs={})
    parent.add_node("root-interrupted", interrupt_root, inputs={}, outputs={})
    parent.set_outputs({})

    result = await parent.run(Graph.values())

    assert isinstance(result, Graph.FailedResult)
    with pytest.raises(Graph.Error, match="family driver"):
        replace(result, _seal=1)
    assert tuple((view.scope, view.node_id, view.failure) for view in result.failures) == (
        ((), "left", "left-failed"),
        ((), "right", "right-failed"),
        ((), "root-failed", "root-failed"),
        (("left",), "failed", "left-failed"),
        (("right",), "failed", "right-failed"),
    )
    assert tuple((view.scope, view.node_id, view.request_payload) for view in result.interrupts) == (
        ((), "root-interrupted", b"root"),
    )


@pytest.mark.asyncio
async def test_failed_child_cleans_up_awaiting_child_only_after_pending_siblings_settle() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("child failed")

    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    calls: list[str] = []

    async def ordinary(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("ordinary")
        return Graph.values()

    async def resource(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("resource")
        return Graph.values()

    failed_child = Graph[str]("public.failed-priority.failed-child")
    failed_child.add_node("leaf", fail, inputs={}, outputs={})
    failed_child.set_outputs({})
    awaiting_child = Graph[str]("public.failed-priority.awaiting-child")
    awaiting_child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    awaiting_child.add_node("leaf", interrupt, inputs={}, outputs={})
    awaiting_child.set_outputs({})
    parent = Graph[str]("public.failed-priority")
    parent.add_node("failed", failed_child, inputs={})
    parent.add_node("ordinary", ordinary, inputs={}, outputs={})
    parent.add_node("resource", resource, inputs={}, outputs={}, resources=("file",))
    parent.add_node("waiting", awaiting_child, inputs={})
    parent.set_outputs({})
    commits = CommitLog()

    result = await parent.run(
        Graph.values(),
        commit=commits,
        max_parallel_tasks=1,
    )

    assert isinstance(result, Graph.FailedResult)
    assert calls == ["ordinary", "resource"]
    assert tuple((view.scope, view.node_id, view.failure) for view in result.failures) == (
        ((), "failed", "child failed"),
        ((), "waiting", "nested graph was superseded by a sibling failure"),
        (("failed",), "leaf", "child failed"),
    )
    assert tuple((view.scope, view.node_id, view.request_payload) for view in result.interrupts) == (
        (("waiting",), "leaf", b"question"),
    )
    settlement_order = tuple(
        GraphNodeId(transition.writes.settlement.node_id)
        for transition in commits.transitions
        if (
            transition.scope == ()
            and isinstance(transition.command, SettleGraphNode)
            and transition.writes.settlement is not None
        )
    )
    assert settlement_order.index(GraphNodeId("ordinary")) < settlement_order.index(GraphNodeId("waiting"))
    assert settlement_order.index(GraphNodeId("resource")) < settlement_order.index(GraphNodeId("waiting"))


@pytest.mark.asyncio
async def test_ordinary_failure_cleans_up_awaiting_child_at_terminal_failure() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("ordinary failed")

    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    awaiting_child = Graph[str]("public.ordinary-failed-priority.awaiting-child")
    awaiting_child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    awaiting_child.add_node("leaf", interrupt, inputs={}, outputs={})
    awaiting_child.set_outputs({})
    parent = Graph[str]("public.ordinary-failed-priority")
    parent.add_node("failed", fail, inputs={}, outputs={})
    parent.add_node("waiting", awaiting_child, inputs={})
    parent.set_outputs({})
    commits = CommitLog()

    result = await parent.run(Graph.values(), commit=commits, max_parallel_tasks=1)

    assert isinstance(result, Graph.FailedResult)
    assert tuple((view.scope, view.node_id, view.failure) for view in result.failures) == (
        ((), "failed", "ordinary failed"),
        ((), "waiting", "nested graph was superseded by a sibling failure"),
    )
    assert tuple((view.scope, view.node_id, view.request_payload) for view in result.interrupts) == (
        (("waiting",), "leaf", b"question"),
    )
    settlement_order = tuple(
        GraphNodeId(transition.writes.settlement.node_id)
        for transition in commits.transitions
        if (
            transition.scope == ()
            and isinstance(transition.command, SettleGraphNode)
            and transition.writes.settlement is not None
        )
    )
    assert settlement_order == (GraphNodeId("failed"), GraphNodeId("waiting"))


@pytest.mark.asyncio
async def test_aborted_authoritative_state_is_returned_without_execution() -> None:
    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"question")

    graph = Graph[str]("public.aborted")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node("node", interrupt, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({})
    awaiting = await graph.run(Graph.values(value="input"), run_id="aborted-run")
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    aborted_state = reduce_graph_run(
        awaiting.state,
        AbortGraphRun(awaiting.state.revision, GraphAbortReason("operator abort")),
    )

    aborted = await graph.run(state=aborted_state)

    assert isinstance(aborted, Graph.AbortedResult)
    assert aborted.abort.reason == "operator abort"
    with pytest.raises(Graph.Error, match="family driver"):
        replace(aborted, _seal=1)
