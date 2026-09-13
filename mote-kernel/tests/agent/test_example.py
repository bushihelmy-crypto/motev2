from typing import assert_type

import pytest
from example.graph.checkpointed_import import ImportJob, ImportStatus
from example.graph.durable_agent_import import build_agent, demonstrate
from tests.agent.persistence_fixtures import JournalPersistence, MemoryAuthority, SnapshotPersistence

from mote_kernel.agent import AgentCompleted, AgentResult, AgentResume


@pytest.mark.asyncio
@pytest.mark.parametrize("journal", [False, True])
async def test_typed_import_example_uses_only_agent_requests_and_business_results(journal: bool) -> None:
    authority = MemoryAuthority()
    store = JournalPersistence[ImportJob](authority) if journal else SnapshotPersistence[ImportJob](authority)
    result = await demonstrate(store, authority, run_id="example", source="input.csv")
    assert_type(result, AgentCompleted[ImportJob, str, str])
    assert result.outputs["job"] == ImportJob("input.csv", True, ImportStatus.LOADED)
    writes = len(store.commits)
    replay = await build_agent(store, authority).run(AgentResume[ImportJob]("example"))
    assert_type(replay, AgentResult[ImportJob, str, str])
    assert isinstance(replay, AgentCompleted)
    assert replay.outputs["job"] == result.outputs["job"]
    assert len(store.commits) == writes
    assert authority.released == authority.acquired
