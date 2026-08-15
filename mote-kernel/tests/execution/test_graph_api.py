import asyncio
from dataclasses import replace
from typing import Protocol, cast

import pytest

import mote_kernel.execution as public_execution
from mote_kernel.execution import Graph
from mote_kernel.execution.claim import PreparedExecutionClaim
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.errors import GraphValidationError, SnapshotMismatchError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.request import ResumeNodeRequest, StepRequest, UseRequestInput
from mote_kernel.execution.result import ChildWaitAction, PrepareDisposition, WaitingForChildren
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    FenceGraphExecution,
    GraphAbortReason,
    GraphRunState,
    ResumeGraphNodes,
    SettleGraphNode,
    StartGraphRun,
    reduce_graph_run,
)


class CommitLog:
    def __init__(self) -> None:
        self.transitions: list[Graph.Transition[str]] = []

    async def __call__(self, transition: Graph.Transition[str]) -> Graph.State:
        self.transitions.append(transition)
        assert transition.next_state == reduce_graph_run(transition.previous_state, transition.command)
        return transition.next_state


def encode_text(value: str) -> bytes:
    return value.encode()


def decode_text(payload: bytes) -> str:
    return payload.decode()


class _StringNode(Protocol):
    async def __call__(self, value: str, /) -> Graph.Outcome[str] | str: ...


@pytest.mark.asyncio
async def test_graph_is_the_single_public_execution_facade_and_runs_plain_node_outputs() -> None:
    async def uppercase(value: str) -> str:
        return value.upper()

    graph = Graph[str, str]("public.graph").add_node("uppercase", uppercase)
    assert graph.set_entry("uppercase").add_edge("uppercase", Graph.END) is graph
    assert public_execution.__all__ == ["Graph"]
    assert public_execution.Graph is Graph

    result: Graph.Result[str] = await graph.run("hello", run_id="public-run")

    assert result.completed
    assert not result.aborted
    assert not result.awaiting_resume
    assert tuple((output.node_id, output.output) for output in result.outputs) == (("uppercase", "HELLO"),)
    assert result.failures == ()
    assert result.interrupts == ()

    repeated = await graph.run("ignored", state=result.state)
    assert repeated.completed and repeated.outputs == ()
    with pytest.raises(GraphValidationError, match="immutable"):
        graph.add_node("late", uppercase)


@pytest.mark.asyncio
async def test_one_compiled_facade_runs_independent_states_concurrently() -> None:
    async def echo(value: str) -> str:
        await asyncio.sleep(0)
        return value

    graph = Graph[str, str]("public.concurrent").add_node("node", echo).set_entry("node")
    graph.add_edge("node", Graph.END)

    first, second = await asyncio.gather(
        graph.run("first", run_id="first-run"),
        graph.run("second", run_id="second-run"),
    )

    assert first.state.run_id == "first-run"
    assert second.state.run_id == "second-run"
    assert first.outputs[0].output == "first"
    assert second.outputs[0].output == "second"


@pytest.mark.asyncio
async def test_run_commits_each_resource_node_transition_and_immediately_admits_the_waiter() -> None:
    async def complete(value: str) -> str:
        return value

    graph = Graph[str, str]("public.resources")
    assert graph.add_resource("exclusive") is graph
    graph.add_node("b", complete, resources=("exclusive",))
    graph.add_node("a", complete, resources=("exclusive",))
    graph.set_entry("b", "a").add_edge("a", Graph.END).add_edge("b", Graph.END)
    commits = CommitLog()

    result = await graph.run("value", run_id="resource-run", commit=commits, max_parallel_tasks=2)

    assert result.completed
    assert [type(transition.command) for transition in commits.transitions] == [
        StartGraphRun,
        ClaimGraphExecution,
        SettleGraphNode,
        SettleGraphNode,
        CompleteGraphFrontier,
    ]
    settlements = [transition for transition in commits.transitions if isinstance(transition.command, SettleGraphNode)]
    assert [transition.result.task.node_id for transition in settlements if transition.result is not None] == [
        "a",
        "b",
    ]
    first_resources = settlements[0].next_state.resources
    assert first_resources is not None
    assert len(first_resources.acquisitions) == 1
    assert first_resources.acquisitions[0].node_id == "b"
    assert first_resources.acquisitions[0].admitted
    assert all(
        transition.result is None
        for transition in commits.transitions
        if not isinstance(transition.command, SettleGraphNode)
    )
    assert tuple(output.node_id for output in result.outputs) == ("a", "b")


