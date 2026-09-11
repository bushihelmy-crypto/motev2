from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from tests.agent.config_fixtures import ConfigCatalog
from tests.agent.persistence_fixtures import SnapshotPersistence
from tests.execution.test_persistence_config import config_at

from mote_kernel.agent import Agent, AgentCompleted, AgentConfig, AgentContractError, AgentResume, AgentStart
from mote_kernel.config import (
    Config,
    ConfigContractError,
    ConfigResolver,
    ConfigSnapshot,
    ConfigSnapshotKey,
    ConfigSnapshotStore,
    resolve_config,
    save_config_snapshot,
)
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.values import _make_single_graph_value
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import GraphCheckpoint, GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentRunKey,
    CommitOutcome,
    ExecutionAuthority,
    PersistenceContractError,
    PersistenceUnavailableError,
)
from mote_kernel.state.graph_state import GraphRunId


@pytest.mark.asyncio
@pytest.mark.parametrize("initially_configured", [False, True])
async def test_observe_is_the_only_config_writer_and_recovery_resolves_exact_history(
    agent: Agent[str], store: SnapshotPersistence[str], initially_configured: bool
) -> None:
    initial = config_at(1, definition="config-history")
    successor = config_at(2 if initially_configured else 1, definition="config-history")
    latest = config_at(3, definition="config-history")
    catalog = ConfigCatalog((initial.snapshot, latest.snapshot))
    seen: list[Config | None] = []
    assembled: list[Config | None] = []

    def assemble(config: Config | None) -> Graph[str]:
        assembled.append(config)
        graph = Graph[str]("config-history")

        async def observe(values: Graph.Values[str]) -> Graph.Values[str]:
            seen.append(values.activation_config)
            saved = await save_config_snapshot(catalog, successor.snapshot)
            changed = await resolve_config(catalog, saved)
            return _make_single_graph_value("value", values["value"], changed)

        async def consume(values: Graph.Values[str]) -> Graph.Values[str]:
            seen.append(values.activation_config)
            return values

        graph.add_node("observe", observe, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
        graph.add_node(
            "consume", consume, inputs={"value": graph.node_output("observe", "value")}, outputs={"value": str}
        )
        graph.add_edge("observe", "consume")
        graph.set_outputs({"value": graph.output_ref("consume", "value")})
        return graph

    async def stop(_authority: ExecutionAuthority, request: GraphPersistenceCommit[str]) -> CommitOutcome[str] | None:
        if request.candidate_state.superstep == 1:
            raise PersistenceUnavailableError("stop after Observe settlement")
        return None

    agent = replace(
        agent,
        assemble=assemble,
        config=AgentConfig(catalog, catalog, initial.snapshot.key if initially_configured else None),
    )
    store.on_commit = stop
    with pytest.raises(PersistenceUnavailableError):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.root_state.config_cursor == successor.config_cursor
    assert checkpoint.graph_inputs[0].frame.config_cursor == (initial.config_cursor if initially_configured else None)
    assert catalog.saves == [successor.snapshot]
    assert len(seen) == 1
    fresh_catalog = ConfigCatalog(tuple(deepcopy(tuple(catalog.snapshots.values()))))
    store.on_commit = None
    recovered = await replace(agent, config=AgentConfig(fresh_catalog, fresh_catalog, latest.snapshot.key)).run(
        AgentResume[str]("run")
    )
    assert isinstance(recovered, AgentCompleted)
    assert recovered.outputs["value"] == "input"
    assert recovered.outputs.activation_config is None
    assert len(seen) == 2
    assert (seen[0].snapshot if seen[0] is not None else None) == (initial.snapshot if initially_configured else None)
    assert seen[1] is not None and seen[1].snapshot == successor.snapshot
    assert assembled[-1] is seen[1]
    assert set(fresh_catalog.loads) == {initial.snapshot.key, successor.snapshot.key}
    assert len(fresh_catalog.loads) == (2 if initially_configured else 1)
    assert latest.snapshot.key not in fresh_catalog.loads
    assert fresh_catalog.saves == []
    assert catalog.saves == [successor.snapshot]


@pytest.mark.asyncio
async def test_config_free_history_does_not_adopt_an_initial_config_during_recovery(
    agent: Agent[str], store: SnapshotPersistence[str]
) -> None:
    await agent.run(AgentStart("run", Graph.values(value="input")))
    config = config_at(9)
    catalog = ConfigCatalog((config.snapshot,))
    result = await replace(agent, config=AgentConfig(catalog, catalog, config.snapshot.key)).run(
        AgentResume[str]("run")
    )
    assert isinstance(result, AgentCompleted)
    assert catalog.loads == catalog.resolutions == catalog.saves == []
    assert len(store.loads) == 2


@pytest.mark.asyncio
async def test_referenced_config_requires_both_capabilities_and_all_exact_snapshots(
    agent: Agent[str], store: SnapshotPersistence[str]
) -> None:
    config = config_at(1)
    catalog = ConfigCatalog((config.snapshot,))
    configured = replace(agent, config=AgentConfig(catalog, catalog, config.snapshot.key))
    await configured.run(AgentStart("run", Graph.values(value="input")))
    writes = len(store.commits)
    with pytest.raises(AgentContractError, match="persisted Config"):
        await agent.run(AgentResume[str]("run"))
    catalog.snapshots.clear()
    with pytest.raises(ConfigContractError, match="unavailable"):
        await configured.run(AgentResume[str]("run"))
    assert len(store.commits) == writes


@pytest.mark.asyncio
async def test_missing_initial_config_does_not_create_state(agent: Agent[str], store: SnapshotPersistence[str]) -> None:
    catalog = ConfigCatalog()
    agent = replace(agent, config=AgentConfig(catalog, catalog, config_at(1).snapshot.key))
    with pytest.raises(ConfigContractError, match="unavailable"):
        await agent.run(AgentStart("run", Graph.values(value="input")))
    assert store.commits == []


@pytest.mark.parametrize("missing", ["store", "resolver", "initial"])
def test_config_capabilities_fail_as_a_group_at_assembly(missing: str) -> None:
    catalog = ConfigCatalog()
    with pytest.raises(ConfigContractError):
        AgentConfig(
            cast(ConfigSnapshotStore, None) if missing == "store" else catalog,
            cast(ConfigResolver, None) if missing == "resolver" else catalog,
            cast(ConfigSnapshotKey, object()) if missing == "initial" else None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["key", "digest", "payload", "resolver-key", "resolver-type"])
async def test_config_boundary_corruption_fails_before_resume_writes(
    agent: Agent[str], store: SnapshotPersistence[str], monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    config = config_at(1)
    catalog = ConfigCatalog((config.snapshot,))
    agent = replace(agent, config=AgentConfig(catalog, catalog, config.snapshot.key))
    await agent.run(AgentStart("run", Graph.values(value="input")))
    writes = len(store.commits)
    snapshot = deepcopy(config.snapshot)
    if variant == "key":
        snapshot = config_at(2).snapshot
    elif variant == "digest":
        snapshot = ConfigSnapshot.capture(snapshot.key, b"other bytes")
    elif variant == "payload":
        object.__setattr__(snapshot, "payload", b"corrupted")

    async def loaded(_key: ConfigSnapshotKey, /) -> ConfigSnapshot:
        return snapshot

    async def resolved(_snapshot: ConfigSnapshot, /) -> Config:
        return config_at(2) if variant == "resolver-key" else cast(Config, None)

    monkeypatch.setattr(catalog, "load", loaded)
    if variant.startswith("resolver"):
        monkeypatch.setattr(catalog, "resolve", resolved)
    with pytest.raises(ConfigContractError):
        await agent.run(AgentResume[str]("run"))
    assert len(store.commits) == writes


@pytest.mark.asyncio
async def test_state_validation_precedes_config_resolution(agent: Agent[str], store: SnapshotPersistence[str]) -> None:
    await agent.run(AgentStart("run", Graph.values(value="input")))
    checkpoint = (await store.view(AgentRunKey("agent", GraphRunId("run")))).checkpoint()
    malformed = deepcopy(checkpoint)
    object.__setattr__(malformed.root_state, "revision", -1)

    async def loaded(_authority: ExecutionAuthority, _children: tuple[ScopeRunCoordinate, ...]) -> GraphCheckpoint[str]:
        return malformed

    store.on_load = loaded
    writes = len(store.commits)
    with pytest.raises(PersistenceContractError, match="malformed checkpoint"):
        await agent.run(AgentResume[str]("run"))
    assert len(store.commits) == writes
