from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from tests.agent.persistence_fixtures import MemoryAuthority, SnapshotPersistence
from tests.execution.persistence_fixtures import linear_graph
from tests.execution.test_persistence_config import config_at

from mote_kernel.agent import (
    Agent,
    AgentAnswer,
    AgentContractError,
    AgentResume,
    AgentStart,
)
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.values import _GraphValues, _make_single_graph_value
from mote_kernel.execution.graph_result import GraphInterruptView
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import DurableGraphCommit, GraphCheckpoint, GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentLatestHead,
    AgentRunKey,
    AuthorityLostError,
    CommitApplied,
    CommitOutcome,
    CommitUnknown,
    ExecutionAuthority,
    NeverCreated,
    PersistenceContractError,
    PersistenceTombstoneError,
    PersistenceUnavailableError,
)
from mote_kernel.state.graph_state import GraphInterruptId, GraphRunId


class NoncallablePersistence:
    load_latest = None
    load = None
    commit = None
    reconcile = None


class NoncallableAuthority:
    acquire = None
    release = None


class UnsupportedApplied(CommitApplied[str]):
    pass


class UnsupportedAnswer(AgentAnswer[str]):
    pass


class UnsupportedCheckpoint(GraphCheckpoint[str]):
    pass


@pytest.mark.parametrize("key", [None, object(), "run"])
def test_authority_cannot_be_constructed_without_an_exact_run_key(key: object) -> None:
    with pytest.raises(PersistenceContractError, match="exact Agent run key"):
        ExecutionAuthority(cast(AgentRunKey, key), b"grant")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agent_id", ""),
        ("agent_id", " spaced "),
        ("agent_id", 1),
        ("assemble", None),
        ("codec", None),
        ("persistence", None),
        ("persistence", object()),
        ("persistence", NoncallablePersistence()),
        ("authority", None),
        ("authority", object()),
        ("authority", NoncallableAuthority()),
        ("config", object()),
        ("max_commit_attempts", True),
        ("max_commit_attempts", 0),
        ("max_commit_attempts", -1),
        ("max_commit_attempts", 1.5),
        ("limits", None),
    ],
)
def test_required_capabilities_and_immutable_policy_fail_assembly(
    agent: Agent[str], authority: MemoryAuthority, field: str, value: object
) -> None:
    with pytest.raises(AgentContractError):
        replace(agent, **{field: value})
    assert authority.acquired == []


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["", " run ", None, 0, True])
async def test_invalid_run_identity_fails_before_acquisition(
    agent: Agent[str], authority: MemoryAuthority, run_id: str
) -> None:
    with pytest.raises(PersistenceContractError):
        await agent.run(AgentStart(run_id, Graph.values(value="input")))
    assert authority.acquired == []


@pytest.mark.asyncio
async def test_business_requests_cannot_smuggle_config_or_untyped_frames(
    agent: Agent[str], authority: MemoryAuthority
) -> None:
    config_values = _make_single_graph_value("value", "input", config_at(1))
    with pytest.raises(AgentContractError, match="Config"):
        await agent.run(AgentStart("run", config_values))
    with pytest.raises(Graph.ValueAdmissionError):
        await agent.run(AgentStart("run", cast(Graph.Values[str], {"value": "input"})))
    with pytest.raises(AgentContractError):
        await agent.run(cast(AgentStart[str], object()))
    assert authority.acquired == []


def test_answers_require_typed_questions_and_immutable_business_values() -> None:
    question = GraphInterruptView((), "ask", GraphInterruptId("interrupt"), b"question")
    with pytest.raises(AgentContractError):
        AgentAnswer(cast(GraphInterruptView, object()), Graph.values(value="answer"))
    with pytest.raises(AgentContractError, match="Config"):
        AgentAnswer(question, _make_single_graph_value("value", "answer", config_at(1)))
    answer = AgentAnswer(question, Graph.values(value="answer"))
    with pytest.raises(AgentContractError):
        AgentResume("run", cast(tuple[AgentAnswer[str], ...], [answer]))
    with pytest.raises(AgentContractError):
        AgentResume("run", (cast(AgentAnswer[str], object()),))


