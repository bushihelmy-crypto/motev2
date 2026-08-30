import asyncio
import copy
import pickle
from dataclasses import replace
from typing import Protocol, cast

import pytest

import mote_kernel.execution as public_execution
import mote_kernel.execution.family_driver as family_driver_module
from mote_kernel.execution import Graph
from mote_kernel.execution.claim import PreparedExecutionClaim
from mote_kernel.execution.engine.claim_stage import project_claim_command
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.errors import FrameInstallationInvariantError, GraphValuePublicationError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.family_driver import admit_continued_root, project_graph_result
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import _frame_value
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.invocation import PlannedResume
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import (
    AwaitingResume,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ChildStateBinding,
    ConfirmedPublication,
    ContinuationSnapshot,
    ScopedFrameIndex,
    SkipSubstitutionProvenance,
    _admit_continuation,
    _CompiledFamilyIdentity,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    FailedGraphNode,
    FenceGraphExecution,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphNodeId,
    GraphNodeInterrupt,
    GraphNodeInterruptIdentity,
    GraphRunState,
    InterruptedGraphNode,
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
        graph.resume_failed_with("node", invalid)
    with pytest.raises(Graph.ValueAdmissionError, match=r"Graph\.values"):
        graph.resume_interrupted("node", "interrupt", invalid)
    with pytest.raises(Graph.ValueAdmissionError, match=r"Graph.values"):
        graph.skip_failed("node", "skip", output=invalid)


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
async def test_same_scope_resume_input_and_substitution_install_as_one_frame_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    async def fail_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        return Graph.failure("declined") if attempts == 1 else values

    graph = Graph[str]("public.atomic-resume-frames")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node("override", fail_once, inputs={"value": input_ref()}, outputs={"value": str})
    graph.add_node("substitute", fail, inputs={}, outputs={"value": str})
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
        observed.append((frames, installed))
        return installed

    monkeypatch.setattr(family_driver_module, "project_resume_frames", capture)

    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            graph.resume_failed_with("override", Graph.values(value="override")),
            graph.skip_failed("substitute", "replacement", output=Graph.values(value="replacement")),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert len(observed) == 1
    before, installed = observed[0]
    assert before.resume_inputs == ()
    assert not any(isinstance(record.provenance, SkipSubstitutionProvenance) for record in before.publications)
    assert len(installed.resume_inputs) == 1
    assert (
        len(
            tuple(
                record for record in installed.publications if isinstance(record.provenance, SkipSubstitutionProvenance)
            )
        )
        == 1
    )


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
async def test_skip_failed_substitution_publishes_exact_output_for_downstream_materialization() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    async def consume(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(Graph.values(result=f"accepted:{values['value']}"))

    graph = Graph[str]("public.skip-substitution")
    graph.add_node("review", fail, inputs={"value": input_ref()}, outputs={"value": str})
    graph.add_node(
        "consume",
        consume,
        inputs={"value": Graph.node_output("review", "value")},
        outputs={"result": str},
    )
    graph.add_edge("review", "consume")
    graph.set_outputs({"result": Graph.node_output("consume", "result")})
    failed = await graph.run(Graph.values(value="input"), run_id="skip-substitution-run")
    assert isinstance(failed, Graph.AwaitingResumeResult)

    resumed = await graph.run(
        state=failed.state,
        continuation=failed.continuation,
        resume=(graph.skip_failed("review", "operator replacement", output=Graph.values(value="replacement")),),
    )

    assert isinstance(resumed, Graph.CompletedResult)
    assert resumed.outputs["result"] == "accepted:replacement"


@pytest.mark.asyncio
async def test_loop_substitutions_keep_the_same_node_isolated_by_superstep() -> None:
    calls = 0

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.failure(f"failure-{calls}")

    graph = Graph[str]("public.loop-substitution-identity")
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    graph.add_edge(Graph.START, "source")
    graph.add_conditional_edge("source", "again", "source")
    graph.add_conditional_edge("source", "done", Graph.END)
    graph.set_outputs({"value": Graph.node_output("source", "value")})

    first = await graph.run(Graph.values())
    assert isinstance(first, Graph.AwaitingResumeResult)
    second = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.skip_failed(
                "source",
                "first replacement",
                route="again",
                output=Graph.values(value="first"),
            ),
        ),
    )
    assert isinstance(second, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=second.state,
        continuation=second.continuation,
        resume=(
            graph.skip_failed(
                "source",
                "second replacement",
                route="done",
                output=Graph.values(value="second"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "second"
    owner = _require_compiled_owner(graph)
    context = _admit_continuation(owner.family_identity, completed.state, completed.continuation)
    substitutions = tuple(
        record for record in context.frames.publications if isinstance(record.provenance, SkipSubstitutionProvenance)
    )
    assert tuple(record.coordinate.activation.superstep for record in substitutions) == (0, 1)
    assert tuple(_frame_value(record.frame, "value") for record in substitutions) == ("first", "second")
    assert calls == 2


@pytest.mark.asyncio
async def test_sibling_scope_substitutions_install_and_materialize_without_cross_talk() -> None:
    observed: list[tuple[str, str]] = []

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    def child(definition_id: str, label: str) -> Graph[str]:
        async def consume(values: Graph.Values[str]) -> Graph.Values[str]:
            observed.append((label, values["value"]))
            return Graph.values()

        graph = Graph[str](definition_id)
        graph.add_node("leaf", fail, inputs={}, outputs={"value": str})
        graph.add_node(
            "consume",
            consume,
            inputs={"value": Graph.node_output("leaf", "value")},
            outputs={},
        )
        graph.add_edge("leaf", "consume")
        graph.set_outputs({})
        return graph

    parent = Graph[str]("public.sibling-substitution-identity")
    parent.add_node("left", child("public.sibling.left", "left"), inputs={})
    parent.add_node("right", child("public.sibling.right", "right"), inputs={})
    parent.set_outputs({})
    paused = await parent.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)

    completed = await parent.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            parent.skip_failed("leaf", "left replacement", output=Graph.values(value="L"), scope=("left",)),
            parent.skip_failed("leaf", "right replacement", output=Graph.values(value="R"), scope=("right",)),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert sorted(observed) == [("left", "L"), ("right", "R")]
    owner = _require_compiled_owner(parent)
    context = _admit_continuation(owner.family_identity, completed.state, completed.continuation)
    substitutions = tuple(
        record for record in context.frames.publications if isinstance(record.provenance, SkipSubstitutionProvenance)
    )
    assert tuple(record.coordinate.activation.scope_run.scope for record in substitutions) == (
        (GraphNodeId("left"),),
        (GraphNodeId("right"),),
    )
    assert tuple(_frame_value(record.frame, "value") for record in substitutions) == ("L", "R")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_second", [False, True], ids=("error", "cancellation"))
async def test_multi_scope_resume_keeps_first_confirmed_install_when_second_commit_fails(
    cancel_second: bool,
) -> None:
    class SecondScopeCommitError(RuntimeError):
        pass

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    def child(definition_id: str) -> Graph[str]:
        graph = Graph[str](definition_id)
        graph.add_node("leaf", fail, inputs={}, outputs={"value": str})
        graph.set_outputs({"value": Graph.node_output("leaf", "value")})
        return graph

    parent = Graph[str]("public.multi-scope-partial-confirmation")
    parent.add_node("left", child("public.multi-scope.left"), inputs={})
    parent.add_node("right", child("public.multi-scope.right"), inputs={})
    parent.set_outputs({})
    paused = await parent.run(Graph.values())
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
                parent.skip_failed("leaf", "left replacement", output=Graph.values(value="left"), scope=("left",)),
                parent.skip_failed(
                    "leaf",
                    "right replacement",
                    output=Graph.values(value="right"),
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
    left_publication = next(
        record
        for record in checkpoint.frames.publications
        if record.coordinate.activation.scope_run.scope == (GraphNodeId("left"),)
    )
    assert left.state == transitions[0].candidate_state
    assert _frame_value(left_publication.frame, "value") == "left"
    assert not any(
        record.coordinate.activation.scope_run.scope == (GraphNodeId("right"),)
        for record in checkpoint.frames.publications
    )

    retried = await parent.run(
        state=partial.state,
        continuation=partial.continuation,
        resume=(parent.skip_failed("leaf", "right replacement", output=Graph.values(value="right"), scope=("right",)),),
    )
    assert isinstance(retried, Graph.CompletedResult)


@pytest.mark.asyncio
async def test_multi_scope_resume_keeps_first_install_when_second_confirmation_is_non_exact() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    def child(definition_id: str) -> Graph[str]:
        graph = Graph[str](definition_id)
        graph.add_node("leaf", fail, inputs={}, outputs={"value": str})
        graph.set_outputs({"value": Graph.node_output("leaf", "value")})
        return graph

    parent = Graph[str]("public.multi-scope-non-exact")
    parent.add_node("left", child("public.multi-scope-non-exact.left"), inputs={})
    parent.add_node("right", child("public.multi-scope-non-exact.right"), inputs={})
    parent.set_outputs({})
    paused = await parent.run(Graph.values())
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
                parent.skip_failed("leaf", "left replacement", output=Graph.values(value="left"), scope=("left",)),
                parent.skip_failed(
                    "leaf",
                    "right replacement",
                    output=Graph.values(value="right"),
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
    assert tuple(
        record.coordinate.activation.scope_run.scope
        for record in checkpoint.frames.publications
        if isinstance(record.provenance, SkipSubstitutionProvenance)
    ) == ((GraphNodeId("left"),),)
    retried = await parent.run(
        state=partial.state,
        continuation=partial.continuation,
        resume=(parent.skip_failed("leaf", "right replacement", output=Graph.values(value="right"), scope=("right",)),),
    )
    assert isinstance(retried, Graph.CompletedResult)


@pytest.mark.asyncio
async def test_second_scope_frame_install_failure_hands_off_only_the_first_installed_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    def child(definition_id: str) -> Graph[str]:
        graph = Graph[str](definition_id)
        graph.add_node("leaf", fail, inputs={}, outputs={"value": str})
        graph.set_outputs({})
        return graph

    parent = Graph[str]("public.multi-scope-frame-install-failure")
    parent.add_node("left", child("public.frame-install.left"), inputs={})
    parent.add_node("right", child("public.frame-install.right"), inputs={})
    parent.set_outputs({})
    paused = await parent.run(Graph.values())
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
                parent.skip_failed("leaf", "left", output=Graph.values(value="left"), scope=("left",)),
                parent.skip_failed("leaf", "right", output=Graph.values(value="right"), scope=("right",)),
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
    left_publication = next(
        record
        for record in handed_off.frames.publications
        if record.coordinate.activation.scope_run.scope == (GraphNodeId("left"),)
    )
    assert _frame_value(left_publication.frame, "value") == "left"
    assert not any(
        record.coordinate.activation.scope_run.scope == (GraphNodeId("right"),)
        for record in handed_off.frames.publications
    )
    left = next(binding for binding in handed_off.child_states if binding.coordinate.scope == (GraphNodeId("left"),))
    right = next(binding for binding in handed_off.child_states if binding.coordinate.scope == (GraphNodeId("right"),))
    assert left.state == transitions[0].candidate_state
    assert right.state == old_snapshot.child_states[1].state


@pytest.mark.asyncio
async def test_root_resume_then_child_commit_failure_hands_off_a_pairable_latest_root_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    child = Graph[str]("public.root-child-partial.child")
    child.add_node("leaf", fail, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.root-child-partial.parent")
    parent.add_node("root-failure", fail, inputs={}, outputs={})
    parent.add_node("child", child, inputs={})
    parent.set_outputs({})
    paused = await parent.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    original_snapshot = _continuation_snapshot(paused.continuation)
    root_failed = replace(
        paused.state,
        frontier=GraphFrontierState(
            tuple(
                replace(node, settlement=FailedGraphNode(GraphFailure("root declined")))
                if node.node_id == GraphNodeId("root-failure")
                else node
                for node in paused.state.frontier.nodes
            )
        ),
    )
    child_states = tuple(
        replace(
            binding,
            state=replace(
                binding.state,
                frontier=GraphFrontierState(
                    tuple(
                        replace(node, settlement=FailedGraphNode(GraphFailure("child declined")))
                        if node.node_id == GraphNodeId("leaf")
                        else node
                        for node in binding.state.frontier.nodes
                    )
                ),
            ),
        )
        for binding in original_snapshot.child_states
    )
    object.__setattr__(
        paused.continuation,
        "_snapshot",
        replace(
            original_snapshot,
            root_state=root_failed,
            child_states=child_states,
        ),
    )
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
            state=root_failed,
            continuation=paused.continuation,
            resume=(
                parent.skip_failed("root-failure", "root skip"),
                parent.skip_failed("leaf", "child skip", scope=("child",)),
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
        resume=(parent.skip_failed("leaf", "child skip", scope=("child",)),),
    )
    assert isinstance(retried, Graph.CompletedResult)


@pytest.mark.asyncio
async def test_first_resume_scope_failure_propagates_original_error_without_partial_handoff() -> None:
    class FirstScopeCommitError(RuntimeError):
        pass

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.first-scope-failure")
    graph.add_node("source", fail, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    original = FirstScopeCommitError()

    async def reject(_transition: Graph.Transition[str], /) -> Graph.State:
        raise original

    with pytest.raises(FirstScopeCommitError) as raised:
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(graph.skip_failed("source", "skip"),),
            commit=reject,
        )

    assert raised.value is original


@pytest.mark.asyncio
async def test_failure_after_exact_fence_explicitly_hands_off_the_fenced_snapshot() -> None:
    class SecondFenceError(RuntimeError):
        pass

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    def child(definition_id: str) -> Graph[str]:
        graph = Graph[str](definition_id)
        graph.add_node("leaf", fail, inputs={}, outputs={})
        graph.set_outputs({})
        return graph

    graph = Graph[str]("public.fence-partial-handoff")
    graph.add_node("left", child("public.fence-partial.left"), inputs={})
    graph.add_node("right", child("public.fence-partial.right"), inputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
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
async def test_state_only_multi_scope_substitution_is_rejected_before_first_commit() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    def child(definition_id: str) -> Graph[str]:
        graph = Graph[str](definition_id)
        graph.add_node("leaf", fail, inputs={}, outputs={"value": str})
        graph.set_outputs({})
        return graph

    parent = Graph[str]("public.state-only-multi-scope-substitution")
    parent.add_node("left", child("public.state-only-multi-scope.left"), inputs={})
    parent.add_node("right", child("public.state-only-multi-scope.right"), inputs={})
    parent.set_outputs({})
    paused = await parent.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match=r"state-only multi-scope.*left.*leaf"):
        await parent.run(
            state=paused.state,
            resume=(
                parent.skip_failed("leaf", "left", output=Graph.values(value="left"), scope=("left",)),
                parent.skip_failed("leaf", "right", output=Graph.values(value="right"), scope=("right",)),
            ),
            commit=commits,
        )

    assert commits.transitions == []


@pytest.mark.asyncio
async def test_normal_resume_never_mutates_the_input_continuation_snapshot() -> None:
    attempts = 0

    async def fail_once(_values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        return Graph.failure("declined") if attempts == 1 else Graph.values()

    graph = Graph[str]("public.immutable-input-continuation")
    graph.add_node("source", fail_once, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    old_snapshot = _continuation_snapshot(paused.continuation)

    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(graph.resume_failed("source"),),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert _continuation_snapshot(paused.continuation) is old_snapshot
    assert completed.continuation is not paused.continuation
    owner = _require_compiled_owner(graph)
    restored = _admit_continuation(owner.family_identity, paused.state, paused.continuation)
    assert restored.root_state == paused.state


@pytest.mark.asyncio
async def test_shared_input_continuation_is_not_modified_by_independent_invocations() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.shared-immutable-continuation")
    graph.add_node("source", fail, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    old_snapshot = _continuation_snapshot(paused.continuation)

    first, second = await asyncio.gather(
        graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(graph.skip_failed("source", "first"),),
        ),
        graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(graph.skip_failed("source", "second"),),
        ),
    )

    assert isinstance(first, Graph.CompletedResult)
    assert isinstance(second, Graph.CompletedResult)
    assert _continuation_snapshot(paused.continuation) is old_snapshot
    assert first.continuation is not second.continuation


@pytest.mark.asyncio
async def test_pure_skip_future_proof_accepts_a_substitution_candidate_path() -> None:
    calls: list[str] = []

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    async def consume(values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append(values["value"])
        return Graph.values(result=values["value"])

    graph = Graph[str]("public.skip-future-substitution")
    graph.add_node("ignored", fail, inputs={}, outputs={})
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    graph.add_node(
        "consumer",
        consume,
        inputs={"value": Graph.node_output("source", "value")},
        outputs={"result": str},
    )
    graph.add_edge("source", "consumer")
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)

    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            graph.skip_failed("ignored", "pure skip"),
            graph.skip_failed("source", "replacement", output=Graph.values(value="replacement")),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert calls == ["replacement"]


@pytest.mark.asyncio
async def test_pure_skip_future_proof_rejects_output_lost_after_a_runnable_step_before_commit() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    async def safe(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    graph = Graph[str]("public.skip-future-output")
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    graph.add_node("safe", safe, inputs={}, outputs={})
    graph.add_edge("source", "safe")
    graph.set_outputs({"value": Graph.node_output("source", "value")})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match="historical"):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(graph.skip_failed("source", "pure skip"),),
            commit=commits,
        )

    assert commits.transitions == []


@pytest.mark.asyncio
async def test_pure_skip_future_proof_rejects_missing_nested_boundary_input_before_commit() -> None:
    calls = {"safe": 0, "child": 0}

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    async def safe(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["safe"] += 1
        return Graph.values()

    async def child_leaf(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls["child"] += 1
        return Graph.values()

    child = Graph[str]("public.skip-future-nested.child")
    child.add_node("leaf", child_leaf, inputs={"value": Graph.graph_input("value", str)}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("public.skip-future-nested.parent")
    parent.add_node("source", fail, inputs={}, outputs={"value": str})
    parent.add_node("safe", safe, inputs={}, outputs={})
    parent.add_node("child", child, inputs={"value": Graph.node_output("source", "value")})
    parent.add_edge("source", "safe")
    parent.add_edge("safe", "child")
    parent.set_outputs({})
    paused = await parent.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(
        Graph.ValueUnavailableError,
        match=r"actions .*source.*consumer inputs=.*child.*source.value",
    ):
        await parent.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(parent.skip_failed("source", "pure skip"),),
            commit=commits,
        )

    assert commits.transitions == []
    assert calls == {"safe": 0, "child": 0}


@pytest.mark.asyncio
async def test_state_only_recovery_fails_closed_after_confirmed_substitution_continuation_is_lost() -> None:
    calls = {"source": 0, "consumer": 0}

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls["source"] += 1
        return Graph.failure("declined")

    async def consume(values: Graph.Values[str]) -> Graph.Values[str]:
        calls["consumer"] += 1
        return Graph.values(value=values["value"])

    graph = Graph[str]("public.lost-substitution-continuation")
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    graph.add_node(
        "consumer",
        consume,
        inputs={"value": Graph.node_output("source", "value")},
        outputs={"value": str},
    )
    graph.add_conditional_edge("source", "consume", "consumer")
    graph.set_outputs({"value": Graph.node_output("consumer", "value")})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    captured: GraphRunState | None = None

    class LoseAfterResumeError(RuntimeError):
        pass

    async def lose_advance(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, AdvanceGraphFrontier):
            captured = transition.candidate_state
            raise LoseAfterResumeError
        return transition.candidate_state

    with pytest.raises(LoseAfterResumeError):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                graph.skip_failed(
                    "source",
                    "replacement",
                    route="consume",
                    output=Graph.values(value="replacement"),
                ),
            ),
            commit=lose_advance,
        )
    assert captured is not None
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match=r"historical.*consumer"):
        await graph.run(state=captured, commit=commits)

    assert commits.transitions == []
    assert calls == {"source": 1, "consumer": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation_mode", ["complete", "state-only"])
async def test_skip_failed_substitution_rejects_completed_join_with_missing_required_input_before_commit(
    continuation_mode: str,
) -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        pytest.fail("unavailable join target must not execute")

    graph = Graph[str](f"public.skip-substitution-unavailable-join.{continuation_mode}")
    graph.add_node("missing", fail, inputs={}, outputs={"value": str})
    graph.add_node("review", fail, inputs={}, outputs={"value": str})
    graph.add_node(
        "consume",
        consume,
        inputs={
            "replacement": Graph.node_output("review", "value"),
            "lost": Graph.node_output("missing", "value"),
        },
        outputs={},
    )
    graph.add_join(("missing", "review"), "consume")
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match=r"required nodes.*consume"):
        actions = (
            graph.skip_failed("missing", "no replacement"),
            graph.skip_failed("review", "replacement", output=Graph.values(value="replacement")),
        )
        if continuation_mode == "complete":
            await graph.run(
                state=paused.state,
                continuation=paused.continuation,
                resume=actions,
                commit=commits,
            )
        else:
            await graph.run(state=paused.state, resume=actions, commit=commits)

    assert commits.transitions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conditional", "route"),
    [(False, None), (True, "consume")],
    ids=("selected-direct", "selected-conditional"),
)
async def test_pure_skip_rejects_selected_consumer_with_missing_output_before_commit(
    conditional: bool,
    route: str | None,
) -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        pytest.fail("consumer with unavailable input must not execute")

    graph = Graph[str](f"public.skip-selected-missing.{conditional}")
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    if not conditional:
        graph.add_node("gate", fail, inputs={}, outputs={})
    graph.add_node(
        "consumer",
        consume,
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    if conditional:
        graph.add_conditional_edge("source", "consume", "consumer")
    else:
        graph.add_join(("gate", "source"), "consumer")
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match=r"consumer.*source.value"):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                *((graph.skip_failed("gate", "pure skip"),) if not conditional else ()),
                graph.skip_failed("source", "pure skip", route=route),
            ),
            commit=commits,
        )

    assert commits.transitions == []


@pytest.mark.asyncio
async def test_pure_skip_does_not_reject_an_incomplete_join_and_commits_resume() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.skip-incomplete-join")
    graph.add_node("left", fail, inputs={}, outputs={})
    graph.add_node("right", fail, inputs={}, outputs={})
    graph.add_node("joined", fail, inputs={"required": Graph.graph_input("required", str)}, outputs={})
    graph.add_join(("left", "right"), "joined")
    graph.set_outputs({})
    paused = await graph.run(Graph.values(required="available"))
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    still_paused = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(graph.skip_failed("left", "partial join"),),
        commit=commits,
    )

    assert isinstance(still_paused, Graph.AwaitingResumeResult)
    assert isinstance(commits.transitions[0].command, ResumeGraphNodes)


@pytest.mark.asyncio
async def test_pure_skip_rejects_a_completed_join_with_missing_input_before_commit() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.skip-completed-join")
    graph.add_node("left", fail, inputs={}, outputs={})
    graph.add_node("right", fail, inputs={}, outputs={})
    graph.add_node("missing", fail, inputs={}, outputs={"value": str})
    graph.add_node(
        "joined",
        fail,
        inputs={"required": Graph.node_output("missing", "value")},
        outputs={},
    )
    graph.add_join(("left", "missing", "right"), "joined")
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ValueUnavailableError, match=r"joined.*missing.value"):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                graph.skip_failed("left", "skip"),
                graph.skip_failed("missing", "skip"),
                graph.skip_failed("right", "skip"),
            ),
            commit=commits,
        )

    assert commits.transitions == []


