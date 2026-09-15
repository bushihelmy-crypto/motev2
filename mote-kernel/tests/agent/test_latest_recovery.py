import asyncio
import json
from dataclasses import replace
from typing import cast

import pytest
from tests.agent.config_fixtures import ConfigCatalog
from tests.agent.persistence_fixtures import AsyncGate, MemoryAuthority, SnapshotPersistence
from tests.execution.persistence_fixtures import STRING_CODEC, interrupt_graph, nested_graph
from tests.execution.test_persistence_config import config_at

from mote_kernel.agent import (
    Agent,
    AgentAborted,
    AgentAnswer,
    AgentCompleted,
    AgentConfig,
    AgentContractError,
    AgentFailed,
    AgentInterrupted,
    AgentResume,
    AgentRunNotFoundError,
    AgentStart,
)
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentLatestHead,
    AgentRunKey,
    CommitApplied,
    CommitAttemptsExhaustedError,
    CommitNotApplied,
    CommitOutcome,
    CommitUnknown,
    CommitUnresolvedError,
    ExecutionAuthority,
    LatestHeadMovedError,
    NeverCreated,
    PersistenceContractError,
    PersistenceTombstoneError,
)
from mote_kernel.session import AgentSession, AgentSessionCodec
from mote_kernel.state.graph_state import GraphRunId


def _session_codec() -> AgentSessionCodec[str, str]:
    def encode(hook_state: str, context: str) -> bytes:
        return json.dumps((hook_state, context), separators=(",", ":")).encode()

    def decode(payload: bytes) -> tuple[str, str]:
        decoded: object = json.loads(payload)
        if type(decoded) is not list:
            raise ValueError("session payload must be a list")
        values = cast(list[object], decoded)
        if len(values) != 2 or any(type(value) is not str for value in values):
            raise ValueError("session payload must contain two strings")
        return cast(str, values[0]), cast(str, values[1])

    return AgentSessionCodec("test.latest-session", 1, encode, decode)


def _session_agent(
    agent: Agent[str],
    store: SnapshotPersistence[str],
) -> Agent[str, str, str]:
    return Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )


@pytest.mark.asyncio
async def test_resume_without_run_id_recovers_latest_state_and_session(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    calls: list[str],
) -> None:
    configured = _session_agent(agent, store)
    old_session = AgentSession("old-hook", "old-context")
    latest_session = AgentSession("latest-hook", "latest-context")
    await configured.run(AgentStart("old", Graph.values(value="old"), old_session))
    latest = await configured.run(AgentStart("latest", Graph.values(value="latest"), latest_session))
    assert isinstance(latest, AgentCompleted)
    head = AgentLatestHead(AgentRunKey("agent", GraphRunId("latest")), 2)
    assert store.heads == {"agent": head}
    writes = len(store.commits)
    node_calls = tuple(calls)

    omitted = await configured.run(AgentResume())
    explicit_none = await configured.run(AgentResume(None))

    for recovered in (omitted, explicit_none):
        assert isinstance(recovered, AgentCompleted)
        assert recovered.run_id == "latest"
        assert recovered.outputs["value"] == "latest-first-second"
        assert recovered.session == latest_session
    assert store.latest_loads == ["agent", "agent"]
    assert store.load_fences[-2:] == [head, head]
    assert len(store.commits) == writes
    assert tuple(calls) == node_calls


@pytest.mark.asyncio
async def test_explicit_historical_resume_bypasses_latest_and_does_not_retrograde_head(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    calls: list[str],
) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        return interrupt_graph(calls)

    configured = replace(agent, assemble=assemble)
    waiting = await configured.run(AgentStart("old", Graph.values(value="question")))
    assert isinstance(waiting, AgentInterrupted)
    latest_result = await configured.run(AgentStart("latest", Graph.values(value="latest")))
    assert isinstance(latest_result, AgentCompleted)
    head = store.heads["agent"]
    writes = len(store.commits)

    recovered = await configured.run(
        AgentResume(
            "old",
            (AgentAnswer(waiting.interrupts[0], Graph.values(value="answer")),),
        )
    )

    assert isinstance(recovered, AgentCompleted)
    assert recovered.run_id == "old"
    assert recovered.outputs["value"] == "answer"
    assert len(store.commits) > writes
    assert store.latest_loads == []
    assert store.load_fences[-1] is None
    assert store.heads["agent"] == head

    latest_replay = await configured.run(AgentResume())
    assert isinstance(latest_replay, AgentCompleted)
    assert latest_replay.run_id == "latest"
    assert latest_replay.outputs["value"] == "latest"


