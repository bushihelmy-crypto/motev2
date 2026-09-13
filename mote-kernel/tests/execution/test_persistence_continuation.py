from dataclasses import replace
from typing import cast

import pytest
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence, interrupt_graph

from mote_kernel.execution import Graph
from mote_kernel.execution.persistence import (
    DurableGraphCommit,
    GraphPersistenceCommit,
    GraphPersistenceWriteSet,
    GraphRecovery,
)
from mote_kernel.state.graph_state import AbortGraphRun, GraphAbortReason, reduce_graph_run


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True], ids=["initial", "cold-recovery"])
@pytest.mark.parametrize("argument", ["omitted", "none", "bound"])
async def test_durable_continuation_inherits_its_exact_commit(recovered: bool, argument: str) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    commit = DurableGraphCommit(STRING_CODEC, store)
    graph = interrupt_graph(calls)
    waiting = await graph.run(Graph.values(value="question"), run_id="run", commit=commit)
    if recovered:
        graph = interrupt_graph(calls)
        waiting = await graph.run(recovery=GraphRecovery(store.checkpoint(), commit))
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    committed = len(store.requests)
    waiting = await graph.run(state=waiting.state, continuation=waiting.continuation)
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    assert len(store.requests) == committed
    resume = (graph.resume_interrupted("ask", waiting.interrupts[0].interrupt_id, Graph.values(value="answer")),)
    if argument == "omitted":
        completed = await graph.run(state=waiting.state, continuation=waiting.continuation, resume=resume)
    else:
        completed = await graph.run(
            state=waiting.state,
            continuation=waiting.continuation,
            resume=resume,
            commit=commit if argument == "bound" else None,
        )
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.state == store.checkpoint().root_state
    assert len(store.requests) > committed
    assert store.requests[committed].expected_revision == waiting.state.revision
    assert store.requests[-1].candidate_state == completed.state
    assert completed.outputs["value"] == "answer"
    assert calls == ["question", "answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True], ids=["initial", "cold-recovery"])
@pytest.mark.parametrize(
    "replacement", ["identical", "codec-id", "codec-version", "codec-implementation", "writer", "transient"]
)
async def test_durable_continuation_rejects_commit_replacement_before_effects(
    recovered: bool, replacement: str
) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    commit = DurableGraphCommit(STRING_CODEC, store)
    graph = interrupt_graph(calls)
    waiting = await graph.run(Graph.values(value="question"), run_id="run", commit=commit)
    if recovered:
        graph = interrupt_graph(calls)
        waiting = await graph.run(recovery=GraphRecovery(store.checkpoint(), commit))
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    other_store = MemoryPersistence[str]()
    replacement_calls: list[Graph.Transition[str]] = []
    replacement_encodings: list[Graph.Values[str]] = []

    def encode(values: Graph.Values[str]) -> bytes:
        replacement_encodings.append(values)
        return STRING_CODEC.encode(values)

    async def transient(transition: Graph.Transition[str], /) -> Graph.State:
        replacement_calls.append(transition)
        return transition.candidate_state

    candidate: Graph.Commit[str]
    if replacement == "identical":
        candidate = replace(commit)
        assert candidate == commit
    elif replacement == "codec-id":
        candidate = replace(commit, codec=replace(STRING_CODEC, codec_id="another-codec"))
    elif replacement == "codec-version":
        candidate = replace(commit, codec=replace(STRING_CODEC, version=2))
    elif replacement == "codec-implementation":
        candidate = replace(commit, codec=replace(STRING_CODEC, encoder=encode))
    elif replacement == "writer":
        candidate = replace(commit, writer=other_store)
    else:
        candidate = transient
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError, match="commit capability"):
        await graph.run(
            state=waiting.state,
            continuation=waiting.continuation,
            resume=(graph.resume_interrupted("ask", waiting.interrupts[0].interrupt_id, Graph.values(value="answer")),),
            commit=candidate,
        )
    assert calls == ["question"]
    assert len(store.requests) == committed
    assert store.checkpoint().root_state == waiting.state
    assert other_store.requests == []
    assert replacement_calls == []
    assert replacement_encodings == []


