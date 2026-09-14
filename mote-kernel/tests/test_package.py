from typing import cast

import pytest
from tests.agent.persistence_fixtures import JournalPersistence, MemoryAuthority, SnapshotPersistence
from tests.execution.persistence_fixtures import STRING_CODEC, linear_graph

from mote_kernel import Agent, __version__
from mote_kernel.agent import AgentCompleted, AgentResume, AgentStart
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.persistence import AgentRunKey
from mote_kernel.state.graph_state import GraphRunId


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_agent_is_the_only_package_public_facade() -> None:
    import mote_kernel
    import mote_kernel.agent as agent_module

    assert mote_kernel.__all__ == ["Agent", "__version__"]
    assert agent_module.__all__ == ["Agent"]
    assert mote_kernel.Agent is agent_module.Agent
    assert not hasattr(mote_kernel, "AgentSession")
    assert not hasattr(mote_kernel, "AgentSessionCodec")


def test_star_imports_match_the_two_public_boundaries() -> None:
    import mote_kernel
    import mote_kernel.agent as agent_module

    package_namespace: dict[str, object] = {}
    exec("from mote_kernel import *", package_namespace)
    package_namespace.pop("__builtins__", None)
    assert package_namespace == {"Agent": mote_kernel.Agent, "__version__": mote_kernel.__version__}

    agent_namespace: dict[str, object] = {}
    exec("from mote_kernel.agent import *", agent_namespace)
    agent_namespace.pop("__builtins__", None)
    assert agent_namespace == {"Agent": agent_module.Agent}


def test_agent_records_stay_owned_by_the_agent_module() -> None:
    import mote_kernel
    import mote_kernel.agent as agent_module

    records = (
        "AgentAnswer",
        "AgentAborted",
        "AgentCompleted",
        "AgentConfig",
        "AgentContractError",
        "AgentFailed",
        "AgentInterrupted",
        "AgentRequest",
        "AgentResult",
        "AgentResume",
        "AgentRunNotFoundError",
        "AgentStart",
    )
    assert all(hasattr(agent_module, name) for name in records)
    assert all(not hasattr(mote_kernel, name) for name in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("journal", [False, True], ids=["snapshot", "journal"])
async def test_root_agent_facade_composes_with_graph_and_cold_resume(journal: bool) -> None:
    authority = MemoryAuthority()
    store: SnapshotPersistence[str] = (
        JournalPersistence[str](authority) if journal else SnapshotPersistence[str](authority)
    )
    calls: list[str] = []

    def assemble(_config: Config | None) -> Graph[str]:
        return linear_graph(calls)

    agent = Agent("public-agent", assemble, STRING_CODEC, store, authority)
    started = await agent.run(AgentStart("run", Graph.values(value="input")))

    assert isinstance(started, AgentCompleted)
    assert started.outputs["value"] == "input-first-second"
    assert calls == ["first", "second"]
    writes = len(store.commits)
    assert writes > 0

    resumed = await Agent("public-agent", assemble, STRING_CODEC, store, authority).run(AgentResume[str]("run"))

    assert isinstance(resumed, AgentCompleted)
    assert resumed.outputs == started.outputs
    assert calls == ["first", "second"]
    assert len(store.commits) == writes
    assert authority.current == {}
    assert authority.released == authority.acquired
    checkpoint = (await store.view(AgentRunKey("public-agent", GraphRunId("run")))).checkpoint()
    assert checkpoint.root_state.run_id == GraphRunId("run")


@pytest.mark.asyncio
async def test_root_agent_facade_rejects_an_untyped_graph_frame_before_authority() -> None:
    authority = MemoryAuthority()
    store = SnapshotPersistence[str](authority)

    def assemble(_config: Config | None) -> Graph[str]:
        return linear_graph([])

    agent = Agent("public-agent", assemble, STRING_CODEC, store, authority)
    forged = cast(Graph.Values[str], {"value": "input"})

    with pytest.raises(Graph.ValueAdmissionError):
        await agent.run(AgentStart("run", forged))

    assert authority.acquired == []
    assert store.commits == []