@pytest.mark.asyncio
async def test_latest_resume_can_answer_the_latest_interrupt_without_an_id(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    calls: list[str],
) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        return interrupt_graph(calls)

    interrupted_agent = replace(agent, assemble=assemble)
    waiting = await interrupted_agent.run(AgentStart("run", Graph.values(value="question")))
    assert isinstance(waiting, AgentInterrupted)

    recovered = await interrupted_agent.run(AgentResume())
    assert isinstance(recovered, AgentInterrupted)
    assert recovered.run_id == "run"
    assert recovered.interrupts == waiting.interrupts

    answered = await interrupted_agent.run(
        AgentResume(answers=(AgentAnswer(waiting.interrupts[0], Graph.values(value="answer")),))
    )

    assert isinstance(answered, AgentCompleted)
    assert answered.run_id == "run"
    assert answered.outputs["value"] == "answer"


@pytest.mark.asyncio
async def test_answer_from_an_old_run_cannot_be_applied_to_a_new_latest_run(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    calls: list[str],
) -> None:
    def interrupt_assemble(_config: Config | None) -> Graph[str]:
        return interrupt_graph(calls)

    interrupted_agent = replace(agent, assemble=interrupt_assemble)
    waiting = await interrupted_agent.run(AgentStart("old", Graph.values(value="question")))
    assert isinstance(waiting, AgentInterrupted)
    answer = AgentAnswer(waiting.interrupts[0], Graph.values(value="answer"))

    latest_waiting = await interrupted_agent.run(AgentStart("new", Graph.values(value="question")))
    assert isinstance(latest_waiting, AgentInterrupted)
    assert latest_waiting.interrupts[0].interrupt_id != waiting.interrupts[0].interrupt_id
    writes = len(store.commits)
    calls_before = tuple(calls)

    with pytest.raises(Graph.Error):
        await interrupted_agent.run(AgentResume(answers=(answer,)))

    assert len(store.commits) == writes
    assert tuple(calls) == calls_before


@pytest.mark.asyncio
async def test_root_reconcile_requires_latest_head_confirmation(
    agent: Agent[str],
    store: SnapshotPersistence[str],
) -> None:
    async def write_family_then_lose_latest(
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[str],
    ) -> CommitOutcome[str]:
        await store.apply(authority, request)
        store.heads.pop(authority.run.agent_id, None)
        return CommitUnknown()

    store.on_commit = write_family_then_lose_latest
    with pytest.raises(CommitUnresolvedError):
        await agent.run(AgentStart("run", Graph.values(value="input")))

    family = await store.view(AgentRunKey("agent", GraphRunId("run")))
    assert family.requests
    assert store.heads == {}
    assert len(store.reconciles) == 1


@pytest.mark.asyncio
async def test_latest_resume_uses_the_checkpoint_config_cursor_not_config_latest(
    agent: Agent[str],
    store: SnapshotPersistence[str],
) -> None:
    historical = config_at(1, definition="latest-config")
    unrelated_latest = config_at(9, definition="latest-config")
    catalog = ConfigCatalog((historical.snapshot, unrelated_latest.snapshot))
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        config=AgentConfig(catalog, catalog),
        session_codec=_session_codec(),
    )
    session = AgentSession("hook", "context", historical)
    await configured.run(AgentStart("run", Graph.values(value="input"), session))
    catalog.loads.clear()

    recovered = await configured.run(AgentResume())

    assert isinstance(recovered, AgentCompleted)
    assert recovered.session is not None
    assert recovered.session.config is not None
    assert recovered.session.config.snapshot == historical.snapshot
    assert catalog.loads == [historical.snapshot.key]
    assert unrelated_latest.snapshot.key not in catalog.loads


