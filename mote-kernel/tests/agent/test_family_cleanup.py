import asyncio
from dataclasses import replace

import pytest
from tests.agent.persistence_fixtures import MemoryAuthority, SnapshotPersistence
from tests.execution.persistence_fixtures import nested_graph
from tests.execution.test_family_driver_local_ownership import fail_owner_construction

from mote_kernel.agent import Agent, AgentCompleted, AgentFailed, AgentResume, AgentStart
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.commit import GraphCommitError
from mote_kernel.execution.persistence import GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentRunKey,
    CommitOutcome,
    CommitUnknown,
    CommitUnresolvedError,
    ExecutionAuthority,
    PersistenceUnavailableError,
)
from mote_kernel.state.graph_state import GraphRunId, GraphRunStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["fresh-root", "fresh-child", "continued-root", "continued-child"])
async def test_construction_cleanup_unknown_stops_before_ancestor_writes(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    depth = 0 if boundary.endswith("root") else 1

    def assemble(_config: Config | None) -> Graph[str]:
        return nested_graph(calls, depth=depth)

    agent = replace(agent, assemble=assemble)
    continuing = boundary.startswith("continued")
    if continuing:

        async def stop_claim(
            _authority: ExecutionAuthority, request: GraphPersistenceCommit[str]
        ) -> CommitOutcome[str] | None:
            if request.candidate_state.execution is not None:
                raise PersistenceUnavailableError("claim was not written")
            return None

        store.on_commit = stop_claim
        with pytest.raises(PersistenceUnavailableError):
            await agent.run(AgentStart("run", Graph.values(value="input")))

    unknown: list[GraphPersistenceCommit[str]] = []

    async def lose_abort(
        _authority: ExecutionAuthority, request: GraphPersistenceCommit[str]
    ) -> CommitOutcome[str] | None:
        if request.candidate_state.status is GraphRunStatus.ABORTED:
            unknown.append(request)
            return CommitUnknown()
        return None

    async def unresolved(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        assert request is unknown[0]
        return CommitUnknown()

    store.on_commit, store.on_reconcile = lose_abort, unresolved
    with monkeypatch.context() as patch:
        fail_owner_construction(patch, RuntimeError("owner construction failed"), scope_depth=depth)
        with pytest.raises(CommitUnresolvedError):
            if continuing:
                await agent.run(AgentResume[str]("run"))
            else:
                await agent.run(AgentStart("run", Graph.values(value="input")))

    assert len(unknown) == 1
    assert len(unknown[0].scope) == depth
    assert store.commits[-1][1] is unknown[0]
    assert store.reconciles[-1][1] is unknown[0]
    assert authority.current == {}
    assert authority.released == authority.acquired
    assert calls == []
    store.on_commit = store.on_reconcile = None
    completed = await replace(agent).run(AgentResume[str]("run"))
    assert isinstance(completed, AgentCompleted)
    assert completed.outputs["value"] == "input-first-second"
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_status", [GraphRunStatus.RUNNING, GraphRunStatus.ABORTED], ids=["fence", "abort"])
@pytest.mark.parametrize("applied", [False, True], ids=["unwritten", "written"])
async def test_cancelled_family_stops_durable_cleanup_at_first_unknown(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    failed_status: GraphRunStatus,
    applied: bool,
) -> None:
    entered, released = asyncio.Event(), asyncio.Event()
    started: list[str] = []
    finished: list[str] = []

    def assemble(_config: Config | None) -> Graph[str]:
        async def node(values: Graph.Values[str]) -> Graph.Values[str]:
            started.append(values["value"])
            if len(started) == 2:
                entered.set()
            try:
                await released.wait()
            finally:
                finished.append(values["value"])
            return values

        child = Graph[str]("cleanup.child")
        child.add_node("node", node, inputs={"value": child.graph_input("value", str)}, outputs={"value": str})
        child.set_outputs({"value": child.output_ref("node", "value")})
        graph = Graph[str]("cleanup.root")
        for name in ("left", "right"):
            graph.add_node(name, child, inputs={"value": graph.graph_input("value", str)})
        graph.add_join(("left", "right"), Graph.END)
        graph.set_outputs({"left": graph.output_ref("left", "value"), "right": graph.output_ref("right", "value")})
        return graph

    agent = replace(agent, assemble=assemble)
    task = asyncio.create_task(agent.run(AgentStart("run", Graph.values(value="input"))))
    await entered.wait()
    before = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint().root_state
    unknown: list[GraphPersistenceCommit[str]] = []

    async def stop_cleanup(
        grant: ExecutionAuthority, request: GraphPersistenceCommit[str]
    ) -> CommitOutcome[str] | None:
        if request.scope and request.candidate_state.status is failed_status:
            unknown.append(request)
            if applied:
                await store.apply(grant, request)
            return CommitUnknown()
        return None

    async def unresolved(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        assert request is unknown[0]
        return CommitUnknown()

    store.on_commit, store.on_reconcile = stop_cleanup, unresolved
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert isinstance(caught.value.__cause__, GraphCommitError)
    assert isinstance(caught.value.__cause__.cause, CommitUnresolvedError)
    assert len(unknown) == 1
    assert store.commits[-1][1] is unknown[0]
    assert finished == started == ["input", "input"]
    assert authority.current == {}
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.root_state.status is GraphRunStatus.RUNNING
    assert checkpoint.root_state == before
    assert checkpoint.publications == ()
    store.on_commit = store.on_reconcile = None
    released.set()
    recovered = await replace(agent).run(AgentResume[str]("run"))
    if applied and failed_status is GraphRunStatus.ABORTED:
        assert isinstance(recovered, AgentFailed)
        assert len(recovered.failures) == 1
        assert recovered.failures[0].node_id == "left"
        assert recovered.failures[0].failure == "graph invocation was cancelled"
    else:
        assert isinstance(recovered, AgentCompleted)
        assert dict(recovered.outputs.items()) == {"left": "input", "right": "input"}
    assert authority.current == {}
