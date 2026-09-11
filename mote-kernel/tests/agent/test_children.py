from dataclasses import replace
from typing import cast

import pytest
from tests.agent.persistence_fixtures import SnapshotPersistence
from tests.execution.persistence_fixtures import encode_strings, interrupt_graph, nested_graph

from mote_kernel.agent import Agent, AgentAnswer, AgentCompleted, AgentInterrupted, AgentResume, AgentStart
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import EncodedFrame, GraphCheckpoint, GraphPersistenceCommit
from mote_kernel.execution.run_context import ScopedStateBinding, UncreatedGraphRun
from mote_kernel.persistence import (
    AgentRunKey,
    CommitOutcome,
    ExecutionAuthority,
    NeverCreated,
    PersistenceTombstoneError,
    PersistenceUnavailableError,
)
from mote_kernel.state.graph_state import GraphRunId, OverrideGraphNodeInput, PendingGraphNode


class UnsupportedCheckpoint(GraphCheckpoint[str]):
    pass


def test_child_lookup_requires_a_typed_checkpoint_before_compiling() -> None:
    graph = Graph[str]("missing-checkpoint")
    with pytest.raises(Graph.SnapshotMismatchError, match="typed checkpoint"):
        graph.recovery_child_reads(cast(GraphCheckpoint[str], object()))
    graph.set_outputs({})


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [1, 3])
async def test_uncreated_child_queries_are_graph_owned_and_authority_consistent(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], depth: int
) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        return nested_graph(calls, depth=depth)

    async def stop(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if len(request.scope) == depth and request.expected_revision is None:
            raise PersistenceUnavailableError("child start never applied")
        return None

    agent = replace(agent, assemble=assemble)
    store.on_commit = stop
    with pytest.raises(PersistenceUnavailableError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert calls == []
    store.on_commit = None
    result = await replace(agent).run(AgentResume[str]("run"))
    assert isinstance(result, AgentCompleted)
    assert result.outputs["value"] == "input-first-second"
    assert calls == ["first", "second"]
    assert len(store.loads) == 3
    first_read, child_read = store.loads[1:]
    assert first_read[0] is child_read[0]
    assert first_read[1] == ()
    assert len(child_read[1]) == 1
    assert len(child_read[1][0].scope) == depth
    assert all(grant is child_read[0] for grant, _request in store.commits if grant != store.loads[0][0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["state", "input", "missing", "duplicate", "wrong", "absent", "tombstone", "subclass"]
)
async def test_child_reread_cannot_mix_facts_or_guess_negative_evidence(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], variant: str
) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        return nested_graph(calls)

    async def stop(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.scope:
            raise PersistenceUnavailableError("child is uncreated")
        return None

    agent = replace(agent, assemble=assemble)
    store.on_commit = stop
    with pytest.raises(PersistenceUnavailableError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    writes = len(store.commits)
    store.on_commit = None

    async def reread(
        authority: ExecutionAuthority, children: tuple[ScopeRunCoordinate, ...]
    ) -> GraphCheckpoint[str] | NeverCreated | None:
        if not children:
            return None
        if variant == "absent":
            return NeverCreated()
        if variant == "tombstone":
            raise PersistenceTombstoneError("child was deleted")
        family = await store.view(authority.run)
        checkpoint = family.checkpoint(authority.run.run_id, child_reads=children)
        if variant == "subclass":
            return UnsupportedCheckpoint(
                checkpoint.root_state, checkpoint.child_runs, checkpoint.graph_inputs, checkpoint.publications
            )
        if variant == "state":
            return replace(checkpoint, root_state=replace(checkpoint.root_state, revision=99))
        if variant == "input":
            original = checkpoint.graph_inputs[0]
            changed = EncodedFrame(
                original.frame.codec_id, original.frame.codec_version, encode_strings(Graph.values(value="changed"))
            )
            return replace(checkpoint, graph_inputs=(replace(original, frame=changed),))
        if variant == "missing":
            return replace(checkpoint, child_runs=())
        if variant == "duplicate":
            return replace(checkpoint, child_runs=(*checkpoint.child_runs, *checkpoint.child_runs))
        wrong = UncreatedGraphRun(replace(children[0], graph_run_id=GraphRunId("wrong")))
        return replace(checkpoint, child_runs=(wrong,))

    store.on_load = reread
    expected = PersistenceTombstoneError if variant == "tombstone" else Graph.SnapshotMismatchError
    with pytest.raises(expected):
        await agent.run(AgentResume[str]("run"))
    assert len(store.commits) == writes
    assert calls == []


@pytest.mark.asyncio
async def test_settled_child_loss_is_not_a_request_to_create_it_again(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        return nested_graph(calls)

    agent = replace(agent, assemble=assemble)
    await agent.run(AgentStart("run", Graph.values(value="input")))
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert len(checkpoint.child_runs) == 1

    async def missing(
        _authority: ExecutionAuthority, _children: tuple[ScopeRunCoordinate, ...]
    ) -> GraphCheckpoint[str]:
        return replace(checkpoint, child_runs=())

    store.on_load = missing
    writes = len(store.commits)
    with pytest.raises(Graph.SnapshotMismatchError, match="settled child"):
        await agent.run(AgentResume[str]("run"))
    assert len(store.loads) == 2
    assert len(store.commits) == writes
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_partial_resume_uses_only_a_new_authoritative_read_not_a_continuation(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        graph = Graph[str]("two-interrupts")
        child = interrupt_graph(calls)
        for name in ("left", "right"):
            graph.add_node(name, child, inputs={"value": graph.graph_input("value", str)})
        graph.add_join(("left", "right"), Graph.END)
        graph.set_outputs({"left": graph.output_ref("left", "value"), "right": graph.output_ref("right", "value")})
        return graph

    agent = replace(agent, assemble=assemble)
    waiting = await agent.run(AgentStart("run", Graph.values(value="question")))
    assert isinstance(waiting, AgentInterrupted)
    answers = tuple(AgentAnswer(question, Graph.values(value=question.scope[0])) for question in waiting.interrupts)
    assert len(answers) == 2
    failure = PersistenceUnavailableError("right resume did not apply")

    async def stop(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.scope == ("right",) and any(
            isinstance(node.settlement, PendingGraphNode) and isinstance(node.settlement.input, OverrideGraphNodeInput)
            for node in request.candidate_state.frontier.nodes
        ):
            raise failure
        return None

    store.on_commit = stop
    with pytest.raises(PersistenceUnavailableError) as caught:
        await agent.run(AgentResume("run", answers))
    assert caught.value is failure
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    left = next(item for item in checkpoint.child_runs if item.scope_run.scope == ("left",))
    assert isinstance(left, ScopedStateBinding)
    assert any(
        isinstance(node.settlement, PendingGraphNode) and isinstance(node.settlement.input, OverrideGraphNodeInput)
        for node in left.state.frontier.nodes
    )
    assert calls == ["question", "question"]
    store.on_commit = None
    right_answer = next(answer for answer in answers if answer.interrupt.scope == ("right",))
    completed = await replace(agent).run(AgentResume("run", (right_answer,)))
    assert isinstance(completed, AgentCompleted)
    assert dict(completed.outputs.items()) == {"left": "left", "right": "right"}
    assert sorted(calls) == ["left", "question", "question", "right"]