@pytest.mark.asyncio
async def test_missing_latest_is_not_treated_as_a_start(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    calls: list[str],
) -> None:
    with pytest.raises(AgentRunNotFoundError, match="no latest run"):
        await agent.run(AgentResume())

    assert store.latest_loads == ["agent"]
    assert store.loads == store.commits == []
    assert authority.acquired == authority.released == []
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("move", ["different-key", "aba"])
async def test_latest_head_move_between_lookup_and_family_load_fails_closed(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    calls: list[str],
    move: str,
) -> None:
    await agent.run(AgentStart("old", Graph.values(value="old")))
    resolved = store.heads["agent"]
    writes = len(store.commits)
    node_calls = tuple(calls)

    async def move_head(
        _authority: ExecutionAuthority,
        children: tuple[ScopeRunCoordinate, ...],
    ) -> NeverCreated | None:
        assert children == ()
        key = AgentRunKey("agent", GraphRunId("new")) if move == "different-key" else resolved.key
        generation = resolved.generation + (1 if move == "different-key" else 2)
        store.heads["agent"] = AgentLatestHead(key, generation)
        return None

    store.on_load = move_head
    with pytest.raises(LatestHeadMovedError):
        await agent.run(AgentResume())

    assert len(store.commits) == writes
    assert tuple(calls) == node_calls
    assert authority.released == authority.acquired
    assert authority.current == {}


@pytest.mark.asyncio
async def test_latest_head_race_after_lookup_before_acquire_fails_closed(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    calls: list[str],
) -> None:
    await agent.run(AgentStart("old", Graph.values(value="old")))
    resolved = store.heads["agent"]

    async def move_after_lookup(_agent_id: str) -> AgentLatestHead:
        moved = AgentLatestHead(AgentRunKey("agent", GraphRunId("new")), resolved.generation + 1)
        store.heads["agent"] = moved
        return resolved

    store.on_load_latest = move_after_lookup
    writes = len(store.commits)
    node_calls = tuple(calls)

    with pytest.raises(LatestHeadMovedError):
        await agent.run(AgentResume())

    assert len(store.commits) == writes
    assert tuple(calls) == node_calls
    assert authority.released == authority.acquired
    assert authority.current == {}


@pytest.mark.asyncio
async def test_latest_head_pointing_to_a_missing_family_fails_closed(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
) -> None:
    key = AgentRunKey("agent", GraphRunId("missing"))
    store.heads["agent"] = AgentLatestHead(key, 1)

    with pytest.raises(PersistenceContractError, match="missing durable family"):
        await agent.run(AgentResume())

    assert [grant.run for grant in authority.acquired] == [key]
    assert authority.released == authority.acquired
    assert store.commits == []


@pytest.mark.asyncio
async def test_latest_head_tombstone_is_not_reinterpreted_as_a_missing_run(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
) -> None:
    await agent.run(AgentStart("run", Graph.values(value="input")))
    writes = len(store.commits)
    store.tombstones.add(AgentRunKey("agent", GraphRunId("run")))

    with pytest.raises(PersistenceTombstoneError):
        await agent.run(AgentResume())

    assert authority.released == authority.acquired
    assert len(store.commits) == writes


@pytest.mark.asyncio
async def test_latest_fence_is_reused_for_a_pending_child_reread(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    calls: list[str],
) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        return nested_graph(calls)

    async def stop_child(
        _authority: ExecutionAuthority,
        request: GraphPersistenceCommit[str],
    ) -> CommitOutcome[str] | None:
        if request.scope:
            raise OSError("stop before child creation")
        return None

    nested = replace(agent, assemble=assemble)
    store.on_commit = stop_child
    with pytest.raises(OSError, match="child creation"):
        await nested.run(AgentStart("run", Graph.values(value="input")))
    head = store.heads["agent"]
    store.on_commit = None

    result = await nested.run(AgentResume())

    assert isinstance(result, AgentCompleted)
    assert result.run_id == "run"
    assert result.outputs["value"] == "input-first-second"
    assert store.load_fences[-2:] == [head, head]
    assert store.loads[-2][1] == ()
    assert len(store.loads[-1][1]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["not-applied", "unknown"])
async def test_unapplied_root_start_does_not_move_latest(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    outcome: str,
) -> None:
    await agent.run(AgentStart("old", Graph.values(value="old")))
    head = store.heads["agent"]

    async def reject(
        _authority: ExecutionAuthority,
        _request: GraphPersistenceCommit[str],
    ) -> CommitOutcome[str]:
        return CommitNotApplied() if outcome == "not-applied" else CommitUnknown()

    store.on_commit = reject
    store.on_reconcile = reject
    expected = CommitAttemptsExhaustedError if outcome == "not-applied" else CommitUnresolvedError
    with pytest.raises(expected):
        await agent.run(AgentStart("new", Graph.values(value="new")))

    assert store.heads["agent"] == head
    assert not (await store.view(AgentRunKey("agent", GraphRunId("new")))).requests