@pytest.mark.asyncio
async def test_pure_skip_allows_an_unselected_branch() -> None:
    calls: list[str] = []

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    async def forbidden(_values: Graph.Values[str]) -> Graph.Values[str]:
        pytest.fail("unselected consumer must not execute")

    async def selected(_values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("selected")
        return Graph.values()

    graph = Graph[str]("public.skip-unselected")
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    graph.add_node(
        "unselected",
        forbidden,
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    graph.add_node("selected", selected, inputs={}, outputs={})
    graph.add_conditional_edge("source", "unselected", "unselected")
    graph.add_conditional_edge("source", "selected", "selected")
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)

    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(graph.skip_failed("source", "pure skip", route="selected"),),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert calls == ["selected"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "message"),
    [
        (Graph.values(), "node output names"),
        (Graph.values(extra="wrong"), "node output names"),
        (Graph.values(value=1), "exact declared type"),
    ],
    ids=("missing", "extra", "wrong-type"),
)
async def test_skip_failed_substitution_rejects_non_exact_output_before_commit(
    output: Graph.Values[str | int],
    message: str,
) -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.skip-substitution-admission")
    graph.add_node("review", fail, inputs={"value": input_ref()}, outputs={"value": str})
    graph.set_outputs({"value": Graph.node_output("review", "value")})
    failed = await graph.run(Graph.values(value="input"), run_id="skip-substitution-admission-run")
    assert isinstance(failed, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(Graph.ValueAdmissionError, match=message):
        await graph.run(
            state=failed.state,
            continuation=failed.continuation,
            resume=(graph.skip_failed("review", "bad replacement", output=cast(Graph.Values[str], output)),),
            commit=commits,
        )

    assert commits.transitions == []


@pytest.mark.asyncio
async def test_skip_failed_accepts_canonical_empty_output_for_an_empty_descriptor() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.skip-empty-output")
    graph.add_node("review", fail, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)

    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(graph.skip_failed("review", "empty replacement", output=Graph.values()),),
    )

    assert isinstance(completed, Graph.CompletedResult)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "output", "error"),
    [
        (None, Graph.values(value="valid"), Graph.RoutingError),
        ("unknown", Graph.values(value="valid"), Graph.RoutingError),
        ("accept", Graph.values(), Graph.ValueAdmissionError),
        ("accept", Graph.values(value=1), Graph.ValueAdmissionError),
    ],
    ids=("missing-route", "unknown-route", "route-with-missing-output", "route-with-wrong-output"),
)
async def test_skip_failed_admits_route_and_substitution_output_independently(
    route: str | None,
    output: Graph.Values[str | int],
    error: type[Exception],
) -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.skip-route-output-admission")
    graph.add_node("review", fail, inputs={}, outputs={"value": str})
    graph.add_conditional_edge("review", "accept", Graph.END)
    graph.set_outputs({"value": Graph.node_output("review", "value")})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()

    with pytest.raises(error):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(graph.skip_failed("review", "replacement", route=route, output=cast(Graph.Values[str], output)),),
            commit=commits,
        )

    assert commits.transitions == []


