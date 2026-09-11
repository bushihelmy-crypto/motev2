from typing import cast

import pytest
from tests.agent.persistence_fixtures import JournalPersistence, MemoryAuthority, SnapshotPersistence
from tests.execution.persistence_fixtures import STRING_CODEC, linear_graph

from mote_kernel.agent import Agent


@pytest.fixture
def authority() -> MemoryAuthority:
    return MemoryAuthority()


@pytest.fixture(params=("snapshot", "journal"))
def store(request: pytest.FixtureRequest, authority: MemoryAuthority) -> SnapshotPersistence[str]:
    return SnapshotPersistence(authority) if cast(str, request.param) == "snapshot" else JournalPersistence(authority)


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def agent(store: SnapshotPersistence[str], authority: MemoryAuthority, calls: list[str]) -> Agent[str]:
    return Agent("agent", lambda _config: linear_graph(calls), STRING_CODEC, store, authority)