@pytest.mark.asyncio
async def test_root_family_and_latest_head_reconcile_as_one_applied_fact(
    agent: Agent[str],
    store: SnapshotPersistence[str],
) -> None:
    await agent.run(AgentStart("old", Graph.values(value="old")))
    old_head = store.heads["agent"]

    async def apply_then_report_unknown(
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[str],
    ) -> CommitOutcome[str] | None:
        if request.expected_revision is None:
            await store.apply(authority, request)
            return CommitUnknown()
        return None

    store.on_commit = apply_then_report_unknown
    result = await agent.run(AgentStart("new", Graph.values(value="new")))

    assert isinstance(result, AgentCompleted)
    new_key = AgentRunKey("agent", GraphRunId("new"))
    assert store.heads["agent"] == AgentLatestHead(new_key, old_head.generation + 1)
    family = await store.view(new_key)
    assert family.requests
    assert len(store.reconciles) == 1
    root_request = next(
        request
        for _authority, request in store.commits
        if request.expected_revision is None and request.candidate_state.run_id == GraphRunId("new")
    )
    assert store.reconciles[0][1] == root_request

    writes = len(store.commits)
    replay = await agent.run(AgentResume())
    assert isinstance(replay, AgentCompleted)
    assert replay.run_id == "new"
    assert len(store.commits) == writes


@pytest.mark.asyncio
async def test_historical_root_reconcile_survives_a_later_latest_head(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
) -> None:
    await agent.run(AgentStart("old", Graph.values(value="old")))
    old_key = AgentRunKey("agent", GraphRunId("old"))
    old_root = next(
        request for grant, request in store.commits if grant.run == old_key and request.candidate_state.revision == 0
    )

    async def lose_ack(
        _authority: ExecutionAuthority,
        _request: GraphPersistenceCommit[str],
    ) -> CommitOutcome[str]:
        return CommitUnknown()

    old_grant = await authority.acquire(old_key)
    store.on_commit = lose_ack
    try:
        outcome = await store.commit(old_grant, old_root)
    finally:
        store.on_commit = None
        await authority.release(old_grant)
    assert isinstance(outcome, CommitUnknown)

    await agent.run(AgentStart("new", Graph.values(value="new")))
    new_head = store.heads["agent"]
    assert new_head.key == AgentRunKey("agent", GraphRunId("new"))

    old_grant = await authority.acquire(old_key)
    try:
        reconciled = await store.reconcile(old_grant, old_root)
    finally:
        await authority.release(old_grant)

    assert isinstance(reconciled, CommitApplied)
    assert reconciled.confirmed == old_root
    assert store.heads["agent"] == new_head


@pytest.mark.asyncio
async def test_reconcile_unknown_after_durable_root_write_remains_unresolved(
    agent: Agent[str],
    store: SnapshotPersistence[str],
) -> None:
    await agent.run(AgentStart("old", Graph.values(value="old")))
    old_head = store.heads["agent"]

    async def apply_then_unknown(
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[str],
    ) -> CommitOutcome[str] | None:
        if request.expected_revision is None:
            await store.apply(authority, request)
            return CommitUnknown()
        return None

    async def remain_unknown(
        _authority: ExecutionAuthority,
        _request: GraphPersistenceCommit[str],
    ) -> CommitOutcome[str]:
        return CommitUnknown()

    store.on_commit = apply_then_unknown
    store.on_reconcile = remain_unknown
    with pytest.raises(CommitUnresolvedError):
        await agent.run(AgentStart("new", Graph.values(value="new")))

    new_key = AgentRunKey("agent", GraphRunId("new"))
    assert store.heads["agent"] == AgentLatestHead(new_key, old_head.generation + 1)
    assert (await store.view(new_key)).requests
    assert len(store.reconciles) == 1


@pytest.mark.asyncio
async def test_root_commit_replay_does_not_increment_latest_generation(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
) -> None:
    await agent.run(AgentStart("run", Graph.values(value="input")))
    key = AgentRunKey("agent", GraphRunId("run"))
    root = next(request for _grant, request in store.commits if request.candidate_state.revision == 0)
    head = store.heads["agent"]
    grant = await authority.acquire(key)
    try:
        assert await store.apply(grant, root) == root
    finally:
        await authority.release(grant)

    assert store.heads["agent"] == head


