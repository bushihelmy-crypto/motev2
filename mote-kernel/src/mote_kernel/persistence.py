"""Backend-neutral authority and atomic Graph persistence capabilities.

Adapters arbitrate ownership and enforce it atomically at every read, write
and reconciliation. A token contains no Kernel-owned lease clock or backend
selector. Commit outcomes concern Graph state/value writes, never tools.
"""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import GraphCheckpoint, GraphPersistenceCommit
from mote_kernel.state.graph_state import GraphRunId
from mote_kernel.state.graph_state.identity import is_canonical_identity

GraphValueT = TypeVar("GraphValueT")


class PersistenceContractError(ValueError):
    """A persistence capability violated its typed boundary contract."""


class PersistenceUnavailableError(RuntimeError):
    """A read or authority operation failed without proving absence."""


class PersistenceConflictError(RuntimeError):
    """The absent/revision/content precondition does not hold."""


class LatestHeadMovedError(PersistenceConflictError):
    """The latest index changed after a caller resolved its read fence."""


class PersistenceTombstoneError(RuntimeError):
    """A deliberately removed run must not be recreated as never-created."""


class AuthorityBusyError(RuntimeError):
    """Another invocation exclusively owns the requested Agent run."""


class AuthorityLostError(RuntimeError):
    """The presented authority is not permitted to advance this run."""


class CommitUnresolvedError(RuntimeError):
    """Reconciliation could not prove whether the exact Graph write applied."""


class CommitAttemptsExhaustedError(RuntimeError):
    """The explicit attempt budget ended with a proven unapplied write."""