@pytest.mark.asyncio
async def test_public_builder_supports_conditional_routing_and_joins() -> None:
    async def decision(value: str) -> Graph.Outcome[str]:
        return Graph.success(f"decision:{value}", route="left")

    async def plain(value: str) -> str:
        return value

    graph = Graph[str, str]("public.routing")
    graph.add_node("decision", decision).add_node("side", plain).add_node("left", plain).add_node("joined", plain)
    graph.set_entry("decision", "side")
    assert graph.add_conditional_edge("decision", "left", "left") is graph
    assert graph.add_join(("left", "side"), "joined") is graph
    graph.add_edge("joined", Graph.END)

    result = await graph.run("input")

    assert result.completed
    assert result.state.run_id
    assert tuple(output.node_id for output in result.outputs) == ("decision", "side", "left", "joined")


@pytest.mark.asyncio
async def test_failure_resume_actions_are_canonicalized_and_share_run() -> None:
    attempts = {"a": 0, "b": 0}

    def operation(node_id: str) -> _StringNode:
        async def fail_once(value: str) -> Graph.Outcome[str] | str:
            attempts[node_id] += 1
            if attempts[node_id] == 1:
                return Graph.failure(f"{node_id}-failed")
            return value

        return fail_once

    graph = Graph[str, str]("public.resume")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node("a", operation("a")).add_node("b", operation("b"))
    graph.set_entry("a", "b").add_edge("a", Graph.END).add_edge("b", Graph.END)

    failed = await graph.run("initial", run_id="resume-run")

    assert failed.awaiting_resume and not failed.completed and not failed.aborted
    assert tuple((failure.node_id, failure.failure) for failure in failed.failures) == (
        ("a", "a-failed"),
        ("b", "b-failed"),
    )
    assert failed.interrupts == () and failed.outputs == ()

    commits = CommitLog()
    resumed = await graph.run(
        "run-input",
        state=failed.state,
        resume=(graph.resume_failed_with("b", "override"), graph.resume_failed("a")),
        commit=commits,
    )

    assert resumed.completed
    resume_command = commits.transitions[0].command
    assert isinstance(resume_command, ResumeGraphNodes)
    assert tuple(action.node_id for action in resume_command.actions) == ("a", "b")
    assert tuple((output.node_id, output.output) for output in resumed.outputs) == (
        ("a", "run-input"),
        ("b", "override"),
    )


@pytest.mark.asyncio
async def test_skip_failed_routes_without_reexecuting_the_node() -> None:
    calls = 0

    async def fail(value: str) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.failure("declined")

    graph = Graph[str, str]("public.skip")
    graph.add_node("review", fail).set_entry("review")
    graph.add_conditional_edge("review", "skip", Graph.END)
    failed = await graph.run("input", run_id="skip-run")

    skipped = await graph.run(
        "ignored",
        state=failed.state,
        resume=(graph.skip_failed("review", "operator skip", route="skip"),),
    )

    assert skipped.completed and skipped.outputs == ()
    assert calls == 1


@pytest.mark.asyncio
async def test_interrupt_resume_is_an_exact_action_inside_run() -> None:
    calls = 0

    async def interrupt_once(value: str) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Graph.interrupt(b"approve?")
        return Graph.success(value)

    graph = Graph[str, str]("public.interrupt")
    graph.set_resume_codec("text", 1, encode_text, decode_text)
    graph.add_node("review", interrupt_once).set_entry("review").add_edge("review", Graph.END)

    interrupted = await graph.run("draft", run_id="interrupt-run")

    assert interrupted.awaiting_resume
    assert interrupted.failures == ()
    assert len(interrupted.interrupts) == 1
    view = interrupted.interrupts[0]
    assert (view.node_id, view.request_payload) == ("review", b"approve?")
    with pytest.raises(SnapshotMismatchError, match="does not match"):
        await graph.run(
            "ignored",
            state=interrupted.state,
            resume=(graph.resume_interrupted("review", "stale", "approved"),),
        )

    resumed = await graph.run(
        "ignored",
        state=interrupted.state,
        resume=(graph.resume_interrupted("review", view.interrupt_id, "approved"),),
    )

    assert resumed.completed
    assert tuple(output.output for output in resumed.outputs) == ("approved",)