def test_answer_and_interrupt_exact_objects_with_missing_fields_fail_in_the_agent_contract() -> None:
    interrupt = object.__new__(GraphInterruptView)
    with pytest.raises(AgentContractError):
        AgentAnswer(interrupt, Graph.values(value="answer"))

    answer = cast(AgentAnswer[str], object.__new__(AgentAnswer))
    with pytest.raises(AgentContractError):
        answer.admit()

    unsupported = UnsupportedAnswer(
        GraphInterruptView((), "ask", GraphInterruptId("interrupt"), b"question"),
        Graph.values(value="answer"),
    )
    with pytest.raises(AgentContractError, match="exact typed records"):
        unsupported.admit()


def test_agent_run_key_readmission_rejects_an_incomplete_exact_record() -> None:
    with pytest.raises(PersistenceContractError, match="run key is malformed"):
        AgentRunKey.admit(object.__new__(AgentRunKey))


@pytest.mark.asyncio
async def test_graph_values_with_missing_fields_fail_before_authority_acquisition(
    agent: Agent[str],
    authority: MemoryAuthority,
) -> None:
    values = cast(Graph.Values[str], object.__new__(_GraphValues))
    with pytest.raises(Graph.ValueAdmissionError):
        await agent.run(AgentStart("run", values))
    assert authority.acquired == []


def test_applied_outcomes_with_missing_or_untyped_confirmations_fail_in_the_persistence_contract() -> None:
    malformed = cast(CommitApplied[str], object.__new__(CommitApplied))
    with pytest.raises(PersistenceContractError):
        malformed.admit()
    with pytest.raises(PersistenceContractError):
        CommitApplied(cast(GraphPersistenceCommit[str], object()))
    malformed_confirmation = cast(GraphPersistenceCommit[str], object.__new__(GraphPersistenceCommit))
    with pytest.raises(PersistenceContractError):
        CommitApplied(malformed_confirmation)
    unsupported = cast(CommitApplied[str], object.__new__(UnsupportedApplied))
    with pytest.raises(PersistenceContractError, match="unsupported Applied variant"):
        unsupported.admit()


@pytest.mark.asyncio
async def test_authorized_writer_rejects_a_malformed_graph_request_before_persistence(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_malformed_request(
        durable: DurableGraphCommit[str],
        _transition: Graph.Transition[str],
    ) -> Graph.State:
        await durable.writer(cast(GraphPersistenceCommit[str], object.__new__(GraphPersistenceCommit)))
        raise AssertionError("malformed Graph request was accepted")

    monkeypatch.setattr(DurableGraphCommit, "__call__", send_malformed_request)

    with pytest.raises(PersistenceContractError, match="malformed Graph commit request"):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert store.commits == []
    assert authority.released == authority.acquired


@pytest.mark.asyncio
async def test_exact_agent_request_with_missing_fields_fails_before_authority(
    agent: Agent[str], authority: MemoryAuthority
) -> None:
    request = cast(AgentStart[str], object.__new__(AgentStart))

    with pytest.raises(AgentContractError, match="malformed request"):
        await agent.run(request)
    assert authority.acquired == []


@pytest.mark.asyncio
async def test_forged_frozen_answer_is_readmitted_before_authority(
    agent: Agent[str], authority: MemoryAuthority
) -> None:
    question = GraphInterruptView((), "ask", GraphInterruptId("interrupt"), b"question")
    answer = AgentAnswer(question, Graph.values(value="answer"))
    request = AgentResume("run", (answer,))
    object.__setattr__(answer, "values", _make_single_graph_value("value", "answer", config_at(1)))
    with pytest.raises(AgentContractError):
        await agent.run(request)
    assert authority.acquired == []


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, "absent", False, object()])
async def test_load_does_not_guess_absence_from_an_untyped_response(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    monkeypatch: pytest.MonkeyPatch,
    response: object,
) -> None:
    async def malformed(
        _authority: ExecutionAuthority,
        /,
        *,
        children: tuple[ScopeRunCoordinate, ...] = (),
        expected_latest: AgentLatestHead | None = None,
    ) -> GraphCheckpoint[str] | NeverCreated:
        assert children == ()
        assert expected_latest is None
        return cast(GraphCheckpoint[str] | NeverCreated, response)

    monkeypatch.setattr(store, "load", malformed)
    with pytest.raises(PersistenceContractError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert store.commits == []
    assert authority.released == authority.acquired


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [PersistenceUnavailableError("offline"), PersistenceTombstoneError("deleted"), AuthorityLostError("lost")]
)
async def test_load_failures_are_not_new_runs(
    agent: Agent[str], store: SnapshotPersistence[str], authority: MemoryAuthority, error: Exception
) -> None:
    async def fail(_authority: ExecutionAuthority, _children: tuple[ScopeRunCoordinate, ...]) -> NeverCreated:
        raise error

    store.on_load = fail
    with pytest.raises(type(error)) as caught:
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert caught.value is error
    assert store.commits == []
    assert authority.released == authority.acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["foreign", "malformed", "untyped"])
