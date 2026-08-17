import asyncio
import copy
import pickle
from dataclasses import replace
from typing import Protocol, cast

import pytest

import mote_kernel.execution as public_execution
from mote_kernel.execution import Graph
from mote_kernel.execution.claim import PreparedExecutionClaim
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    PrepareDisposition,
    PreparedNestedRun,
    StartMissingChildren,
    WaitingForChildren,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    FenceGraphExecution,
    GraphAbortReason,
    GraphNodeId,
    GraphRunState,
    ParentGraphActivation,
    ResumeGraphNodes,
    SettleGraphNode,
    StartGraphRun,
    child_graph_run_id,
    reduce_graph_run,
)


class CommitLog:
    def __init__(self) -> None:
        self.transitions: list[Graph.Transition[str]] = []

    async def __call__(self, transition: Graph.Transition[str], /) -> Graph.State:
        self.transitions.append(transition)
        assert transition.candidate_state == reduce_graph_run(transition.previous_state, transition.command)
        return transition.candidate_state


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
        graph.resume_failed_with("node", invalid)
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


def encode_text(value: Graph.Values[str]) -> bytes:
    return value["value"].encode()


def decode_text(payload: bytes) -> Graph.Values[str]:
    return Graph.values(value=payload.decode())


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
    with pytest.raises(Graph.SnapshotMismatchError, match="family driver"):
        replace(first_transition, _seal=1)
    successful_settlement = next(
        transition.result for transition in commits.transitions if isinstance(transition.result, Graph.SuccessResult)
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
    admitted = [transition.result for transition in settlements]
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
async def test_failure_resume_actions_are_canonicalized_and_share_run() -> None:
    attempts = {"a": 0, "b": 0}

    def operation(node_id: str):
        async def fail_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
            attempts[node_id] += 1
            if attempts[node_id] == 1:
                return Graph.failure(f"{node_id}-failed")
            return values

        return fail_once

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

    failed = await graph.run(Graph.values(value="initial"), run_id="resume-run")

    assert isinstance(failed, Graph.AwaitingResumeResult)
    assert tuple((view.node_id, view.failure) for view in failed.failures) == (
        ("a", "a-failed"),
        ("b", "b-failed"),
    )
    with pytest.raises(Graph.Error, match="family driver"):
        replace(failed, _seal=1)
    commits = CommitLog()
    resumed = await graph.run(
        state=failed.state,
        continuation=failed.continuation,
        resume=(
            graph.resume_failed("a"),
            graph.resume_failed_with("b", Graph.values(value="override")),
        ),
        commit=commits,
    )

    assert isinstance(resumed, Graph.CompletedResult)
    assert isinstance(commits.transitions[0].command, ResumeGraphNodes)
    assert tuple(action.node_id for action in commits.transitions[0].command.actions) == ("a", "b")
    assert resumed.outputs["value"] == "initial|override"


@pytest.mark.asyncio
async def test_skip_failed_routes_without_reexecuting_the_node() -> None:
    calls = 0

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.failure("declined")

    graph = Graph[str]("public.skip")
    graph.add_node("review", fail, inputs={"value": input_ref()}, outputs={"value": str})
    graph.add_conditional_edge("review", "skip", Graph.END)
    graph.set_outputs({})
    failed = await graph.run(Graph.values(value="input"), run_id="skip-run")
    assert isinstance(failed, Graph.AwaitingResumeResult)

    skipped = await graph.run(
        state=failed.state,
        continuation=failed.continuation,
        resume=(graph.skip_failed("review", "operator skip", route="skip"),),
    )

    assert isinstance(skipped, Graph.CompletedResult)
    assert calls == 1


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

    resumed = await graph.run(
        state=interrupted.state,
        continuation=interrupted.continuation,
        resume=(
            graph.resume_interrupted(
                "review",
                view.interrupt_id,
                Graph.values(value="approved"),
            ),
        ),
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
    assert isinstance(first, Graph.AwaitingResumeResult)
    assert isinstance(second, Graph.AwaitingResumeResult)
    with pytest.raises(Graph.SnapshotMismatchError, match="same compiled graph lineage"):
        await graph.run(
            state=first.state,
            continuation=second.continuation,
        )


@pytest.mark.asyncio
async def test_run_rejects_resume_without_state_and_unsupported_resume_variants() -> None:
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
            resume=(graph.resume_failed("node"),),
        )

    failed = await graph.run(Graph.values(value="input"))
    assert isinstance(failed, Graph.AwaitingResumeResult)
    with pytest.raises(Graph.SnapshotMismatchError, match="interrupt resume requires an interrupted node"):
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

    async def fail(values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.failure(f"failed:{values['value']}")

    graph = Graph[str]("public.invalid-limits.resume")
    graph.add_node("node", fail, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({})
    failed = await graph.run(Graph.values(value="first"), run_id="resume-run")
    assert isinstance(failed, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ExecutionLimitError, match="exact positive"):
        await graph.run(
            state=failed.state,
            continuation=failed.continuation,
            resume=(graph.resume_failed("node"),),
            commit=commits,
            max_supersteps=max_supersteps,
            max_parallel_tasks=max_parallel_tasks,
        )

    assert commits.transitions == []
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_confirmation", ["wrong-type", "wrong-revision"])
async def test_run_requires_exact_authoritative_commit_confirmation(wrong_confirmation: str) -> None:
    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    graph = Graph[str]("public.commit-mismatch")
    graph.add_node("node", echo, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({})

    async def reject(transition: Graph.Transition[str], /) -> Graph.State:
        if wrong_confirmation == "wrong-type":
            return cast(GraphRunState, "not-state")
        return replace(transition.candidate_state, revision=transition.candidate_state.revision + 1)

    with pytest.raises(Graph.SnapshotMismatchError, match="exact authoritative"):
        await graph.run(Graph.values(value="input"), commit=reject)


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
async def test_cancelled_run_quiesces_workers_retains_the_claim_and_recovers_from_authoritative_state() -> None:
    entered = asyncio.Event()
    cleaned = asyncio.Event()
    never = asyncio.Event()
    calls = 0
    should_block = True

    async def operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        if should_block:
            entered.set()
            try:
                await never.wait()
            finally:
                cleaned.set()
        return Graph.values()

    graph = Graph[str]("public.cancel-recovery")
    graph.add_node("node", operation, inputs={}, outputs={})
    graph.set_outputs({})
    commits = CommitLog()
    running = asyncio.create_task(graph.run(Graph.values(), run_id="cancel-run", commit=commits))
    await entered.wait()

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert cleaned.is_set()
    assert [type(transition.command) for transition in commits.transitions] == [
        StartGraphRun,
        ClaimGraphExecution,
    ]
    active_state = commits.transitions[-1].candidate_state
    assert active_state.execution is not None

    should_block = False
    recovery_commits = CommitLog()
    recovered = await graph.run(state=active_state, commit=recovery_commits)

    assert isinstance(recovery_commits.transitions[0].command, FenceGraphExecution)
    assert isinstance(recovered, Graph.CompletedResult)
    assert calls == 2


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

    async def fail_execute(
        self: GraphExecutor[str],
        claim: PreparedExecutionClaim,
        request: StepRequest[str],
    ) -> GraphExecutionSession[str]:
        del self, claim, request
        raise RuntimeError("session creation failed")

    monkeypatch.setattr(GraphExecutor, "execute", fail_execute)
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
    assert any(transition.scope == ("child",) for transition in commits.transitions)


@pytest.mark.asyncio
async def test_facade_fails_closed_if_internal_preparation_requests_nested_coordination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    async def wait_for_unknown_child(
        self: GraphExecutor[str],
        request: StepRequest[str],
    ) -> PrepareDisposition[str]:
        child_graph = self.graph.nested_graphs[GraphNodeId("child")]
        parent = ParentGraphActivation(
            request.state.run_id,
            request.state.superstep,
            GraphNodeId("unknown-child"),
        )
        run_id = child_graph_run_id(parent.run_id, parent.superstep, parent.node_id)
        prepared = PreparedNestedRun(
            parent,
            child_graph,
            project_start_graph_command(child_graph, run_id, parent),
        )
        return WaitingForChildren(StartMissingChildren((prepared,)))

    child = Graph[str]("public.invalid-child-coordination.child")
    child.add_node(
        "leaf",
        echo,
        inputs={"value": input_ref()},
        outputs={"value": str},
    )
    child.set_outputs({"value": Graph.node_output("leaf", "value")})
    parent = Graph[str]("public.invalid-child-coordination.parent")
    parent.add_node("child", child, inputs={"value": input_ref()})
    parent.set_outputs({})
    monkeypatch.setattr(GraphExecutor, "prepare", wait_for_unknown_child)

    with pytest.raises(Graph.SnapshotMismatchError, match="current pending node"):
        await parent.run(Graph.values(value="input"))


@pytest.mark.asyncio
async def test_aborted_authoritative_state_is_returned_without_execution() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    graph = Graph[str]("public.aborted")
    graph.add_node("node", fail, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({})
    failed = await graph.run(Graph.values(value="input"), run_id="aborted-run")
    assert isinstance(failed, Graph.AwaitingResumeResult)
    aborted_state = reduce_graph_run(
        failed.state,
        AbortGraphRun(failed.state.revision, GraphAbortReason("operator abort")),
    )

    aborted = await graph.run(state=aborted_state)

    assert isinstance(aborted, Graph.AbortedResult)
    assert aborted.abort.reason == "operator abort"
    with pytest.raises(Graph.Error, match="family driver"):
        replace(aborted, _seal=1)