@pytest.mark.asyncio
async def test_run_rejects_resume_without_state_and_unsupported_resume_variants() -> None:
    async def fail(value: str) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    graph = Graph[str, str]("public.invalid-resume").add_node("node", fail).set_entry("node")
    graph.add_edge("node", Graph.END)
    with pytest.raises(SnapshotMismatchError, match="new graph run"):
        await graph.run("input", resume=(graph.resume_failed("node"),))

    failed = await graph.run("input")
    unsupported = cast(ResumeNodeRequest[str], UseRequestInput())
    with pytest.raises(SnapshotMismatchError, match="unsupported action"):
        await graph.run("input", state=failed.state, resume=(unsupported,))


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_confirmation", ["wrong-type", "wrong-revision"])
async def test_run_requires_exact_authoritative_commit_confirmation(wrong_confirmation: str) -> None:
    async def echo(value: str) -> str:
        return value

    graph = Graph[str, str]("public.commit-mismatch").add_node("node", echo).set_entry("node")

    async def reject(transition: Graph.Transition[str]) -> Graph.State:
        if wrong_confirmation == "wrong-type":
            return cast(GraphRunState, "not-state")
        return replace(transition.next_state, revision=transition.next_state.revision + 1)

    with pytest.raises(SnapshotMismatchError, match="exact authoritative"):
        await graph.run("input", commit=reject)


class CommitAcknowledgementLostError(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_run_fences_an_authoritative_unacknowledged_claim_before_recovery() -> None:
    calls = 0
    captured: GraphRunState | None = None

    async def echo(value: str) -> str:
        nonlocal calls
        calls += 1
        return value

    async def lose_claim_ack(transition: Graph.Transition[str]) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, ClaimGraphExecution):
            captured = transition.next_state
            raise CommitAcknowledgementLostError
        return transition.next_state

    graph = Graph[str, str]("public.claim-recovery").add_node("node", echo).set_entry("node")
    graph.add_edge("node", Graph.END)
    with pytest.raises(CommitAcknowledgementLostError):
        await graph.run("input", run_id="recover-run", commit=lose_claim_ack)

    assert captured is not None and captured.execution is not None
    assert calls == 0
    recovered = await graph.run("input", state=captured)
    assert recovered.completed and calls == 1
    with pytest.raises(SnapshotMismatchError, match="run_id"):
        await graph.run("input", run_id="wrong-run", state=recovered.state)


@pytest.mark.asyncio
async def test_node_exception_closes_and_fences_before_propagation() -> None:
    should_fail = True

    async def operation(value: str) -> str:
        if should_fail:
            raise ValueError("node failed")
        return value

    graph = Graph[str, str]("public.node-error").add_node("node", operation).set_entry("node")
    commits = CommitLog()
    with pytest.raises(ValueError, match="node failed"):
        await graph.run("input", run_id="error-run", commit=commits)

    assert isinstance(commits.transitions[-1].command, FenceGraphExecution)
    fenced = commits.transitions[-1].next_state
    assert fenced.execution is None
    should_fail = False
    recovered = await graph.run("input", state=fenced)
    assert recovered.completed


@pytest.mark.asyncio
async def test_session_creation_error_fences_the_committed_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def echo(value: str) -> str:
        return value

    async def fail_execute(
        self: GraphExecutor[str, str],
        claim: PreparedExecutionClaim,
        request: StepRequest[str, str],
    ) -> GraphExecutionSession[str, str]:
        raise RuntimeError("session creation failed")

    monkeypatch.setattr(GraphExecutor, "execute", fail_execute)
    graph = Graph[str, str]("public.session-error").add_node("node", echo).set_entry("node")
    commits = CommitLog()

    with pytest.raises(RuntimeError, match="session creation"):
        await graph.run("input", commit=commits)

    assert isinstance(commits.transitions[-1].command, FenceGraphExecution)
    assert commits.transitions[-1].next_state.execution is None


@pytest.mark.asyncio
async def test_facade_fails_closed_if_internal_preparation_requests_nested_coordination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def echo(value: str) -> str:
        return value

    async def wait_for_child(
        self: GraphExecutor[str, str], request: StepRequest[str, str]
    ) -> PrepareDisposition[str, str]:
        action = cast(ChildWaitAction[str, str], None)
        return WaitingForChildren(action)

    monkeypatch.setattr(GraphExecutor, "prepare", wait_for_child)
    graph = Graph[str, str]("public.nested-boundary").add_node("node", echo).set_entry("node")

    with pytest.raises(GraphValidationError, match="does not compose nested"):
        await graph.run("input")


@pytest.mark.asyncio
async def test_aborted_authoritative_state_is_returned_without_execution() -> None:
    async def fail(value: str) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    graph = Graph[str, str]("public.aborted").add_node("node", fail).set_entry("node")
    failed = await graph.run("input", run_id="aborted-run")
    aborted_state = reduce_graph_run(
        failed.state,
        AbortGraphRun(failed.state.revision, GraphAbortReason("operator abort")),
    )

    aborted = await graph.run("ignored", state=aborted_state)

    assert aborted.aborted
    assert not aborted.completed
    assert not aborted.awaiting_resume
