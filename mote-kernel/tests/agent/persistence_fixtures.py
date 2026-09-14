"""Deterministic authority and two storage representations for Agent tests."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from tests.execution.persistence_fixtures import MemoryPersistence

from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import GraphCheckpoint, GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentLatestHead,
    AgentRunKey,
    AuthorityBusyError,
    AuthorityLostError,
    CommitApplied,
    CommitNotApplied,
    CommitOutcome,
    ExecutionAuthority,
    LatestHeadMovedError,
    NeverCreated,
    PersistenceConflictError,
    PersistenceContractError,
    PersistenceTombstoneError,
)

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class ProcessCommitRecord(Generic[GraphValueT]):
    """A process-journal receipt tied to its owning Agent family."""

    key: AgentRunKey
    request: GraphPersistenceCommit[GraphValueT]

    def __post_init__(self) -> None:
        AgentRunKey.admit(self.key)
        if type(self.request) is not GraphPersistenceCommit:
            raise PersistenceContractError("process journal requires an exact Graph commit")
        self.request.admit()
        if not self.request.scope and self.request.candidate_state.run_id != self.key.run_id:
            raise PersistenceContractError("root journal commit does not match its Agent run key")


CommitHook: TypeAlias = Callable[
    [ExecutionAuthority, GraphPersistenceCommit[GraphValueT]],
    Awaitable[CommitOutcome[GraphValueT] | None],
]
LoadHook: TypeAlias = Callable[
    [ExecutionAuthority, tuple[ScopeRunCoordinate, ...]],
    Awaitable[GraphCheckpoint[GraphValueT] | NeverCreated | None],
]
LatestLoadHook: TypeAlias = Callable[
    [str],
    Awaitable[AgentLatestHead | NeverCreated | None],
]


class AsyncGate:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.proceed = asyncio.Event()
        self.finished = asyncio.Event()

    async def wait(self) -> None:
        self.entered.set()
        try:
            await self.proceed.wait()
        finally:
            self.finished.set()


class MemoryAuthority:
    def __init__(self) -> None:
        self.sequence = 0
        self.current: dict[AgentRunKey, ExecutionAuthority] = {}
        self.acquired: list[ExecutionAuthority] = []
        self.released: list[ExecutionAuthority] = []
        self.acquire_gate: AsyncGate | None = None
        self.release_gate: AsyncGate | None = None
        self.acquire_error: BaseException | None = None
        self.release_error: BaseException | None = None

    async def acquire(self, run: AgentRunKey, /) -> ExecutionAuthority:
        if self.acquire_error is not None:
            raise self.acquire_error
        if run in self.current:
            raise AuthorityBusyError("the run already has a live owner")
        self.sequence += 1
        authority = ExecutionAuthority(run, f"grant-{self.sequence}".encode())
        self.current[run] = authority
        self.acquired.append(authority)
        if self.acquire_gate is not None:
            await self.acquire_gate.wait()
        return authority

    async def release(self, authority: ExecutionAuthority, /) -> None:
        if self.release_gate is not None:
            await self.release_gate.wait()
        self.released.append(authority)
        if self.current.get(authority.run) == authority:
            del self.current[authority.run]
        if self.release_error is not None:
            raise self.release_error

    def validate(self, authority: ExecutionAuthority) -> None:
        if self.current.get(authority.run) != authority:
            raise AuthorityLostError("the grant has expired or been replaced")


class SnapshotPersistence(Generic[GraphValueT]):
    def __init__(self, authority: MemoryAuthority) -> None:
        self.authority = authority
        self.families: dict[AgentRunKey, MemoryPersistence[GraphValueT]] = {}
        self.tombstones: set[AgentRunKey] = set()
        self.child_tombstones: set[ScopeRunCoordinate] = set()
        self.loads: list[tuple[ExecutionAuthority, tuple[ScopeRunCoordinate, ...]]] = []
        self.load_fences: list[AgentLatestHead | None] = []
        self.commits: list[tuple[ExecutionAuthority, GraphPersistenceCommit[GraphValueT]]] = []
        self.reconciles: list[tuple[ExecutionAuthority, GraphPersistenceCommit[GraphValueT]]] = []
        self.heads: dict[str, AgentLatestHead] = {}
        self.latest_loads: list[str] = []
        self.on_load: LoadHook[GraphValueT] | None = None
        self.on_load_latest: LatestLoadHook | None = None
        self.on_commit: CommitHook[GraphValueT] | None = None
        self.on_reconcile: CommitHook[GraphValueT] | None = None

    async def view(self, key: AgentRunKey) -> MemoryPersistence[GraphValueT]:
        return self.families.get(key, MemoryPersistence[GraphValueT]())

    async def load_latest(self, agent_id: str, /) -> AgentLatestHead | NeverCreated:
        self.latest_loads.append(agent_id)
        if type(agent_id) is not str or not agent_id or agent_id != agent_id.strip():
            raise PersistenceContractError("latest lookup requires a canonical Agent identity")
        if self.on_load_latest is not None:
            response = await self.on_load_latest(agent_id)
            if response is not None:
                return response
        head = self.heads.get(agent_id)
        return NeverCreated() if head is None else deepcopy(head)

    def _check_latest_fence(
        self,
        authority: ExecutionAuthority,
        expected_latest: AgentLatestHead | None,
    ) -> None:
        if expected_latest is None:
            return
        expected = AgentLatestHead.admit(expected_latest)
        if expected.key != authority.run:
            raise LatestHeadMovedError("latest head names a different Agent run")
        current = self.heads.get(authority.run.agent_id)
        if current != expected:
            raise LatestHeadMovedError("latest head moved during recovery")

    def _advance_latest(self, authority: ExecutionAuthority, request: GraphPersistenceCommit[GraphValueT]) -> None:
        if (
            request.scope
            or request.candidate_state.revision != 0
            or request.candidate_state.run_id != authority.run.run_id
        ):
            return
        current = self.heads.get(authority.run.agent_id)
        if current is not None and current.key == authority.run:
            return
        generation = 1 if current is None else current.generation + 1
        self.heads[authority.run.agent_id] = AgentLatestHead(authority.run, generation)

    async def apply(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[GraphValueT],
    ) -> GraphPersistenceCommit[GraphValueT]:
        self.authority.validate(authority)
        if authority.run in self.tombstones:
            raise PersistenceTombstoneError("the family was removed")
        family = self.families.setdefault(authority.run, MemoryPersistence[GraphValueT]())
        was_present = any(
            (existing.scope, existing.writes.commit_key) == (request.scope, request.writes.commit_key)
            for existing in family.requests
        )
        try:
            confirmed = await family(request)
        except ValueError as error:
            raise PersistenceConflictError(str(error)) from error
        if not was_present:
            self._advance_latest(authority, request)
        return confirmed

    async def load(
        self,
        authority: ExecutionAuthority,
        /,
        *,
        children: tuple[ScopeRunCoordinate, ...] = (),
        expected_latest: AgentLatestHead | None = None,
    ) -> GraphCheckpoint[GraphValueT] | NeverCreated:
        self.loads.append((authority, children))
        self.load_fences.append(deepcopy(expected_latest))
        self.authority.validate(authority)
        self._check_latest_fence(authority, expected_latest)
        if self.on_load is not None:
            response = await self.on_load(authority, children)
            self.authority.validate(authority)
            self._check_latest_fence(authority, expected_latest)
            if response is not None:
                return response
        else:
            self.authority.validate(authority)
            self._check_latest_fence(authority, expected_latest)
        if authority.run in self.tombstones or self.child_tombstones.intersection(children):
            raise PersistenceTombstoneError("the requested durable record was removed")
        family = await self.view(authority.run)
        if not family.requests:
            return NeverCreated()
        return deepcopy(family.checkpoint(authority.run.run_id, child_reads=children))

    async def commit(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[GraphValueT],
        /,
    ) -> CommitOutcome[GraphValueT]:
        self.commits.append((authority, request))
        self.authority.validate(authority)
        if self.on_commit is not None:
            response = await self.on_commit(authority, request)
            if response is not None:
                return response
        return CommitApplied(await self.apply(authority, request))

    async def reconcile(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[GraphValueT],
        /,
    ) -> CommitOutcome[GraphValueT]:
        self.reconciles.append((authority, request))
        self.authority.validate(authority)
        if self.on_reconcile is not None:
            response = await self.on_reconcile(authority, request)
            if response is not None:
                return response
        self.authority.validate(authority)
        family = await self.view(authority.run)
        for receipt in family.requests:
            if (receipt.scope, receipt.writes.commit_key) == (request.scope, request.writes.commit_key):
                if receipt != request:
                    raise PersistenceConflictError("a commit key already names different content")
                return CommitApplied(deepcopy(receipt))
        return CommitNotApplied()


class JournalPersistence(SnapshotPersistence[GraphValueT]):
    def __init__(self, authority: MemoryAuthority) -> None:
        super().__init__(authority)
        self.journals: dict[AgentRunKey, tuple[GraphPersistenceCommit[GraphValueT], ...]] = {}

    async def view(self, key: AgentRunKey) -> MemoryPersistence[GraphValueT]:
        family = MemoryPersistence[GraphValueT]()
        for receipt in self.journals.get(key, ()):
            await family(receipt)
        return family

    async def apply(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[GraphValueT],
    ) -> GraphPersistenceCommit[GraphValueT]:
        self.authority.validate(authority)
        if authority.run in self.tombstones:
            raise PersistenceTombstoneError("the family was removed")
        family = await self.view(authority.run)
        was_present = any(
            (existing.scope, existing.writes.commit_key) == (request.scope, request.writes.commit_key)
            for existing in family.requests
        )
        try:
            confirmed = await family(request)
        except ValueError as error:
            raise PersistenceConflictError(str(error)) from error
        self.journals[authority.run] = tuple(family.requests)
        if not was_present:
            self._advance_latest(authority, request)
        return confirmed
