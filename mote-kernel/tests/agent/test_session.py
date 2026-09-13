import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from tests.agent.config_fixtures import ConfigCatalog
from tests.agent.persistence_fixtures import SnapshotPersistence
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence
from tests.execution.test_persistence_config import config_at, config_graph

from mote_kernel.agent import (
    Agent,
    AgentCompleted,
    AgentConfig,
    AgentContractError,
    AgentFailed,
    AgentInterrupted,
    AgentResume,
    AgentStart,
)
from mote_kernel.config import Config, ConfigContractError
from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValidationError, SnapshotMismatchError
from mote_kernel.execution.graph.values import _make_single_graph_value
from mote_kernel.execution.persistence import DurableGraphCommit, GraphPersistenceCommit, GraphRecovery
from mote_kernel.execution.run_context import ScopedStateBinding
from mote_kernel.persistence import AgentRunKey, CommitUnknown, ExecutionAuthority, PersistenceContractError
from mote_kernel.session import (
    AgentSession,
    AgentSessionActivation,
    AgentSessionCarrier,
    AgentSessionCodec,
    AgentSessionCodecCarrier,
    AgentSessionContractError,
    EncodedAgentSession,
    admit_session_carrier,
    admit_session_codec_carrier,
)
from mote_kernel.state.graph_state import GraphConfigCursor, GraphDefinitionId, GraphDefinitionVersion, GraphRunId


def _session_codec() -> AgentSessionCodec[str, str]:
    def encode(hook_state: str, context: str) -> bytes:
        return json.dumps((hook_state, context), separators=(",", ":")).encode()

    def decode(payload: bytes) -> tuple[str, str]:
        value: object = json.loads(payload)
        if type(value) is not list:
            raise ValueError("invalid session")
        values = cast(list[object], value)
        if len(values) != 2 or any(type(item) is not str for item in values):
            raise ValueError("invalid session")
        first, second = values
        return cast(str, first), cast(str, second)

    return AgentSessionCodec("test.agent-session", 1, encode, decode)


@pytest.mark.asyncio
async def test_agent_session_is_written_with_each_graph_commit_and_returned(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    session = AgentSession("hook-1", "context-1")
    agent = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )

    result = await agent.run(AgentStart("run", Graph.values(value="input"), session))

    assert isinstance(result, AgentCompleted)
    assert result.session == session
    assert len(store.commits) > 1
    encoded = tuple(request.agent_session for _, request in store.commits)
    assert encoded[0] is not None and all(candidate == encoded[0] for candidate in encoded)
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session is not None
    assert _session_codec().decode(checkpoint.agent_session, None) == session


@pytest.mark.asyncio
async def test_agent_session_is_recovered_from_the_same_graph_checkpoint(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    codec = _session_codec()
    session = AgentSession("hook-1", "context-1")
    agent = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )
    await agent.run(AgentStart("run", Graph.values(value="input"), session))

    recovered = await agent.run(AgentResume("run"))

    assert isinstance(recovered, AgentCompleted)
    assert recovered.session == session


@pytest.mark.asyncio
async def test_runtime_can_reuse_the_confirmed_session_for_a_later_run(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    codec = _session_codec()
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )
    session = AgentSession("hook-1", "context-1")

    first = await configured.run(AgentStart("run-1", Graph.values(value="input"), session))
    assert isinstance(first, AgentCompleted)
    assert first.session == session

    second = await configured.run(AgentStart("run-2", Graph.values(value="input"), first.session))

    assert isinstance(second, AgentCompleted)
    assert second.session == session
    first_receipts = [request for _, request in store.commits if request.candidate_state.run_id == GraphRunId("run-1")]
    second_receipts = [request for _, request in store.commits if request.candidate_state.run_id == GraphRunId("run-2")]
    assert first_receipts and second_receipts
    assert all(request.agent_session == first_receipts[0].agent_session for request in first_receipts)
    assert all(request.agent_session == first_receipts[0].agent_session for request in second_receipts)


@pytest.mark.asyncio
async def test_session_persists_only_the_config_cursor_and_resolves_capabilities_on_resume(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    config = config_at(1, definition="session-config")
    catalog = ConfigCatalog((config.snapshot,))
    codec = _session_codec()
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        config=AgentConfig(catalog, catalog),
        session_codec=codec,
    )
    session = AgentSession("hook-1", "context-1", config)

    result = await configured.run(AgentStart("run", Graph.values(value="input"), session))

    assert isinstance(result, AgentCompleted)
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session is not None
    assert checkpoint.agent_session.config_cursor == config.config_cursor
    assert configured.config is not None
    assert all(request.agent_session is not None for _, request in store.commits)
    for _, request in store.commits:
        assert request.agent_session is not None
        assert request.agent_session.config_cursor == config.config_cursor

    resumed = await configured.run(AgentResume("run"))

    assert isinstance(resumed, AgentCompleted)
    assert resumed.session is not None and resumed.session.config is not None
    assert resumed.session.config.config_cursor == config.config_cursor
    assert catalog.loads == [config.snapshot.key]


def test_agent_session_admission_rejects_forged_or_invalid_config() -> None:
    with pytest.raises(AgentSessionContractError, match="config"):
        AgentSession("hook", "context", cast(Config, object()))

    forged = cast(AgentSession[str, str], object.__new__(AgentSession))
    with pytest.raises(AgentSessionContractError, match="malformed"):
        AgentSession[str, str].admit(forged)
    with pytest.raises(AgentSessionContractError, match="exact"):
        AgentSession[str, str].admit(cast(AgentSession[str, str], object()))