async def test_acquired_capability_is_readmitted_and_released_if_typed(
    agent: Agent[str],
    store: SnapshotPersistence[str],
    authority: MemoryAuthority,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    acquire = authority.acquire

    async def broken(key: AgentRunKey, /) -> ExecutionAuthority:
        if variant == "untyped":
            return cast(ExecutionAuthority, object())
        grant = await acquire(replace(key, run_id=GraphRunId("foreign")) if variant == "foreign" else key)
        if variant == "malformed":
            object.__setattr__(grant, "credential", b"")
        return grant

    monkeypatch.setattr(authority, "acquire", broken)
    with pytest.raises(PersistenceContractError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert store.loads == []
    assert store.commits == []
    assert authority.current == {}
    assert authority.released == authority.acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["foreign-run", "checkpoint-subclass", "definition", "codec"])
async def test_recovery_identity_and_format_fail_before_nodes_and_writes(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str], variant: str
) -> None:
    await agent.run(AgentStart("run", Graph.values(value="input")))
    writes = len(store.commits)
    original = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    changed = original
    expected: type[Exception] = (
        PersistenceContractError if variant in ("foreign-run", "checkpoint-subclass") else Graph.SnapshotMismatchError
    )
    if variant == "foreign-run":
        changed = deepcopy(original)
        object.__setattr__(changed.root_state, "run_id", GraphRunId("foreign"))
    elif variant == "checkpoint-subclass":
        changed = UnsupportedCheckpoint(
            original.root_state, original.child_runs, original.graph_inputs, original.publications
        )
    elif variant == "definition":

        def assemble(_config: Config | None) -> Graph[str]:
            return linear_graph(calls, version=2)

        agent = replace(agent, assemble=assemble)
    else:
        agent = replace(agent, codec=replace(agent.codec, version=2))

    async def loaded(_authority: ExecutionAuthority, _children: tuple[ScopeRunCoordinate, ...]) -> GraphCheckpoint[str]:
        return changed

    store.on_load = loaded
    with pytest.raises(expected):
        await agent.run(AgentResume[str]("run"))
    assert calls == ["first", "second"]
    assert len(store.commits) == writes


@pytest.mark.asyncio
async def test_valid_checkpoint_for_another_run_is_rejected_after_readmission(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    await agent.run(AgentStart("foreign", Graph.values(value="input")))
    foreign = (await store.view(AgentRunKey("agent", GraphRunId("foreign")))).checkpoint("foreign")
    writes = len(store.commits)

    async def loaded(
        _authority: ExecutionAuthority,
        _children: tuple[ScopeRunCoordinate, ...],
    ) -> GraphCheckpoint[str]:
        return foreign

    store.on_load = loaded
    with pytest.raises(Graph.SnapshotMismatchError, match="different Agent run"):
        await agent.run(AgentResume[str]("run"))
    assert len(store.commits) == writes
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_assembly_cannot_return_an_alternative_runner(agent: Agent[str], store: SnapshotPersistence[str]) -> None:
    def assemble(_config: Config | None) -> Graph[str]:
        return cast(Graph[str], object())

    agent = replace(agent, assemble=assemble)
    with pytest.raises(AgentContractError, match="Graph facade"):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert store.commits == []


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["applied-subclass", "unknown-type", "reconcile-type"])
async def test_commit_outcomes_are_a_closed_typed_contract(
    agent: Agent[str], store: SnapshotPersistence[str], variant: str
) -> None:
    async def malformed(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str]:
        if variant == "applied-subclass":
            return UnsupportedApplied(request)
        if variant == "reconcile-type":
            return CommitUnknown()
        return cast(CommitOutcome[str], "applied")

    async def bad_reconcile(
        _authority: ExecutionAuthority, _request: GraphPersistenceCommit[str]
    ) -> CommitOutcome[str]:
        return cast(CommitOutcome[str], False)

    store.on_commit, store.on_reconcile = malformed, bad_reconcile
    with pytest.raises(PersistenceContractError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert len(store.commits) == 1
    assert len(store.reconciles) == int(variant == "reconcile-type")