@pytest.mark.asyncio
async def test_transient_continuation_cannot_skip_start_evidence_by_adding_a_durable_commit() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    graph = interrupt_graph(calls)
    waiting = await graph.run(Graph.values(value="question"), run_id="run")
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    with pytest.raises(Graph.SnapshotMismatchError, match="commit capability"):
        await graph.run(
            state=waiting.state,
            continuation=waiting.continuation,
            resume=(graph.resume_interrupted("ask", waiting.interrupts[0].interrupt_id, Graph.values(value="answer")),),
            commit=DurableGraphCommit(STRING_CODEC, store),
        )
    assert calls == ["question"]
    assert store.requests == []


@pytest.mark.asyncio
async def test_custom_commit_inherits_the_same_binding_without_a_persistence_type_check() -> None:
    calls: list[str] = []
    transitions: list[Graph.Transition[str]] = []

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        return transition.candidate_state

    graph = interrupt_graph(calls)
    waiting = await graph.run(Graph.values(value="question"), run_id="run", commit=commit)
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    committed = len(transitions)
    completed = await graph.run(
        state=waiting.state,
        continuation=waiting.continuation,
        resume=(graph.resume_interrupted("ask", waiting.interrupts[0].interrupt_id, Graph.values(value="answer")),),
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert transitions[committed].previous_state == waiting.state
    assert transitions[-1].candidate_state == completed.state
    assert calls == ["question", "answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True], ids=["initial", "cold-recovery"])
async def test_unavailable_bound_commit_never_falls_back_to_memory(recovered: bool) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    commit = DurableGraphCommit(STRING_CODEC, store)
    graph = interrupt_graph(calls)
    waiting = await graph.run(Graph.values(value="question"), run_id="run", commit=commit)
    if recovered:
        graph = interrupt_graph(calls)
        waiting = await graph.run(recovery=GraphRecovery(store.checkpoint(), commit))
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    resume = (graph.resume_interrupted("ask", waiting.interrupts[0].interrupt_id, Graph.values(value="answer")),)
    committed = len(store.requests)
    store.unavailable = True
    with pytest.raises(OSError):
        await graph.run(state=waiting.state, continuation=waiting.continuation, resume=resume)
    assert store.checkpoint().root_state == waiting.state
    assert len(store.requests) == committed
    assert calls == ["question"]
    store.reopen()
    completed = await graph.run(state=waiting.state, continuation=waiting.continuation, resume=resume)
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.state == store.checkpoint().root_state


def interrupt_family(calls: list[str]) -> Graph[str]:
    graph = Graph[str]("persistent-interrupt-family")
    child = interrupt_graph(calls)
    for scope in ("left", "right"):
        graph.add_node(scope, child, inputs={"value": graph.graph_input(scope, str)})
    graph.add_join(("left", "right"), Graph.END)
    graph.set_outputs({scope: graph.output_ref(scope, "value") for scope in ("left", "right")})
    return graph


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True], ids=["initial", "cold-recovery"])
async def test_partial_resume_handoff_keeps_one_durable_binding_for_the_entire_family(recovered: bool) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    commit = DurableGraphCommit(STRING_CODEC, store)
    graph = interrupt_family(calls)
    waiting = await graph.run(
        Graph.values(left="question", right="question"), run_id="run", commit=commit, max_parallel_tasks=1
    )
    if recovered:
        graph = interrupt_family(calls)
        waiting = await graph.run(recovery=GraphRecovery(store.checkpoint(), commit), max_parallel_tasks=1)
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    actions = tuple(
        graph.resume_interrupted(
            "ask", interrupt.interrupt_id, Graph.values(value=interrupt.scope[0]), scope=interrupt.scope
        )
        for interrupt in waiting.interrupts
    )
    store.fail_when = lambda request: request.scope == ("right",)
    committed = len(store.requests)
    with pytest.raises(Graph.Error) as raised:
        await graph.run(state=waiting.state, continuation=waiting.continuation, resume=actions, max_parallel_tasks=1)
    partial = cast(Graph.PartialCommitError[str], raised.value)
    assert isinstance(partial, Graph.PartialCommitError)
    assert isinstance(partial.cause, OSError)
    assert partial.failed_scope == ("right",)
    assert len(store.requests) == committed + 1
    assert store.requests[-1].scope == ("left",)
    assert calls == ["question", "question"]
    store.reopen()
    with pytest.raises(Graph.SnapshotMismatchError, match="commit capability"):
        await graph.run(
            state=partial.state,
            continuation=partial.continuation,
            resume=(actions[1],),
            commit=replace(commit),
            max_parallel_tasks=1,
        )
    assert len(store.requests) == committed + 1
    assert calls == ["question", "question"]
    completed = await graph.run(
        state=partial.state, continuation=partial.continuation, resume=(actions[1],), max_parallel_tasks=1
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.state == store.checkpoint().root_state
    assert dict(completed.outputs.items()) == {"left": "left", "right": "right"}
    assert calls == ["question", "question", "left", "right"]


@pytest.mark.asyncio
async def test_new_commit_capability_requires_an_authoritative_reread() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    original = DurableGraphCommit(STRING_CODEC, store)
    graph = interrupt_graph(calls)
    waiting = await graph.run(Graph.values(value="question"), run_id="run", commit=original)
    assert isinstance(waiting, Graph.AwaitingResumeResult)
    replacement = replace(original)
    completed = await graph.run(
        recovery=GraphRecovery(store.checkpoint(), replacement),
        resume=(graph.resume_interrupted("ask", waiting.interrupts[0].interrupt_id, Graph.values(value="answer")),),
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.state == store.checkpoint().root_state
    with pytest.raises(Graph.SnapshotMismatchError, match="commit capability"):
        await graph.run(state=completed.state, continuation=completed.continuation, commit=original)
    assert calls == ["question", "answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True], ids=["initial", "cold-recovery"])
@pytest.mark.parametrize("failed", [False, True], ids=["completed", "failed"])
async def test_terminal_continuations_cannot_replace_commit_even_without_execution(
    recovered: bool, failed: bool
) -> None:
    calls: list[str] = []

    def graph() -> Graph[str]:
        result = Graph[str]("terminal-continuation")

        async def operation(values: Graph.Values[str]) -> Graph.Values[str] | Graph.FailureOutcome:
            calls.append("node")
            return Graph.failure("failed") if failed else values

        result.add_node("node", operation, inputs={"value": result.graph_input("value", str)}, outputs={"value": str})
        result.set_outputs({"value": result.output_ref("node", "value")})
        return result

    store = MemoryPersistence[str]()
    commit = DurableGraphCommit(STRING_CODEC, store)
    owner = graph()
    terminal = await owner.run(Graph.values(value="business"), run_id="run", commit=commit)
    if recovered:
        owner = graph()
        terminal = await owner.run(recovery=GraphRecovery(store.checkpoint(), commit))
    assert isinstance(terminal, Graph.FailedResult if failed else Graph.CompletedResult)
    committed = len(store.requests)
    replay = await owner.run(state=terminal.state, continuation=terminal.continuation)
    assert type(replay) is type(terminal)
    assert replay.state == terminal.state
    with pytest.raises(Graph.SnapshotMismatchError, match="commit capability"):
        await owner.run(state=replay.state, continuation=replay.continuation, commit=replace(commit))
    assert calls == ["node"]
    assert len(store.requests) == committed


@pytest.mark.asyncio
async def test_aborted_recovery_keeps_its_commit_binding_without_a_new_write() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    commit = DurableGraphCommit(STRING_CODEC, store)
    graph = interrupt_graph(calls)
    waiting = await graph.run(Graph.values(value="question"), run_id="run", commit=commit)
    aborted = reduce_graph_run(waiting.state, AbortGraphRun(waiting.state.revision, GraphAbortReason("operator abort")))
    await store(
        GraphPersistenceCommit(
            (),
            waiting.state.revision,
            aborted,
            GraphPersistenceWriteSet(Graph.CommitKey(aborted.run_id, aborted.revision), (), ()),
        )
    )
    terminal = await graph.run(recovery=GraphRecovery(store.checkpoint(), commit))
    assert isinstance(terminal, Graph.AbortedResult)
    committed = len(store.requests)
    replay = await graph.run(state=terminal.state, continuation=terminal.continuation)
    assert isinstance(replay, Graph.AbortedResult)
    assert replay.state == aborted
    with pytest.raises(Graph.SnapshotMismatchError, match="commit capability"):
        await graph.run(state=replay.state, continuation=replay.continuation, commit=replace(commit))
    assert calls == ["question"]
    assert len(store.requests) == committed