def test_agent_rejects_a_session_envelope_without_its_resolved_config() -> None:
    cursor = config_at(1, definition="missing-session-config").config_cursor
    encoded = EncodedAgentSession("test.agent-session", 1, b"payload", cursor)
    resolve_session_config = Agent._session_config  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ConfigContractError, match="exact Config snapshot"):
        resolve_session_config(encoded, None, ())


def test_session_carriers_and_activation_require_the_exact_concrete_owner() -> None:
    with pytest.raises(AgentSessionContractError, match="exact AgentSession"):
        AgentSessionCarrier().admitted()
    with pytest.raises(AgentSessionContractError, match="exact AgentSessionCodec"):
        AgentSessionCodecCarrier().encode_session(cast(AgentSessionCarrier, object()))
    with pytest.raises(AgentSessionContractError, match="exact AgentSession"):
        admit_session_carrier(cast(AgentSessionCarrier, object()))
    with pytest.raises(AgentSessionContractError, match="exact AgentSessionCodec"):
        admit_session_codec_carrier(cast(AgentSessionCodecCarrier, object()))

    session = AgentSession("hook", "context")
    with pytest.raises(AgentSessionContractError, match="value is required"):
        AgentSessionActivation(None, session)
    with pytest.raises(AgentSessionContractError, match="exact AgentSession"):
        AgentSessionActivation("value", cast(AgentSessionCarrier, object()))
    forged = cast(AgentSession[str, str], object.__new__(AgentSession))
    with pytest.raises(AgentSessionContractError, match="malformed"):
        AgentSessionActivation("value", forged)


def _session_encoder(hook_state: str, context: str) -> bytes:
    return f"{hook_state}:{context}".encode()


def _session_decoder(payload: bytes) -> tuple[str, str]:
    hook_state, context = payload.decode().split(":")
    return hook_state, context


def _session_hook(values: Graph.Values[str]) -> str:
    session = values.session
    if session is None:
        return "none"
    typed = cast(AgentSession[str, str], session)
    return typed.hook_state