@pytest.mark.asyncio
async def test_skip_failed_substitution_rejects_publication_projection_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.skip-substitution-invariant")
    graph.add_node("review", fail, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)

    def reject_publication(self: ScopedFrameIndex[str], record: ConfirmedPublication[str]) -> ScopedFrameIndex[str]:
        if isinstance(record.provenance, SkipSubstitutionProvenance):
            raise GraphValuePublicationError("forced defensive collision")
        return self

    monkeypatch.setattr(ScopedFrameIndex, "add_publication", reject_publication)
    commits = CommitLog()

    with pytest.raises(FrameInstallationInvariantError, match="owner-local projection"):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(graph.skip_failed("review", "replacement", output=Graph.values(value="replacement")),),
            commit=commits,
        )

    assert commits.transitions == []
    owner = _require_compiled_owner(graph)
    context = _admit_continuation(
        owner.family_identity,
        paused.state,
        paused.continuation,
    )
    assert not any(isinstance(record.provenance, SkipSubstitutionProvenance) for record in context.frames.publications)


@pytest.mark.asyncio
async def test_duplicate_public_skip_candidates_are_rejected_before_commit() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.duplicate-skip-candidates")
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    commits = CommitLog()
    action = graph.skip_failed("source", "replacement", output=Graph.values(value="value"))

    with pytest.raises(Graph.ValuePublicationError, match=r"source.*duplicate candidate"):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(action, action),
            commit=commits,
        )

    assert commits.transitions == []


