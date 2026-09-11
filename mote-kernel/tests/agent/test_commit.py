from dataclasses import replace

import pytest
from tests.agent.persistence_fixtures import MemoryAuthority, SnapshotPersistence
from tests.execution.persistence_fixtures import capture_graph_input, encode_strings

from mote_kernel.agent import Agent, AgentCompleted, AgentResume, AgentStart
from mote_kernel.execution import Graph
from mote_kernel.execution.persistence import EncodedFrame, GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentRunKey,
    AuthorityLostError,
    CommitApplied,
    CommitAttemptsExhaustedError,
    CommitNotApplied,
    CommitOutcome,
    CommitUnknown,
    CommitUnresolvedError,
    ExecutionAuthority,
    PersistenceConflictError,
    PersistenceContractError,
    PersistenceUnavailableError,
)
from mote_kernel.state.graph_state import GraphRunId


def with_graph_input_value(
    request: GraphPersistenceCommit[str],
    value: str,
) -> GraphPersistenceCommit[str]:
    graph_input = request.writes.graph_inputs[0]
    changed = capture_graph_input(
        graph_input.coordinate,
        EncodedFrame(
            graph_input.frame.codec_id,
            graph_input.frame.codec_version,
            encode_strings(Graph.values(value=value)),
        ),
        graph_input.birth,
    )
    return replace(
        request,
        candidate_state=replace(request.candidate_state, graph_input_evidence=changed.evidence),
        writes=replace(request.writes, graph_inputs=(changed,)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", range(7))
@pytest.mark.parametrize("outcome", ["not-applied", "unknown-not-applied", "unknown-applied"])
async def test_every_transition_retries_or_reconciles_only_the_same_request(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], revision: int, outcome: str
) -> None:
    attempts: list[GraphPersistenceCommit[str]] = []

    async def inject(authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.candidate_state.revision != revision:
            return None
        attempts.append(request)
        if len(attempts) > 1:
            return None
        if outcome == "not-applied":
            return CommitNotApplied()
        if outcome == "unknown-applied":
            await store.apply(authority, request)
        return CommitUnknown()

    store.on_commit = inject
    result = await agent.run(AgentStart("run", Graph.values(value="input")))
    assert isinstance(result, AgentCompleted)
    assert result.outputs["value"] == "input-first-second"
    assert calls == ["first", "second"]
    assert len(attempts) == (1 if outcome == "unknown-applied" else 2)
    assert all(request is attempts[0] for request in attempts)
    assert len(store.reconciles) == (0 if outcome == "not-applied" else 1)
    if store.reconciles:
        grant, request = store.reconciles[0]
        assert request is attempts[0]
        assert all(commit_grant is grant for commit_grant, _request in store.commits)
    family = await store.view(AgentRunKey("agent", GraphRunId("run")))
    assert len(family.requests) == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("max_attempts", [1, 2, 4])
@pytest.mark.parametrize("uncertain", [False, True])
async def test_not_applied_has_a_finite_exact_attempt_budget(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], max_attempts: int, uncertain: bool
) -> None:
    async def reject(_authority: ExecutionAuthority, _request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        return CommitUnknown() if uncertain else CommitNotApplied()

    store.on_commit = reject
    agent = replace(agent, max_commit_attempts=max_attempts)
    with pytest.raises(CommitAttemptsExhaustedError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert len(store.commits) == max_attempts
    assert len(store.reconciles) == (max_attempts if uncertain else 0)
    assert all(request is store.commits[0][1] for _grant, request in store.commits)
    assert calls == []
    assert (await store.view(AgentRunKey("agent", GraphRunId("run")))).requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("applied", [False, True])
@pytest.mark.parametrize("revision", range(7))
async def test_unknown_stops_without_cleanup_writes_and_a_fresh_call_rereads(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], applied: bool, revision: int
) -> None:
    async def lose_ack(
        authority: ExecutionAuthority, request: GraphPersistenceCommit[str]
    ) -> CommitOutcome[str] | None:
        if request.candidate_state.revision != revision:
            return None
        if applied:
            await store.apply(authority, request)
        return CommitUnknown()

    async def unknown(_authority: ExecutionAuthority, _request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        return CommitUnknown()

    store.on_commit, store.on_reconcile = lose_ack, unknown
    with pytest.raises(CommitUnresolvedError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert store.commits[-1][1].candidate_state.revision == revision
    assert len(store.reconciles) == 1
    assert store.reconciles[0][1] is store.commits[-1][1]
    before = list(calls)
    family = await store.view(AgentRunKey("agent", GraphRunId("run")))
    assert len(family.requests) == revision + int(applied)
    store.on_commit = store.on_reconcile = None
    resumed = (
        await agent.run(AgentStart("run", Graph.values(value="input")))
        if revision == 0 and not applied
        else await agent.run(AgentResume[str]("run"))
    )
    assert isinstance(resumed, AgentCompleted)
    if applied and revision >= 2:
        assert calls.count("first") == 1
    if applied and revision >= 5:
        assert calls == before


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [0, 1, 2, 3, 4, 5, 6])
async def test_revoked_authority_never_installs_or_rebases_a_candidate(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, calls: list[str], revision: int
) -> None:
    async def revoke(grant: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.candidate_state.revision == revision:
            del authority.current[grant.run]
        return None

    store.on_commit = revoke
    with pytest.raises(AuthorityLostError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert len(store.commits) == revision + 1
    assert store.reconciles == []
    assert len((await store.view(AgentRunKey("agent", GraphRunId("run")))).requests) == revision
    assert calls == (["first", "second"] if revision >= 5 else ["first"] if revision >= 2 else [])
    assert authority.released == authority.acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [AuthorityLostError("revoked"), PersistenceUnavailableError("unavailable")])
async def test_reconciliation_failure_never_resends_or_covers_the_original_boundary(
    agent: Agent[str], store: SnapshotPersistence[str], failure: Exception
) -> None:
    async def unknown(_authority: ExecutionAuthority, _request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        return CommitUnknown()

    async def fail(_authority: ExecutionAuthority, _request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        raise failure

    store.on_commit, store.on_reconcile = unknown, fail
    with pytest.raises(type(failure)) as caught:
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert caught.value is failure
    assert len(store.commits) == len(store.reconciles) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("conflicting", [False, True])
async def test_create_if_absent_uses_complete_content_identity(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], conflicting: bool
) -> None:
    async def race(authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.expected_revision is not None:
            return None
        raced = with_graph_input_value(request, "other") if conflicting else request
        await store.apply(authority, raced)
        return None

    store.on_commit = race
    if conflicting:
        with pytest.raises(PersistenceConflictError):
            await agent.run(AgentStart("run", Graph.values(value="input")))
        assert calls == []
        assert len(store.commits) == 1
    else:
        assert isinstance(await agent.run(AgentStart("run", Graph.values(value="input"))), AgentCompleted)
        assert calls == ["first", "second"]
        assert len((await store.view(AgentRunKey("agent", GraphRunId("run")))).requests) == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("reconciled", [False, True])
async def test_mismatched_receipt_is_not_treated_as_retry_permission(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], reconciled: bool
) -> None:
    async def mismatch(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        return CommitApplied(with_graph_input_value(request, "mismatched"))

    async def unknown(_authority: ExecutionAuthority, _request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        return CommitUnknown()

    store.on_commit = unknown if reconciled else mismatch
    store.on_reconcile = mismatch
    with pytest.raises(Graph.SnapshotMismatchError, match="exact state"):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert calls == []
    assert len(store.commits) == 1
    assert len(store.reconciles) == int(reconciled)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["commit", "reconcile"])
async def test_persistence_cannot_mutate_the_request_during_retry_or_reconciliation(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    calls: list[str],
    boundary: str,
) -> None:
    async def mutate(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        object.__setattr__(request.writes.graph_inputs[0].frame, "payload", b'{"value":"mutated"}')
        return CommitNotApplied()

    async def unknown(_authority: ExecutionAuthority, _request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        return CommitUnknown()

    store.on_commit = mutate if boundary == "commit" else unknown
    store.on_reconcile = mutate
    with pytest.raises(PersistenceContractError, match="mutated"):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert len(store.commits) == 1
    assert len(store.reconciles) == int(boundary == "reconcile")
    assert calls == []
