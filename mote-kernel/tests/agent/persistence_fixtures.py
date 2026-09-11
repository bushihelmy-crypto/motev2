"""Deterministic authority and two storage representations for Agent tests."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Generic, TypeAlias, TypeVar

from tests.execution.persistence_fixtures import MemoryPersistence

from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import GraphCheckpoint, GraphPersistenceCommit
from mote_kernel.persistence import (
    AgentRunKey,
    AuthorityBusyError,
    AuthorityLostError,
    CommitApplied,
    CommitNotApplied,
    CommitOutcome,
    ExecutionAuthority,
    NeverCreated,
    PersistenceConflictError,
    PersistenceTombstoneError,
)

GraphValueT = TypeVar("GraphValueT")
CommitHook: TypeAlias = Callable[
    [ExecutionAuthority, GraphPersistenceCommit[GraphValueT]],
    Awaitable[CommitOutcome[GraphValueT] | None],
]
LoadHook: TypeAlias = Callable[
    [ExecutionAuthority, tuple[ScopeRunCoordinate, ...]],
    Awaitable[GraphCheckpoint[GraphValueT] | NeverCreated | None],
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
        self.commits: list[tuple[ExecutionAuthority, GraphPersistenceCommit[GraphValueT]]] = []
        self.reconciles: list[tuple[ExecutionAuthority, GraphPersistenceCommit[GraphValueT]]] = []
        self.on_load: LoadHook[GraphValueT] | None = None
        self.on_commit: CommitHook[GraphValueT] | None = None
        self.on_reconcile: CommitHook[GraphValueT] | None = None

    async def view(self, key: AgentRunKey) -> MemoryPersistence[GraphValueT]:
        return self.families.get(key, MemoryPersistence[GraphValueT]())

    async def apply(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[GraphValueT],
    ) -> GraphPersistenceCommit[GraphValueT]:
        self.authority.validate(authority)
        if authority.run in self.tombstones:
            raise PersistenceTombstoneError("the family was removed")
        family = self.families.setdefault(authority.run, MemoryPersistence[GraphValueT]())
        try:
            return await family(request)
        except ValueError as error:
            raise PersistenceConflictError(str(error)) from error

    async def load(
        self,
        authority: ExecutionAuthority,
        /,
        *,
        children: tuple[ScopeRunCoordinate, ...] = (),
    ) -> GraphCheckpoint[GraphValueT] | NeverCreated:
        self.loads.append((authority, children))
        self.authority.validate(authority)
        if self.on_load is not None:
            response = await self.on_load(authority, children)
            if response is not None:
                return response
        self.authority.validate(authority)
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
        try:
            confirmed = await family(request)
        except ValueError as error:
            raise PersistenceConflictError(str(error)) from error
        self.journals[authority.run] = tuple(family.requests)
        return confirmed