@pytest.mark.asyncio
async def test_latest_heads_are_isolated_by_agent_namespace(
    agent: Agent[str],
    store: SnapshotPersistence[str],
) -> None:
    other = replace(agent, agent_id="other")
    await agent.run(AgentStart("same-run", Graph.values(value="one")))
    await other.run(AgentStart("same-run", Graph.values(value="two")))

    first, second = await agent.run(AgentResume()), await other.run(AgentResume())

    assert isinstance(first, AgentCompleted) and first.outputs["value"] == "one-first-second"
    assert isinstance(second, AgentCompleted) and second.outputs["value"] == "two-first-second"
    assert first.run_id == second.run_id == "same-run"
    assert store.latest_loads == ["agent", "other"]
    assert set(store.heads) == {"agent", "other"}


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["failed", "aborted"])
async def test_latest_resume_replays_failed_and_aborted_terminal_results_without_an_id(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    calls: list[str],
    terminal: str,
) -> None:
    gate = AsyncGate()

    def assemble(_config: Config | None) -> Graph[str]:
        graph = Graph[str](f"latest-{terminal}")

        async def node(values: Graph.Values[str]) -> Graph.Outcome[str]:
            calls.append("node")
            if terminal == "aborted":
                await gate.wait()
            return Graph.failure(values["value"])

        graph.add_node("node", node, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
        graph.add_edge(Graph.START, "node")
        graph.set_outputs({"value": graph.output_ref("node", "value")})
        return graph

    terminal_agent = replace(agent, assemble=assemble)
    if terminal == "aborted":
        task = asyncio.create_task(terminal_agent.run(AgentStart("run", Graph.values(value="input"))))
        await gate.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        first = await terminal_agent.run(AgentResume())
    else:
        first = await terminal_agent.run(AgentStart("run", Graph.values(value="input")))

    if terminal == "failed":
        assert isinstance(first, AgentFailed)
    else:
        assert isinstance(first, AgentAborted)
    assert first.run_id == "run"
    writes = len(store.commits)
    observed_calls = tuple(calls)

    repeated = await terminal_agent.run(AgentResume())

    assert repeated == first
    assert repeated.run_id == "run"
    assert len(store.commits) == writes
    assert tuple(calls) == observed_calls


@pytest.mark.parametrize("run_id", ["", " run ", 0, True, object()])
def test_explicit_resume_identity_must_be_canonical(run_id: object) -> None:
    with pytest.raises(AgentContractError, match="canonical or omitted"):
        AgentResume(cast(str, run_id))


class _UnsupportedLatestHead(AgentLatestHead):
    pass


def test_latest_head_is_an_exact_index_with_a_positive_generation() -> None:
    key = AgentRunKey("agent", GraphRunId("run"))
    assert AgentLatestHead.admit(AgentLatestHead(key, 1)) == AgentLatestHead(key, 1)
    with pytest.raises(PersistenceContractError, match="exact Agent run key"):
        AgentLatestHead(cast(AgentRunKey, object()), 1)
    with pytest.raises(PersistenceContractError, match="positive integer"):
        AgentLatestHead(key, 0)
    with pytest.raises(PersistenceContractError, match="positive integer"):
        AgentLatestHead(key, cast(int, True))
    with pytest.raises(PersistenceContractError, match="unsupported latest head"):
        AgentLatestHead.admit(_UnsupportedLatestHead(key, 1))
    malformed = object.__new__(AgentLatestHead)
    with pytest.raises(PersistenceContractError, match="malformed latest head"):
        AgentLatestHead.admit(malformed)


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["untyped", "foreign", "malformed"])
async def test_latest_lookup_response_is_readmitted_before_authority(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    variant: str,
) -> None:
    async def malformed(_agent_id: str) -> AgentLatestHead | NeverCreated:
        if variant == "foreign":
            return AgentLatestHead(AgentRunKey("other", GraphRunId("run")), 1)
        if variant == "malformed":
            return object.__new__(AgentLatestHead)
        return cast(AgentLatestHead, object())

    store.on_load_latest = malformed
    with pytest.raises(PersistenceContractError):
        await agent.run(AgentResume())

    assert authority.acquired == []
    assert store.loads == store.commits == []