def _successor_graph(calls: list[str], *, successor_session: AgentSession[str, str] | None = None) -> Graph[str]:
    graph = Graph[str]("session-successor")

    async def first(values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append(f"first:{_session_hook(values)}")
        successor = successor_session if successor_session is not None else AgentSession("hook-2", "context-2")
        return Graph.success(Graph.values(value=f"{values['value']}-first"), session=successor)

    async def second(values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append(f"second:{_session_hook(values)}")
        return Graph.success(Graph.values(value=f"{values['value']}-second"))

    graph.add_node("first", first, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_node("second", second, inputs={"value": graph.node_output("first", "value")}, outputs={"value": str})
    graph.add_edge(Graph.START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", Graph.END)
    graph.set_outputs({"value": graph.output_ref("second", "value")})
    return graph


def _nested_terminal_session_graph(
    calls: list[str],
    *,
    child_terminal: str,
    child_ready: "asyncio.Event | None" = None,
) -> Graph[str]:
    """Build a child that advances Session before its terminal boundary."""

    child = Graph[str](f"session-child-{child_terminal}")

    async def first(values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append(f"first:{_session_hook(values)}")
        return Graph.success(
            Graph.values(value=values["value"]),
            session=AgentSession("hook-2", "context-2"),
        )

    async def terminal(values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append(f"terminal:{_session_hook(values)}")
        if child_ready is not None:
            child_ready.set()
        if child_terminal == "failure":
            return Graph.failure("child-failed")
        if child_terminal == "abort":
            return Graph.interrupt(b"child-question")
        return Graph.success(Graph.values())

    child.add_node("first", first, inputs={"value": child.graph_input("value", str)}, outputs={"value": str})
    child.add_node("terminal", terminal, inputs={"value": child.node_output("first", "value")}, outputs={})
    child.add_edge(Graph.START, "first")
    child.add_edge("first", "terminal")
    child.add_edge("terminal", Graph.END)
    if child_terminal == "abort":
        child.set_resume_codec("empty", 1, lambda _values: b"", lambda _payload: Graph.values())
    child.set_outputs({})

    parent = Graph[str](f"session-parent-{child_terminal}")
    parent.add_node("child", child, inputs={"value": parent.graph_input("value", str)})
    if child_terminal == "abort":
        assert child_ready is not None

        async def fail_parent(_values: Graph.Values[str]) -> Graph.Outcome[str]:
            await child_ready.wait()
            return Graph.failure("parent-failed")

        parent.add_node("fail", fail_parent, inputs={}, outputs={})
    parent.set_outputs({})
    return parent


def parallel_child_successor_graph(
    calls: list[str], child_confirmed: "asyncio.Event", *, sibling_session: AgentSession[str, str] | None = None
) -> Graph[str]:
    child = Graph[str]("parallel-session-child")

    async def update(values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append(f"child:{_session_hook(values)}")
        return Graph.success(
            Graph.values(),
            session=AgentSession("hook-2", "context-2"),
        )

    child.add_node("update", update, inputs={}, outputs={})
    child.add_edge(Graph.START, "update")
    child.add_edge("update", Graph.END)
    child.set_outputs({})

    parent = Graph[str]("parallel-session-parent")
    parent.add_node("child", child, inputs={})

    async def sibling(values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append(f"parent:{_session_hook(values)}")
        await child_confirmed.wait()
        return Graph.success(Graph.values(), session=sibling_session)

    parent.add_node("sibling", sibling, inputs={}, outputs={})
    parent.add_edge(Graph.START, "child")
    parent.add_edge(Graph.START, "sibling")
    parent.add_edge("child", Graph.END)
    parent.add_edge("sibling", Graph.END)
    parent.set_outputs({})
    return parent


def ordered_parallel_successor_graph(
    calls: list[str], order: tuple[str, str], first_ready: "asyncio.Event"
) -> Graph[str]:
    successors = {
        "b": AgentSession("hook-b", "context-b"),
        "c": AgentSession("hook-c", "context-c"),
    }

    def child(label: str) -> Graph[str]:
        graph = Graph[str](f"ordered-session-child-{label}")

        async def update(values: Graph.Values[str]) -> Graph.Outcome[str]:
            if label != order[0]:
                await first_ready.wait()
            calls.append(f"{label}:{_session_hook(values)}")
            if label == order[0]:
                first_ready.set()
            return Graph.success(Graph.values(), session=successors[label])

        graph.add_node("update", update, inputs={}, outputs={})
        graph.add_edge(Graph.START, "update")
        graph.add_edge("update", Graph.END)
        graph.set_outputs({})
        return graph

    parent = Graph[str]("ordered-session-parent")
    parent.add_node("b", child("b"), inputs={})
    parent.add_node("c", child("c"), inputs={})
    parent.add_edge(Graph.START, "b")
    parent.add_edge(Graph.START, "c")
    parent.add_edge("b", Graph.END)
    parent.add_edge("c", Graph.END)
    parent.set_outputs({})
    return parent


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True])
async def test_parallel_child_successor_cannot_be_overwritten_by_parent_inherited_session(
    agent: Agent[str, str, str], store: SnapshotPersistence[str], calls: list[str], unknown: bool
) -> None:
    codec = _session_codec()
    initial = AgentSession("hook-1", "context-1")
    successor = AgentSession("hook-2", "context-2")
    child_confirmed = asyncio.Event()
    configured = Agent(
        agent.agent_id,
        lambda _config: parallel_child_successor_graph(calls, child_confirmed),
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )
    encoded_successor = codec.encode(successor)

    async def observe_commit(
        authority: ExecutionAuthority, request: GraphPersistenceCommit[str]
    ) -> CommitUnknown | None:
        if request.agent_session == encoded_successor:
            child_confirmed.set()
            if unknown:
                await store.apply(authority, request)
                return CommitUnknown()
        return None

    store.on_commit = observe_commit

    result = await configured.run(AgentStart("run", Graph.values(), initial))

    assert isinstance(result, AgentCompleted)
    assert result.session == successor
    assert sorted(calls) == ["child:hook-1", "parent:hook-1"]
    commits = tuple(request for _authority, request in store.commits)
    successor_index = next(index for index, request in enumerate(commits) if request.agent_session == encoded_successor)
    assert all(request.agent_session == encoded_successor for request in commits[successor_index:])
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session == encoded_successor
    if unknown:
        assert store.reconciles

    resumed = await configured.run(AgentResume("run"))
    assert isinstance(resumed, AgentCompleted)
    assert resumed.session == successor


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("b", "c"), ("c", "b")])
async def test_parallel_explicit_successors_follow_durable_confirmation_order(
    agent: Agent[str, str, str], store: SnapshotPersistence[str], calls: list[str], order: tuple[str, str]
) -> None:
    codec = _session_codec()
    initial = AgentSession("hook-a", "context-a")
    successors = {
        "b": AgentSession("hook-b", "context-b"),
        "c": AgentSession("hook-c", "context-c"),
    }
    first_ready = asyncio.Event()
    configured = Agent(
        agent.agent_id,
        lambda _config: ordered_parallel_successor_graph(calls, order, first_ready),
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )

    result = await configured.run(AgentStart("run", Graph.values(), initial))

    assert isinstance(result, AgentCompleted)
    assert result.session == successors[order[1]]
    assert calls == [f"{order[0]}:hook-a", f"{order[1]}:hook-a"]
    encoded_first = codec.encode(successors[order[0]])
    encoded_last = codec.encode(successors[order[1]])
    successor_commits = tuple(
        request for _authority, request in store.commits if request.agent_session in (encoded_first, encoded_last)
    )
    assert tuple(request.agent_session for request in successor_commits[:2]) == (encoded_first, encoded_last)
    last_index = next(
        index for index, (_authority, request) in enumerate(store.commits) if request.agent_session == encoded_last
    )
    assert all(request.agent_session == encoded_last for _authority, request in store.commits[last_index:])
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session == encoded_last


@pytest.mark.asyncio
async def test_explicit_equal_successor_is_not_treated_as_inherited_session(
    agent: Agent[str, str, str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    codec = _session_codec()
    initial = AgentSession("hook-1", "context-1")
    child_confirmed = asyncio.Event()
    configured = Agent(
        agent.agent_id,
        lambda _config: parallel_child_successor_graph(calls, child_confirmed, sibling_session=initial),
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )
    encoded_initial = codec.encode(initial)

    async def observe_commit(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> None:
        if request.agent_session == codec.encode(AgentSession("hook-2", "context-2")):
            child_confirmed.set()

    store.on_commit = observe_commit

    result = await configured.run(AgentStart("run", Graph.values(), initial))

    assert isinstance(result, AgentCompleted)
    assert result.session == initial
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session == encoded_initial


@pytest.mark.asyncio
async def test_nested_child_completion_preserves_its_last_session_successor(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    configured = Agent(
        agent.agent_id,
        lambda _config: _nested_terminal_session_graph(calls, child_terminal="success"),
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )

    result = await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook-1", "context-1")))

    assert isinstance(result, AgentCompleted)
    assert result.session == AgentSession("hook-2", "context-2")
    assert calls == ["first:hook-1", "terminal:hook-2"]
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session is not None
    assert _session_codec().decode(checkpoint.agent_session, None) == AgentSession("hook-2", "context-2")


@pytest.mark.asyncio
async def test_nested_child_session_config_successor_ignores_historical_output_configs(
    agent: Agent[str], store: SnapshotPersistence[str]
) -> None:
    initial = config_at(1, definition="nested-session-config")
    successor = config_at(2, definition="nested-session-config")
    child = Graph[str]("nested-session-config-child")

    async def update(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(
            Graph.values(),
            session=AgentSession("hook-2", "context-2", successor),
        )

    child.add_node("update", update, inputs={}, outputs={})
    child.add_edge(Graph.START, "update")
    child.add_edge("update", Graph.END)
    child.set_outputs({})

    parent = Graph[str]("nested-session-config-parent")
    parent.add_node("child", child, inputs={})
    parent.add_edge(Graph.START, "child")
    parent.add_edge("child", Graph.END)
    parent.set_outputs({})

    catalog = ConfigCatalog((initial.snapshot, successor.snapshot))
    configured = Agent(
        agent.agent_id,
        lambda _config: parent,
        STRING_CODEC,
        store,
        agent.authority,
        config=AgentConfig(catalog, catalog),
        session_codec=_session_codec(),
    )
    result = await configured.run(AgentStart("run", Graph.values(), AgentSession("hook-1", "context-1", initial)))

    assert isinstance(result, AgentCompleted)
    assert result.session == AgentSession("hook-2", "context-2", successor)
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.root_state.config_cursor == successor.config_cursor
    assert checkpoint.agent_session is not None
    assert checkpoint.agent_session.config_cursor == successor.config_cursor

    resumed = await configured.run(AgentResume("run"))
    assert isinstance(resumed, AgentCompleted)
    assert resumed.session is not None and resumed.session.config is not None
    assert resumed.session.config.config_cursor == successor.config_cursor


@pytest.mark.asyncio
async def test_checkpoint_rejects_a_graph_state_with_an_older_session_commit(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    codec = _session_codec()
    configured = Agent(
        agent.agent_id,
        lambda _config: _successor_graph([]),
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )
    initial = AgentSession("hook-1", "context-1")
    await configured.run(AgentStart("run", Graph.values(value="input"), initial))
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    old_receipt = next(request for _, request in store.commits if request.candidate_state.revision == 0)
    assert old_receipt.agent_session is not None and old_receipt.session_receipt is not None

    with pytest.raises(SnapshotMismatchError, match="included family commit"):
        replace(
            checkpoint,
            agent_session=old_receipt.agent_session,
            session_receipt=old_receipt.session_receipt,
        )


@pytest.mark.asyncio
async def test_config_transition_requires_a_session_successor_when_a_session_is_active(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    initial, successor = config_at(1), config_at(2)
    catalog = ConfigCatalog((initial.snapshot, successor.snapshot))
    configured = Agent(
        agent.agent_id,
        lambda _config: config_graph(successor, []),
        STRING_CODEC,
        store,
        agent.authority,
        config=AgentConfig(catalog, catalog),
        session_codec=_session_codec(),
    )

    with pytest.raises(SnapshotMismatchError, match="explicit AgentSession successor"):
        await configured.run(
            AgentStart("run", Graph.values(value="input"), AgentSession("hook-1", "context-1", initial))
        )


@pytest.mark.asyncio
async def test_config_transition_with_a_session_successor_commits_atomically(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    initial, successor = config_at(1), config_at(2)
    child = Graph[str]("explicit-config-successor")

    async def update(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(
            _make_single_graph_value("value", values["value"], successor),
            session=AgentSession("hook-2", "context-2", successor),
        )

    child.add_node("update", update, inputs={"value": child.graph_input("value", str)}, outputs={"value": str})
    child.add_edge(Graph.START, "update")
    child.add_edge("update", Graph.END)
    child.set_outputs({"value": child.output_ref("update", "value")})
    catalog = ConfigCatalog((initial.snapshot, successor.snapshot))
    configured = Agent(
        agent.agent_id,
        lambda _config: child,
        STRING_CODEC,
        store,
        agent.authority,
        config=AgentConfig(catalog, catalog),
        session_codec=_session_codec(),
    )

    result = await configured.run(
        AgentStart("run", Graph.values(value="input"), AgentSession("hook-1", "context-1", initial))
    )

    assert isinstance(result, AgentCompleted)
    assert result.session == AgentSession("hook-2", "context-2", successor)
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.root_state.config_cursor == successor.config_cursor
    assert checkpoint.agent_session is not None
    assert checkpoint.agent_session.config_cursor == successor.config_cursor


@pytest.mark.asyncio
async def test_nested_child_failure_preserves_its_last_session_successor(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    configured = Agent(
        agent.agent_id,
        lambda _config: _nested_terminal_session_graph(calls, child_terminal="failure"),
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )

    result = await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook-1", "context-1")))

    assert isinstance(result, AgentFailed)
    assert result.session == AgentSession("hook-2", "context-2")
    assert calls == ["first:hook-1", "terminal:hook-2"]
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session is not None
    assert _session_codec().decode(checkpoint.agent_session, None) == AgentSession("hook-2", "context-2")


@pytest.mark.asyncio
async def test_nested_child_interrupt_returns_its_last_session_successor(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    initial = config_at(1, definition="nested-interrupt-session-config")
    successor = config_at(2, definition="nested-interrupt-session-config")
    child = Graph[str]("session-child-interrupt")

    async def update(values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append(f"update:{_session_hook(values)}")
        return Graph.success(
            Graph.values(value="updated"),
            session=AgentSession("hook-2", "context-2", successor),
        )

    async def interrupt(values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append(f"interrupt:{_session_hook(values)}")
        return Graph.interrupt(b"question")

    child.add_node("update", update, inputs={}, outputs={"value": str})
    child.add_node("interrupt", interrupt, inputs={}, outputs={})
    child.add_edge(Graph.START, "update")
    child.add_edge("update", "interrupt")
    child.add_edge("interrupt", Graph.END)
    child.set_resume_codec("empty", 1, lambda _values: b"", lambda _payload: Graph.values())
    child.set_outputs({})

    parent = Graph[str]("session-parent-interrupt")
    parent.add_node("child", child, inputs={})
    parent.add_edge(Graph.START, "child")
    parent.add_edge("child", Graph.END)
    parent.set_outputs({})

    catalog = ConfigCatalog((initial.snapshot, successor.snapshot))
    configured = Agent(
        agent.agent_id,
        lambda _config: parent,
        STRING_CODEC,
        store,
        agent.authority,
        config=AgentConfig(catalog, catalog),
        session_codec=_session_codec(),
    )
    result = await configured.run(AgentStart("run", Graph.values(), AgentSession("hook-1", "context-1", initial)))

    assert isinstance(result, AgentInterrupted)
    assert result.session == AgentSession("hook-2", "context-2", successor)
    assert calls == ["update:hook-1", "interrupt:hook-2"]
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    child_state = checkpoint.child_runs[0]
    assert isinstance(child_state, ScopedStateBinding)
    assert checkpoint.root_state.config_cursor == initial.config_cursor
    assert child_state.state.config_cursor == successor.config_cursor
    assert checkpoint.agent_session is not None
    assert checkpoint.agent_session.config_cursor == successor.config_cursor

    recovered = await configured.run(AgentResume("run"))

    assert isinstance(recovered, AgentInterrupted)
    assert recovered.session is not None
    assert (recovered.session.hook_state, recovered.session.context) == ("hook-2", "context-2")
    assert recovered.session.config is not None
    assert recovered.session.config.config_cursor == successor.config_cursor
    assert calls == ["update:hook-1", "interrupt:hook-2"]


@pytest.mark.asyncio
async def test_nested_child_abort_preserves_its_last_session_successor(
    agent: Agent[str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    child_ready = asyncio.Event()
    configured = Agent(
        agent.agent_id,
        lambda _config: _nested_terminal_session_graph(calls, child_terminal="abort", child_ready=child_ready),
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )

    result = await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook-1", "context-1")))

    assert isinstance(result, AgentFailed)
    assert result.session == AgentSession("hook-2", "context-2")
    assert calls == ["first:hook-1", "terminal:hook-2"]
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session is not None
    assert _session_codec().decode(checkpoint.agent_session, None) == AgentSession("hook-2", "context-2")


@pytest.mark.asyncio
async def test_node_session_successor_is_committed_and_reused_after_recovery(
    agent: Agent[str, str, str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    configured = Agent(
        agent.agent_id,
        lambda _config: _successor_graph(calls),
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )

    async def fail(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> None:
        if request.candidate_state.revision == 3:
            raise OSError("injected persistence outage")

    store.on_commit = fail

    with pytest.raises(OSError):
        await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook-1", "context-1")))

    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session is not None
    assert _session_codec().decode(checkpoint.agent_session, None) == AgentSession("hook-2", "context-2")
    assert calls == ["first:hook-1"]

    store.on_commit = None
    result = await configured.run(AgentResume("run"))

    assert isinstance(result, AgentCompleted)
    assert result.session == AgentSession("hook-2", "context-2")
    assert calls == ["first:hook-1", "second:hook-2"]
    receipts = [request for _, request in store.commits]
    assert receipts[0].agent_session is not None
    assert receipts[0].agent_session != receipts[-1].agent_session
    final_session = receipts[-1].agent_session
    assert final_session is not None
    assert _session_codec().decode(final_session, None) == result.session


@pytest.mark.asyncio
async def test_session_successor_can_explicitly_clear_historical_config(
    agent: Agent[str, str, str], store: SnapshotPersistence[str], calls: list[str]
) -> None:
    initial = config_at(1, definition="session-clear-config")
    catalog = ConfigCatalog((initial.snapshot,))
    successor = AgentSession("hook-2", "context-2")
    configured = Agent(
        agent.agent_id,
        lambda _config: _successor_graph(calls, successor_session=successor),
        STRING_CODEC,
        store,
        agent.authority,
        config=AgentConfig(catalog, catalog),
        session_codec=_session_codec(),
    )

    result = await configured.run(
        AgentStart("run", Graph.values(value="input"), AgentSession("hook-1", "context-1", initial))
    )

    assert isinstance(result, AgentCompleted)
    assert result.session == successor
    assert result.outputs.activation_config is None
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.agent_session is not None and checkpoint.agent_session.config_cursor is None

    resumed = await configured.run(AgentResume("run"))

    assert isinstance(resumed, AgentCompleted)
    assert resumed.session == successor
    assert resumed.outputs.activation_config is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["a-none", "a-b", "none-b"])
async def test_recovery_rejects_a_checkpoint_and_commit_session_mismatch(
    agent: Agent[str, str, str], store: SnapshotPersistence[str], scenario: str
) -> None:
    codec = _session_codec()
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )
    session_a = AgentSession("hook-a", "context-a")
    session_b = AgentSession("hook-b", "context-b")
    await configured.run(AgentStart("run", Graph.values(value="input"), session_a))
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    encoded_b = codec.encode(session_b)
    if scenario == "none-b":
        checkpoint = replace(checkpoint, agent_session=None, session_receipt=None)
    commit_session = None if scenario == "a-none" else encoded_b

    async def writer(request: GraphPersistenceCommit[str]) -> GraphPersistenceCommit[str]:
        return request

    commit = DurableGraphCommit(
        STRING_CODEC,
        writer,
        agent_session=commit_session,
        session_codec=codec,
    )

    with pytest.raises(SnapshotMismatchError, match="same AgentSession"):
        GraphRecovery(checkpoint, commit, session=session_a)


@pytest.mark.asyncio
async def test_recovery_rejects_a_decoded_session_with_the_same_cursor_but_different_payload(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    codec = _session_codec()
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )
    session_a = AgentSession("hook-a", "context-a")
    session_b = AgentSession("hook-b", "context-b")
    await configured.run(AgentStart("run", Graph.values(value="input"), session_a))
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    encoded_b = codec.encode(session_b)
    with pytest.raises(SnapshotMismatchError, match="receipt"):
        replace(checkpoint, agent_session=encoded_b)

    async def writer(request: GraphPersistenceCommit[str]) -> GraphPersistenceCommit[str]:
        return request

    commit = DurableGraphCommit(
        STRING_CODEC,
        writer,
        agent_session=encoded_b,
        session_codec=codec,
    )

    with pytest.raises(SnapshotMismatchError, match="same AgentSession"):
        GraphRecovery(checkpoint, commit, session=session_a)


@pytest.mark.asyncio
async def test_recovery_rejects_a_decoded_session_with_the_same_cursor_and_bound_commit(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    codec = _session_codec()
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=codec,
    )
    session_a = AgentSession("hook-a", "context-a")
    session_b = AgentSession("hook-b", "context-b")
    await configured.run(AgentStart("run", Graph.values(value="input"), session_a))
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()

    async def writer(request: GraphPersistenceCommit[str]) -> GraphPersistenceCommit[str]:
        return request

    commit = DurableGraphCommit(
        STRING_CODEC,
        writer,
        agent_session=checkpoint.agent_session,
        session_codec=codec,
    )

    with pytest.raises(SnapshotMismatchError, match="durable envelope"):
        GraphRecovery(checkpoint, commit, session=session_b)


def test_agent_session_codec_constructor_rejects_invalid_capabilities() -> None:
    with pytest.raises(AgentSessionContractError, match="identity"):
        AgentSessionCodec("", 1, _session_encoder, _session_decoder)
    with pytest.raises(AgentSessionContractError, match="version"):
        AgentSessionCodec("test.session", 0, _session_encoder, _session_decoder)
    with pytest.raises(AgentSessionContractError, match="callable"):
        AgentSessionCodec("test.session", 1, cast(Callable[[str, str], bytes], None), _session_decoder)
    with pytest.raises(AgentSessionContractError, match="callable"):
        AgentSessionCodec("test.session", 1, _session_encoder, cast(Callable[[bytes], tuple[str, str]], None))


def test_agent_session_codec_rejects_bad_encoder_and_wrong_session() -> None:
    def raises_encoder(_hook_state: str, _context: str) -> bytes:
        raise ValueError("encode failure")

    def non_bytes_encoder(_hook_state: str, _context: str) -> bytes:
        return cast(bytes, bytearray(b"not-bytes"))

    codec = AgentSessionCodec("test.session", 1, raises_encoder, _session_decoder)
    with pytest.raises(AgentSessionContractError, match="exact"):
        codec.encode(cast(AgentSession[str, str], object()))
    with pytest.raises(AgentSessionContractError, match="encoder rejected"):
        codec.encode(AgentSession("hook", "context"))

    invalid = AgentSessionCodec("test.session", 1, non_bytes_encoder, _session_decoder)
    with pytest.raises(AgentSessionContractError, match="bytes"):
        invalid.encode(AgentSession("hook", "context"))

    with pytest.raises(AgentSessionContractError, match="exact"):
        invalid.encode_session(cast(AgentSessionCarrier, object()))


def test_agent_session_codec_rejects_nonreversible_and_uncomparable_values() -> None:
    class Uncomparable:
        def __eq__(self, _other: object) -> bool:
            raise RuntimeError("comparison failure")

    compare_codec = AgentSessionCodec(
        "test.session",
        1,
        lambda _hook, _context: b"payload",
        lambda _payload: (Uncomparable(), "context"),
    )
    with pytest.raises(AgentSessionContractError, match="compare"):
        compare_codec.encode(AgentSession(Uncomparable(), "context"))

    nonreversible = AgentSessionCodec(
        "test.session",
        1,
        lambda _hook, _context: b"payload",
        lambda _payload: ("different", "context"),
    )
    with pytest.raises(AgentSessionContractError, match="not reversible"):
        nonreversible.encode(AgentSession("hook", "context"))

    noncanonical = AgentSessionCodec(
        "test.session",
        1,
        lambda _hook, _context: b"canonical",
        lambda _payload: ("hook", "context"),
    )
    with pytest.raises(AgentSessionContractError, match="deterministic canonical"):
        noncanonical.decode(EncodedAgentSession("test.session", 1, b"noncanonical"), None)


def test_agent_session_codec_decode_admits_only_matching_durable_values() -> None:
    codec = AgentSessionCodec("test.session", 1, _session_encoder, _session_decoder)
    encoded = codec.encode(AgentSession("hook", "context"))
    with pytest.raises(AgentSessionContractError, match="identity/version"):
        codec.decode(EncodedAgentSession("other.session", 1, encoded.payload), None)

    config = config_at(1, definition="codec-config")
    with pytest.raises(AgentSessionContractError, match="malformed"):
        codec.decode(encoded, cast(Config, object()))
    with pytest.raises(AgentSessionContractError, match="cursor"):
        codec.decode(encoded, config)

    def raises_decoder(_payload: bytes) -> tuple[str, str]:
        raise ValueError("decode failure")

    with pytest.raises(AgentSessionContractError, match="decoder rejected"):
        AgentSessionCodec("test.session", 1, _session_encoder, raises_decoder).decode(encoded, None)

    def wrong_shape(_payload: bytes) -> tuple[str, str]:
        return cast(tuple[str, str], ("only-one",))

    with pytest.raises(AgentSessionContractError, match="two-item"):
        AgentSessionCodec("test.session", 1, _session_encoder, wrong_shape).decode(encoded, None)


def test_agent_session_codec_requires_deterministic_round_trip() -> None:
    calls = 0

    def unstable_encoder(_hook_state: str, _context: str) -> bytes:
        nonlocal calls
        calls += 1
        return str(calls).encode()

    codec = AgentSessionCodec("test.session", 1, unstable_encoder, lambda _payload: ("hook", "context"))
    with pytest.raises(AgentSessionContractError, match="deterministic"):
        codec.encode(AgentSession("hook", "context"))


def test_encoded_agent_session_admission_rejects_malformed_envelopes() -> None:
    config_cursor = config_at(1, definition="encoded-config").config_cursor
    invalid_constructors: tuple[Callable[[], EncodedAgentSession], ...] = (
        lambda: EncodedAgentSession("", 1, b"payload"),
        lambda: EncodedAgentSession("test.session", 0, b"payload"),
        lambda: EncodedAgentSession("test.session", 1, cast(bytes, bytearray(b"payload"))),
        lambda: EncodedAgentSession("test.session", 1, b"payload", cast(GraphConfigCursor, object())),
        lambda: EncodedAgentSession(
            "test.session",
            1,
            b"payload",
            GraphConfigCursor(GraphDefinitionId("encoded"), GraphDefinitionVersion(1), 1),
        ),
    )
    for constructor in invalid_constructors:
        with pytest.raises(AgentSessionContractError):
            constructor()

    valid = EncodedAgentSession("test.session", 1, b"payload", config_cursor)

    class UnsupportedEncodedAgentSession(EncodedAgentSession):
        pass

    with pytest.raises(AgentSessionContractError, match="exact"):
        EncodedAgentSession.admit(UnsupportedEncodedAgentSession("test.session", 1, b"payload", config_cursor))
    forged = object.__new__(EncodedAgentSession)
    with pytest.raises(AgentSessionContractError, match="malformed"):
        EncodedAgentSession.admit(forged)
    assert EncodedAgentSession.admit(valid) == valid


def test_agent_rejects_a_non_exact_session_codec(agent: Agent[str], store: SnapshotPersistence[str]) -> None:
    with pytest.raises(AgentContractError, match="session codec"):
        replace(agent, session_codec=cast(AgentSessionCodec[str, str], object()))


@pytest.mark.asyncio
async def test_agent_rejects_a_forged_start_session_before_authority_work(
    agent: Agent[str, str, str],
) -> None:
    class ForgedAgentSession(AgentSession[str, str]):
        pass

    forged = object.__new__(ForgedAgentSession)
    with pytest.raises(AgentContractError, match="session is malformed"):
        await agent.run(AgentStart("run", Graph.values(value="input"), forged))


@pytest.mark.asyncio
async def test_agent_requires_a_session_codec_for_a_new_session(
    agent: Agent[str, str, str],
) -> None:
    with pytest.raises(AgentContractError, match="requires its session codec"):
        await agent.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook", "context")))


@pytest.mark.asyncio
async def test_agent_rejects_session_codec_encoding_failure(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    def raises_encoder(_hook_state: str, _context: str) -> bytes:
        raise ValueError("encode failure")

    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=AgentSessionCodec("test.session", 1, raises_encoder, _session_decoder),
    )
    with pytest.raises(AgentContractError, match="could not be encoded"):
        await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook", "context")))
    assert store.commits == []


@pytest.mark.asyncio
async def test_agent_rejects_a_non_decodable_session_before_the_first_durable_write(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    def raises_decoder(_payload: bytes) -> tuple[str, str]:
        raise ValueError("decode failure")

    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=AgentSessionCodec("test.session", 1, _session_encoder, raises_decoder),
    )

    with pytest.raises(AgentContractError, match="could not be encoded"):
        await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook", "context")))
    assert store.commits == []


@pytest.mark.asyncio
async def test_agent_does_not_combine_session_config_with_initial_config(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    config = config_at(1, definition="initial-session-conflict")
    catalog = ConfigCatalog((config.snapshot,))
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        config=AgentConfig(catalog, catalog, config.snapshot.key),
        session_codec=_session_codec(),
    )
    with pytest.raises(AgentContractError, match="both be supplied"):
        await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook", "context", config)))


@pytest.mark.asyncio
async def test_agent_requires_the_codec_to_recover_a_persisted_session(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )
    await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook", "context")))

    without_codec = Agent(agent.agent_id, agent.assemble, STRING_CODEC, store, agent.authority)
    with pytest.raises(AgentContractError, match="requires its session codec"):
        await without_codec.run(AgentResume("run"))


@pytest.mark.asyncio
async def test_agent_rejects_a_malformed_persisted_session(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )
    await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook", "context")))

    def raises_decoder(_payload: bytes) -> tuple[str, str]:
        raise ValueError("decode failure")

    malformed = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=AgentSessionCodec("test.agent-session", 1, _session_encoder, raises_decoder),
    )
    with pytest.raises(PersistenceContractError, match="persisted AgentSession"):
        await malformed.run(AgentResume("run"))


@pytest.mark.asyncio
async def test_graph_commit_and_checkpoint_admission_bind_the_session_to_state(
    agent: Agent[str, str, str], store: SnapshotPersistence[str]
) -> None:
    configured = Agent(
        agent.agent_id,
        agent.assemble,
        STRING_CODEC,
        store,
        agent.authority,
        session_codec=_session_codec(),
    )
    await configured.run(AgentStart("run", Graph.values(value="input"), AgentSession("hook", "context")))
    request = store.commits[0][1]
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    malformed = cast(EncodedAgentSession, object())

    with pytest.raises(SnapshotMismatchError, match="commit AgentSession"):
        replace(request, agent_session=malformed)
    with pytest.raises(SnapshotMismatchError, match="checkpoint AgentSession"):
        replace(checkpoint, agent_session=malformed)

    unrelated_cursor = config_at(1, definition="unrelated-session-config").config_cursor
    with pytest.raises(SnapshotMismatchError, match="candidate state"):
        replace(request, agent_session=EncodedAgentSession("test.agent-session", 1, b'["bad"]', unrelated_cursor))
    with pytest.raises(SnapshotMismatchError, match="graph family"):
        replace(
            checkpoint,
            agent_session=EncodedAgentSession("test.agent-session", 1, b'["bad"]', unrelated_cursor),
        )


def test_durable_graph_commit_rejects_a_malformed_session() -> None:
    writer = MemoryPersistence[str]()
    with pytest.raises(GraphValidationError, match="AgentSession"):
        DurableGraphCommit(STRING_CODEC, writer, cast(EncodedAgentSession, object()))


def test_durable_graph_commit_rejects_a_malformed_session_codec() -> None:
    with pytest.raises(GraphValidationError, match="session codec"):
        DurableGraphCommit(
            STRING_CODEC,
            MemoryPersistence[str](),
            session_codec=cast(AgentSessionCodec[str, str], object()),
        )


@pytest.mark.asyncio
async def test_durable_graph_commit_requires_a_codec_for_a_session_successor() -> None:
    graph = _successor_graph([])
    with pytest.raises(SnapshotMismatchError, match="requires its session codec"):
        await graph.run(
            Graph.values(value="input"),
            session=AgentSession("hook", "context"),
            commit=DurableGraphCommit(STRING_CODEC, MemoryPersistence[str]()),
        )


@pytest.mark.asyncio
async def test_durable_graph_commit_reports_a_successor_encoding_failure() -> None:
    def encode(hook_state: str, context: str) -> bytes:
        if hook_state == "hook-2":
            raise ValueError("successor cannot be encoded")
        return _session_encoder(hook_state, context)

    graph = _successor_graph([])
    with pytest.raises(SnapshotMismatchError, match="could not be encoded"):
        await graph.run(
            Graph.values(value="input"),
            session=AgentSession("hook-1", "context-1"),
            commit=DurableGraphCommit(
                STRING_CODEC,
                MemoryPersistence[str](),
                session_codec=AgentSessionCodec("test.session-successor", 1, encode, _session_decoder),
            ),
        )


@pytest.mark.asyncio
async def test_recovery_requires_a_decoded_session_envelope_and_codec() -> None:
    codec = _session_codec()
    store = MemoryPersistence[str]()
    graph = _successor_graph([])
    initial = AgentSession("hook-1", "context-1")
    await graph.run(
        Graph.values(value="input"),
        run_id="run",
        session=initial,
        commit=DurableGraphCommit(STRING_CODEC, store, session_codec=codec),
    )
    checkpoint = store.checkpoint()
    encoded = checkpoint.agent_session
    assert encoded is not None
    final = codec.decode(encoded, None)

    matching = DurableGraphCommit(STRING_CODEC, store, agent_session=encoded, session_codec=codec)
    with pytest.raises(SnapshotMismatchError, match="decoded Session"):
        GraphRecovery(checkpoint, matching, session=None)
    with pytest.raises(SnapshotMismatchError, match="requires its codec"):
        GraphRecovery(
            checkpoint,
            DurableGraphCommit(STRING_CODEC, store, agent_session=encoded),
            session=final,
        )

    malformed_codec = AgentSessionCodec(
        "test.session-recovery-failure",
        1,
        lambda _hook, _context: (_ for _ in ()).throw(ValueError("cannot encode")),
        _session_decoder,
    )
    with pytest.raises(SnapshotMismatchError, match="canonically encoded"):
        GraphRecovery(
            checkpoint,
            DurableGraphCommit(STRING_CODEC, store, agent_session=encoded, session_codec=malformed_codec),
            session=final,
        )
    with pytest.raises(SnapshotMismatchError, match="Session is malformed"):
        GraphRecovery(checkpoint, matching, session=cast(AgentSessionCarrier, object()))


@pytest.mark.asyncio
async def test_recovery_rejects_a_decoded_session_with_the_wrong_config_cursor() -> None:
    codec = _session_codec()
    config = config_at(1, definition="recovery-session-config")
    store = MemoryPersistence[str]()
    graph = Graph[str]("recovery-session-config-graph")

    async def identity(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    graph.add_node("identity", identity, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_edge(Graph.START, "identity")
    graph.add_edge("identity", Graph.END)
    graph.set_outputs({})
    session = AgentSession("hook", "context", config)
    commit = DurableGraphCommit(STRING_CODEC, store, session_codec=codec)
    await graph.run(Graph.values(value="input"), run_id="run", session=session, commit=commit)
    checkpoint = store.checkpoint()
    encoded = checkpoint.agent_session
    assert encoded is not None

    with pytest.raises(SnapshotMismatchError, match="Config cursor"):
        GraphRecovery(
            checkpoint,
            DurableGraphCommit(STRING_CODEC, store, agent_session=encoded, session_codec=codec),
            session=AgentSession("hook", "context"),
        )
