import asyncio
from copy import deepcopy
from dataclasses import fields, replace

import pytest
from tests.agent.persistence_fixtures import AsyncGate, MemoryAuthority, SnapshotPersistence
from tests.execution.persistence_fixtures import interrupt_graph, linear_graph

from mote_kernel import Agent
from mote_kernel.agent import (
    AgentAborted,
    AgentAnswer,
    AgentCompleted,
    AgentFailed,
    AgentInterrupted,
    AgentResume,
    AgentRunNotFoundError,
    AgentStart,
)
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.persistence import GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentRunKey,
    AuthorityBusyError,
    CommitOutcome,
    ExecutionAuthority,
    PersistenceConflictError,
)
from mote_kernel.state.graph_state import GraphRunId, GraphRunStatus


@pytest.mark.asyncio
async def test_execution_limit_preserves_committed_work_for_a_fresh_authorized_resume(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, calls: list[str]
) -> None:
    limited = replace(agent, limits=ExecutionLimits(max_supersteps=1))
    with pytest.raises(Graph.ExecutionLimitError):
        await limited.run(AgentStart("run", Graph.values(value="input")))
    assert calls == ["first"]
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.root_state.status is GraphRunStatus.RUNNING
    assert len(checkpoint.publications) == 1
    assert authority.current == {}
    completed = await replace(agent, limits=ExecutionLimits(max_supersteps=2)).run(AgentResume[str]("run"))
    assert isinstance(completed, AgentCompleted)
    assert completed.outputs["value"] == "input-first-second"
    assert calls == ["first", "second"]
    assert authority.released == authority.acquired


@pytest.mark.asyncio
async def test_start_and_cold_terminal_replay_use_only_the_store(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, calls: list[str]
) -> None:
    result = await agent.run(AgentStart("run", Graph.values(value="input")))
    assert isinstance(result, AgentCompleted)
    assert result.outputs["value"] == "input-first-second"
    assert result.outputs.activation_config is None
    assert {field.name for field in fields(result)} == {"run_id", "outputs"}
    assert calls == ["first", "second"]
    stored = await store.view(AgentRunKey("agent", GraphRunId("run")))
    assert stored.checkpoint().root_state.status is GraphRunStatus.COMPLETED
    writes = len(store.commits)

    def assemble(_config: Config | None) -> Graph[str]:
        return linear_graph(calls)

    fresh = replace(agent, assemble=assemble, codec=deepcopy(agent.codec))
    replayed = await fresh.run(AgentResume[str]("run"))
    assert isinstance(replayed, AgentCompleted)
    assert replayed.outputs["value"] == result.outputs["value"]
    assert calls == ["first", "second"]
    assert len(store.commits) == writes
    assert authority.current == {}
    assert authority.released == authority.acquired
    assert len(authority.acquired) == 2
    assert authority.acquired[0] != authority.acquired[1]


@pytest.mark.asyncio
async def test_start_existing_and_resume_absent_never_mutate(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    with pytest.raises(AgentRunNotFoundError):
        await agent.run(AgentResume[str]("run"))
    assert store.commits == []
    assert calls == []
    await agent.run(AgentStart("run", Graph.values(value="original")))
    writes = len(store.commits)
    with pytest.raises(PersistenceConflictError):
        await agent.run(AgentStart("run", Graph.values(value="replacement")))
    assert len(store.commits) == writes
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_cold_interrupt_answers_remain_exact_and_single_consumption(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        return interrupt_graph(calls)

    agent = replace(agent, assemble=assemble)
    waiting = await agent.run(AgentStart("run", Graph.values(value="question")))
    assert isinstance(waiting, AgentInterrupted)
    assert len(waiting.interrupts) == 1
    assert waiting.interrupts[0].request_payload == b"question"
    writes = len(store.commits)
    repeated = await agent.run(AgentResume[str]("run"))
    assert isinstance(repeated, AgentInterrupted)
    assert repeated.interrupts == waiting.interrupts
    assert len(store.commits) == writes
    answer = AgentAnswer(waiting.interrupts[0], Graph.values(value="answer"))
    completed = await replace(agent).run(AgentResume("run", (answer,)))
    assert isinstance(completed, AgentCompleted)
    assert completed.outputs["value"] == "answer"
    writes = len(store.commits)
    with pytest.raises(Graph.Error):
        await agent.run(AgentResume("run", (answer,)))
    assert calls == ["question", "answer"]
    assert len(store.commits) == writes


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["failed", "aborted"])
async def test_failed_and_aborted_runs_replay_without_new_work(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], terminal: str
) -> None:
    gate = AsyncGate()

    def assemble(_config: object) -> Graph[str]:
        graph = Graph[str]("terminal")

        async def node(values: Graph.Values[str]) -> Graph.Outcome[str]:
            calls.append("node")
            if terminal == "aborted":
                await gate.wait()
            return Graph.failure(values["value"])

        graph.add_node("node", node, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
        graph.add_edge(Graph.START, "node")
        graph.set_outputs({"value": graph.output_ref("node", "value")})
        return graph

    agent = replace(agent, assemble=assemble)
    if terminal == "aborted":
        task = asyncio.create_task(agent.run(AgentStart("run", Graph.values(value="input"))))
        await gate.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = await agent.run(AgentResume[str]("run"))
    else:
        result = await agent.run(AgentStart("run", Graph.values(value="input")))
    if terminal == "failed":
        assert isinstance(result, AgentFailed)
        assert result.failures[0].failure == "input"
    else:
        assert isinstance(result, AgentAborted)
        assert result.abort.reason
    writes = len(store.commits)
    replayed = await agent.run(AgentResume[str]("run"))
    assert replayed == result
    assert calls == ["node"]
    assert len(store.commits) == writes


@pytest.mark.asyncio
@pytest.mark.parametrize("same_instance", [True, False])
async def test_same_run_concurrency_is_externally_arbitrated(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, same_instance: bool
) -> None:
    gate = AsyncGate()

    async def block(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.expected_revision is None:
            await gate.wait()
        return None

    store.on_commit = block
    owner = asyncio.create_task(agent.run(AgentStart("run", Graph.values(value="input"))))
    await gate.entered.wait()
    competitor = agent if same_instance else replace(agent)
    with pytest.raises(AuthorityBusyError):
        await competitor.run(AgentResume[str]("run"))
    assert len(store.loads) == 1
    gate.proceed.set()
    assert isinstance(await owner, AgentCompleted)
    assert authority.current == {}


@pytest.mark.asyncio
async def test_agent_and_run_namespaces_are_independent(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority
) -> None:
    results = await asyncio.gather(
        agent.run(AgentStart("first", Graph.values(value="one"))),
        agent.run(AgentStart("second", Graph.values(value="two"))),
        replace(agent, agent_id="other").run(AgentStart("first", Graph.values(value="three"))),
    )
    assert all(isinstance(result, AgentCompleted) for result in results)
    assert {item.run for item in authority.acquired} == {
        AgentRunKey("agent", GraphRunId("first")),
        AgentRunKey("agent", GraphRunId("second")),
        AgentRunKey("other", GraphRunId("first")),
    }
    assert len(store.loads) == 3
    assert authority.current == {}
