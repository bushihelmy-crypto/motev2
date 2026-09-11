import asyncio
from dataclasses import replace

import pytest
from tests.agent.config_fixtures import ConfigCatalog
from tests.agent.persistence_fixtures import AsyncGate, MemoryAuthority, SnapshotPersistence
from tests.execution.test_persistence_config import config_at

from mote_kernel.agent import Agent, AgentAborted, AgentCompleted, AgentConfig, AgentResume, AgentStart
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentRunKey,
    CommitOutcome,
    CommitUnknown,
    CommitUnresolvedError,
    ExecutionAuthority,
    NeverCreated,
    PersistenceUnavailableError,
)
from mote_kernel.state.graph_state import GraphRunId, GraphRunStatus


@pytest.mark.asyncio
async def test_cancelled_acquisition_is_joined_and_its_grant_released_once(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority
) -> None:
    gate = AsyncGate()
    authority.acquire_gate = gate
    task = asyncio.create_task(agent.run(AgentStart("run", Graph.values(value="input"))))
    await gate.entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert authority.current
    assert authority.released == []
    gate.proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.finished.is_set()
    assert authority.released == authority.acquired
    assert authority.current == {}
    assert store.loads == store.commits == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [PersistenceUnavailableError("no grant"), asyncio.CancelledError("acquirer cancelled")]
)
async def test_failed_acquisition_has_no_grant_or_orphaned_task(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, error: BaseException
) -> None:
    authority.acquire_error = error
    with pytest.raises(type(error)) as caught:
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert caught.value is error
    assert store.loads == []
    assert authority.acquired == authority.released == []


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["load", "config-load", "config-resolve"])
async def test_pre_execution_cancellation_releases_after_port_coroutines_finish(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, boundary: str
) -> None:
    gate = AsyncGate()
    if boundary == "load":

        async def loaded(_authority: ExecutionAuthority, _children: tuple[ScopeRunCoordinate, ...]) -> NeverCreated:
            await gate.wait()
            return NeverCreated()

        store.on_load = loaded
    else:
        config = config_at(1)
        catalog = ConfigCatalog((config.snapshot,))
        if boundary == "config-load":
            catalog.load_gate = gate
        else:
            catalog.resolve_gate = gate
        agent = replace(agent, config=AgentConfig(catalog, catalog, config.snapshot.key))
    task = asyncio.create_task(agent.run(AgentStart("run", Graph.values(value="input"))))
    await gate.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.finished.is_set()
    assert authority.current == {}
    assert authority.released == authority.acquired
    assert store.commits == []