@dataclass(frozen=True, slots=True)
class AgentRunKey:
    agent_id: str
    run_id: GraphRunId

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.agent_id) or not is_canonical_identity(self.run_id):
            raise PersistenceContractError("Agent and run identities must be canonical")

    @classmethod
    def admit(cls, run: "AgentRunKey", /) -> "AgentRunKey":
        if type(run) is not cls:
            raise PersistenceContractError("execution authority requires an exact Agent run key")
        try:
            return cls(run.agent_id, run.run_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise PersistenceContractError("Agent run key is malformed") from error


@dataclass(frozen=True, slots=True)
class AgentLatestHead:
    """The durable latest-run index for one Agent namespace.

    A head is deliberately only an index. Graph state, value evidence,
    Config provenance, and the AgentSession envelope remain owned by the
    ``AgentRunKey`` family named by ``key``. ``generation`` fences a latest
    lookup against an A→B→A move while the invocation is being admitted.
    """

    key: AgentRunKey
    generation: int

    def __post_init__(self) -> None:
        if type(self.key) is not AgentRunKey:
            raise PersistenceContractError("latest head requires an exact Agent run key")
        if type(self.generation) is not int or self.generation < 1:
            raise PersistenceContractError("latest head generation must be a positive integer")
        AgentRunKey.admit(self.key)

    @classmethod
    def admit(cls, head: "AgentLatestHead", /) -> "AgentLatestHead":
        if type(head) is not cls:
            raise PersistenceContractError("persistence returned an unsupported latest head")
        try:
            return cls(AgentRunKey.admit(head.key), head.generation)
        except (AttributeError, TypeError, ValueError) as error:
            raise PersistenceContractError("persistence returned a malformed latest head") from error


@dataclass(frozen=True, slots=True)
class ExecutionAuthority:
    run: AgentRunKey
    credential: bytes

    def __post_init__(self) -> None:
        AgentRunKey.admit(self.run)
        if type(self.credential) is not bytes or not self.credential:
            raise PersistenceContractError("execution authority requires a non-empty opaque credential")

    @classmethod
    def admit(cls, authority: "ExecutionAuthority", /) -> "ExecutionAuthority":
        if type(authority) is not cls:
            raise PersistenceContractError("authority acquisition must return an exact typed capability")
        try:
            return cls(AgentRunKey.admit(authority.run), authority.credential)
        except (AttributeError, TypeError, ValueError) as error:
            raise PersistenceContractError("execution authority capability is malformed") from error


@runtime_checkable
class AuthorityPort(Protocol):
    """Grant one exclusive invocation per key, or raise a typed failure.

    Acquiring a new grant is external arbitration, not inference from an old
    Graph execution lease. Release must not revoke any successor's grant.
    If acquisition cannot return a grant, the Port owns reconciliation and
    cleanup of any uncertain acquisition; Kernel has no capability to release.
    """

    async def acquire(self, run: AgentRunKey, /) -> ExecutionAuthority: ...

    async def release(self, authority: ExecutionAuthority, /) -> None: ...


@dataclass(frozen=True, slots=True)
class NeverCreated:
    """An authoritative negative read, distinct from loss or tombstones."""


@dataclass(frozen=True, slots=True)
class CommitApplied(Generic[GraphValueT]):
    confirmed: GraphPersistenceCommit[GraphValueT]

    def __post_init__(self) -> None:
        if type(self.confirmed) is not GraphPersistenceCommit:
            raise PersistenceContractError("Applied outcome requires an exact Graph commit confirmation")
        try:
            self.confirmed.admit()
        except (AttributeError, SnapshotMismatchError, TypeError, ValueError) as error:
            raise PersistenceContractError("Applied outcome contains a malformed Graph commit confirmation") from error

    def admit(self) -> "CommitApplied[GraphValueT]":
        if type(self) is not CommitApplied:
            raise PersistenceContractError("persistence returned an unsupported Applied variant")
        try:
            return CommitApplied(self.confirmed.admit())
        except (AttributeError, SnapshotMismatchError, TypeError, ValueError) as error:
            raise PersistenceContractError("persistence returned a malformed Applied outcome") from error


@dataclass(frozen=True, slots=True)
class CommitNotApplied:
    """The exact request is proven unapplied under a still-valid authority."""


@dataclass(frozen=True, slots=True)
class CommitUnknown:
    """The outcome is uncertain; it is neither absence nor permission to retry."""


CommitOutcome: TypeAlias = CommitApplied[GraphValueT] | CommitNotApplied | CommitUnknown


@runtime_checkable
class PersistencePort(Protocol[GraphValueT]):
    """One authority-constrained, consistent family read and atomic write Port.

    Load includes all existing family state and frame records. Requested child
    coordinates additionally require explicit negative evidence if never-created;
    missing or tombstoned durable records are errors, not negative evidence.
    Before Kernel writes, repeated reads under one authority must agree on every
    existing fact. Adapters need not interpret Graph topology to answer these reads.

    Commit validates authority, scoped revision/absence and complete request
    identity atomically with state, frames, the optional AgentSession snapshot,
    and receipt. A root revision-0 commit must upsert the latest head in that
    same durable transaction (or an equivalent reconciled boundary). An
    identical key/content is an exact replay; different content is a conflict.
    Uncertain writes return CommitUnknown, not a transport exception purporting
    to prove non-application.
    Reconcile checks the same immutable request, not just its candidate state;
    for a root request it may return ``CommitApplied`` only after proving the
    family receipt and its latest-head update as one durable fact.

    ``load_latest`` is an Agent-scoped, linear-consistent metadata read. It
    returns only the exact family key and a generation fence; callers must
    acquire authority for that key and then pass the returned head to
    ``load``. It must never synthesize a head from a stale cache or from an
    ordering of user-supplied run IDs.
    """

    async def load_latest(self, agent_id: str, /) -> AgentLatestHead | NeverCreated: ...

    async def load(
        self,
        authority: ExecutionAuthority,
        /,
        *,
        children: tuple[ScopeRunCoordinate, ...] = (),
        expected_latest: AgentLatestHead | None = None,
    ) -> GraphCheckpoint[GraphValueT] | NeverCreated: ...

    async def commit(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[GraphValueT],
        /,
    ) -> CommitOutcome[GraphValueT]: ...

    async def reconcile(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[GraphValueT],
        /,
    ) -> CommitOutcome[GraphValueT]: ...


__all__ = [
    "AgentLatestHead",
    "AgentRunKey",
    "AuthorityBusyError",
    "AuthorityLostError",
    "AuthorityPort",
    "CommitApplied",
    "CommitAttemptsExhaustedError",
    "CommitNotApplied",
    "CommitOutcome",
    "CommitUnknown",
    "CommitUnresolvedError",
    "ExecutionAuthority",
    "LatestHeadMovedError",
    "NeverCreated",
    "PersistenceConflictError",
    "PersistenceContractError",
    "PersistencePort",
    "PersistenceTombstoneError",
    "PersistenceUnavailableError",
]