@pytest.mark.asyncio
async def test_existing_publication_collision_is_rejected_by_public_facade_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.invocation as invocation_module

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.existing-publication-collision")
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    original = invocation_module.admit_resume_candidates

    def collide_with_confirmed(
        candidates: tuple[invocation_module.ScopedResumeCandidate[str], ...],
        frames: ScopedFrameIndex[str],
    ):
        substitution = candidates[0].substitutions[0]
        confirmed = ConfirmedPublication(
            substitution.coordinate,
            substitution.frame,
            candidates[0].previous.revision,
            substitution.provenance,
        )
        return original(candidates, replace(frames, publications=(*frames.publications, confirmed)))

    monkeypatch.setattr(invocation_module, "admit_resume_candidates", collide_with_confirmed)
    commits = CommitLog()

    with pytest.raises(Graph.ValuePublicationError, match=r"source.*confirmed publications"):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(graph.skip_failed("source", "replacement", output=Graph.values(value="value")),),
            commit=commits,
        )

    assert commits.transitions == []


@pytest.mark.asyncio
async def test_skip_failed_substitution_rejects_tampered_admitted_successor_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mote_kernel.execution.invocation as invocation_module
    from mote_kernel.state.graph_state import GraphRunCommand

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str]("public.skip-substitution-revision-invariant")
    graph.add_node("review", fail, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)

    def simulate_wrong_revision(previous: GraphRunState | None, command: GraphRunCommand) -> GraphRunState:
        successor = reduce_graph_run(previous, command)
        return replace(successor, revision=successor.revision + 1)

    monkeypatch.setattr(invocation_module, "reduce_graph_run", simulate_wrong_revision)

    with pytest.raises(Graph.SnapshotMismatchError, match="exact command reduction"):
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(graph.skip_failed("review", "replacement", output=Graph.values(value="replacement")),),
        )