@pytest.mark.asyncio
async def test_caller_cancellation_joins_nodes_and_commits_abort_before_release(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority
) -> None:
    node_gate, release_gate = AsyncGate(), AsyncGate()
    authority.release_gate = release_gate

    def assemble(_config: Config | None) -> Graph[str]:
        graph = Graph[str]("cancel-node")

        async def node(values: Graph.Values[str]) -> Graph.Values[str]:
            await node_gate.wait()
            return values

        graph.add_node("node", node, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
        graph.set_outputs({"value": graph.output_ref("node", "value")})
        return graph

    agent = replace(agent, assemble=assemble)
    task = asyncio.create_task(agent.run(AgentStart("run", Graph.values(value="input"))))
    await node_gate.entered.wait()
    task.cancel()
    await release_gate.entered.wait()
    assert node_gate.finished.is_set()
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.root_state.status is GraphRunStatus.ABORTED
    assert checkpoint.root_state.execution is None
    assert checkpoint.publications == ()
    assert not task.done()
    release_gate.proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    authority.release_gate = None
    assert isinstance(await agent.run(AgentResume[str]("run")), AgentAborted)
    assert authority.current == {}


@pytest.mark.asyncio
async def test_node_origin_cancellation_does_not_become_an_agent_abort(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority
) -> None:
    cancellation = asyncio.CancelledError("node-origin")

    def assemble(_config: Config | None) -> Graph[str]:
        graph = Graph[str]("node-origin")

        async def node(_values: Graph.Values[str]) -> Graph.Values[str]:
            raise cancellation

        graph.add_node("node", node, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
        graph.set_outputs({"value": graph.output_ref("node", "value")})
        return graph

    with pytest.raises(asyncio.CancelledError):
        await replace(agent, assemble=assemble).run(AgentStart("run", Graph.values(value="input")))
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.root_state.status is GraphRunStatus.RUNNING
    assert checkpoint.root_state.execution is not None
    assert len(store.commits) == 2
    assert authority.released == authority.acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [0, 1, 2, 3, 5, 6])
async def test_commit_origin_cancellation_never_writes_cleanup_from_old_memory(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, revision: int
) -> None:
    cancellation = asyncio.CancelledError("writer-origin")

    async def cancel(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.candidate_state.revision == revision:
            raise cancellation
        return None

    store.on_commit = cancel
    with pytest.raises(asyncio.CancelledError) as caught:
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert caught.value is cancellation
    assert len(store.commits) == revision + 1
    assert len((await store.view(AgentRunKey("agent", GraphRunId("run")))).requests) == revision
    assert authority.released == authority.acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["commit", "reconcile"])
@pytest.mark.parametrize("outcome", ["applied", "unknown-applied", "unknown-unapplied"])
async def test_caller_cancellation_cannot_interrupt_commit_accounting_or_mask_unknown(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, boundary: str, outcome: str
) -> None:
    gate = AsyncGate()

    async def commit(grant: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.candidate_state.revision != 2:
            return None
        if boundary == "commit":
            await gate.wait()
        if outcome != "unknown-unapplied":
            await store.apply(grant, request)
        return CommitUnknown()

    async def reconcile(_grant: ExecutionAuthority, _request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if boundary == "reconcile":
            await gate.wait()
        return None if outcome == "applied" else CommitUnknown()

    store.on_commit, store.on_reconcile = commit, reconcile
    task = asyncio.create_task(agent.run(AgentStart("run", Graph.values(value="input"))))
    await gate.entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert authority.released == []
    assert not task.done()
    gate.proceed.set()
    expected = asyncio.CancelledError if outcome == "applied" else CommitUnresolvedError
    with pytest.raises(expected):
        await task
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    if outcome == "applied":
        assert checkpoint.root_state.status is GraphRunStatus.ABORTED
        assert len(checkpoint.publications) == 1
    else:
        assert checkpoint.root_state.status is GraphRunStatus.RUNNING
        assert checkpoint.root_state.revision == (2 if outcome == "unknown-applied" else 1)
        assert len(store.commits) == 3
    assert gate.finished.is_set()
    assert authority.released == authority.acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_run", [False, True])
async def test_release_failure_preserves_the_primary_error(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, failed_run: bool
) -> None:
    primary = PersistenceUnavailableError("load failed")
    release_error = RuntimeError("release failed")
    authority.release_error = release_error

    async def fail(_authority: ExecutionAuthority, _children: tuple[ScopeRunCoordinate, ...]) -> NeverCreated:
        raise primary

    if failed_run:
        store.on_load = fail
    expected = primary if failed_run else release_error
    with pytest.raises(type(expected)) as caught:
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert caught.value is expected
    if failed_run:
        assert caught.value.__cause__ is release_error
    assert authority.released == authority.acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_run", [False, True])
async def test_cancellation_during_release_is_joined_without_covering_a_primary_error(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, failed_run: bool
) -> None:
    gate = AsyncGate()
    authority.release_gate = gate
    failure = PersistenceUnavailableError("load failed")

    async def fail(_authority: ExecutionAuthority, _children: tuple[ScopeRunCoordinate, ...]) -> NeverCreated:
        raise failure

    if failed_run:
        store.on_load = fail
    task = asyncio.create_task(agent.run(AgentStart("run", Graph.values(value="input"))))
    await gate.entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert authority.current
    gate.proceed.set()
    expected = PersistenceUnavailableError if failed_run else asyncio.CancelledError
    with pytest.raises(expected) as caught:
        await task
    if failed_run:
        assert caught.value is failure
        assert isinstance(caught.value.__cause__, asyncio.CancelledError)
    assert gate.finished.is_set()
    assert authority.released == authority.acquired
    assert authority.current == {}
    if not failed_run:
        authority.release_gate = None
        assert isinstance(await agent.run(AgentResume[str]("run")), AgentCompleted)