@pytest.mark.asyncio
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

    assert isinstance(result, Graph.AwaitingResumeResult)
    assert tuple((failure.scope, failure.node_id, failure.failure) for failure in result.failures) == (
        ((), "nested", "nested graph node was cancelled"),
    )
    abort_scopes = tuple(
        transition.scope for transition in commits.transitions if isinstance(transition.command, AbortGraphRun)
    )
    assert abort_scopes == (("nested",),)


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

    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("retry")

    graph = Graph[str]("public.continued-root-construction-failure")
    graph.add_node("node", fail, inputs={}, outputs={})
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

    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("retry")

    child = Graph[str]("public.continued-child-construction-failure.child")
    child.add_node("leaf", fail, inputs={}, outputs={})
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

    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("retry")

    child = Graph[str]("public.continued-root-child-cleanup.child")
    child.add_node("leaf", fail, inputs={}, outputs={})
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

    async def fail_execute(
        self: GraphExecutor[str],
        claim: PreparedExecutionClaim[str],
        state: GraphRunState,
    ) -> GraphExecutionSession[str]:
        del self, claim, state
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
async def test_awaiting_result_views_preserve_canonical_root_to_child_scope_order() -> None:
    async def fail_root(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("root-failed")

    async def interrupt_root(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"root")

    def awaiting_child(name: str) -> Graph[str]:
        async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
            return Graph.failure(f"{name}-failed")

        async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
            return Graph.interrupt(name.encode())

        child = Graph[str](f"public.awaiting-views.{name}")
        child.set_resume_codec("text", 1, encode_text, decode_text)
        child.add_node("failed", fail, inputs={}, outputs={})
        child.add_node("interrupted", interrupt, inputs={}, outputs={})
        child.set_outputs({})
        return child

    parent = Graph[str]("public.awaiting-views")
    parent.set_resume_codec("text", 1, encode_text, decode_text)
    parent.add_node("right", awaiting_child("right"), inputs={})
    parent.add_node("left", awaiting_child("left"), inputs={})
    parent.add_node("root-failed", fail_root, inputs={}, outputs={})
    parent.add_node("root-interrupted", interrupt_root, inputs={}, outputs={})
    parent.set_outputs({})

    result = await parent.run(Graph.values())

    assert isinstance(result, Graph.AwaitingResumeResult)
    root_state = replace(result.state, execution_sequence=max(1, result.state.execution_sequence))
    root_interrupt = InterruptedGraphNode(
        GraphNodeInterrupt(
            GraphNodeInterruptIdentity(
                root_state.run_id,
                root_state.superstep,
                GraphNodeId("root-interrupted"),
                root_state.execution_sequence,
            ),
            GraphInterruptPayload(b"root"),
        )
    )
    root_state = replace(
        root_state,
        frontier=GraphFrontierState(
            tuple(
                replace(node, settlement=FailedGraphNode(GraphFailure("root-failed")))
                if node.node_id == GraphNodeId("root-failed")
                else replace(node, settlement=root_interrupt)
                if node.node_id == GraphNodeId("root-interrupted")
                else node
                for node in root_state.frontier.nodes
            )
        ),
    )
    owner = _require_compiled_owner(parent)
    snapshot = _admit_continuation(owner.family_identity, result.state, result.continuation)
    root, evidence_reader = await admit_continued_root(
        owner.graph,
        root_state,
        snapshot.child_states,
        snapshot.frames,
        ExecutionLimits(),
        None,
        (),
        (),
        owner.family_identity,
        recovered=False,
    )
    await root.drive_quantum()
    result = project_graph_result(
        owner.graph,
        owner.family_identity,
        root,
        evidence_reader,
        AwaitingResume((), ()),
        recovered=False,
    )
    await root.release()

    assert isinstance(result, Graph.AwaitingResumeResult)
    assert tuple((view.scope, view.node_id, view.failure) for view in result.failures) == (
        ((), "root-failed", "root-failed"),
        (("left",), "failed", "left-failed"),
        (("right",), "failed", "right-failed"),
    )
    assert tuple((view.scope, view.node_id, view.request_payload) for view in result.interrupts) == (
        ((), "root-interrupted", b"root"),
        (("left",), "interrupted", b"left"),
        (("right",), "interrupted", b"right"),
    )


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
