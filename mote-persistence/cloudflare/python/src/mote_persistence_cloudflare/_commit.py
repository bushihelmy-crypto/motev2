"""Cloudflare Durable Object storage implementation of the Commit Port."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from json import dumps
from typing import Protocol

_SqlValue = bytes | float | int | str | None


class _VersionedState(Protocol):
    @property
    def run_id(self) -> object: ...

    @property
    def revision(self) -> int: ...


class _Transition[StateT: _VersionedState](Protocol):
    @property
    def scope(self) -> tuple[str, ...]: ...

    @property
    def previous_state(self) -> StateT | None: ...

    @property
    def candidate_state(self) -> StateT: ...


class _SqlCursor(Protocol):
    @property
    def rowsWritten(self) -> int: ...  # noqa: N802 - Cloudflare API spelling

    def toArray(self) -> Sequence[Mapping[str, _SqlValue]]: ...  # noqa: N802 - Cloudflare API spelling


class _SqlStorage(Protocol):
    def exec(self, query: str, *bindings: _SqlValue) -> _SqlCursor: ...


class _DurableObjectStorage(Protocol):
    @property
    def sql(self) -> _SqlStorage: ...

    def transactionSync[ResultT](self, callback: Callable[[], ResultT], /) -> ResultT: ...  # noqa: N802


class _PersistenceConflictError(RuntimeError):
    pass


class Commit[StateT: _VersionedState]:
    """Persist one exact transition with Cloudflare storage.sql CAS."""

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS mote_graph_state_v1 (
            scope TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 0),
            payload BLOB NOT NULL
        ) STRICT
    """
    _SELECT = """
        SELECT run_id, revision
        FROM mote_graph_state_v1
        WHERE scope = ?
    """
    _INSERT = """
        INSERT INTO mote_graph_state_v1 (scope, run_id, revision, payload)
        VALUES (?, ?, ?, ?)
    """
    _UPDATE = """
        UPDATE mote_graph_state_v1
        SET run_id = ?, revision = ?, payload = ?
        WHERE scope = ? AND run_id = ? AND revision = ?
    """

    def __init__(
        self,
        storage: _DurableObjectStorage,
        *,
        encode: Callable[[StateT], bytes],
    ) -> None:
        self._storage = storage
        self._encode_state = encode
        self._storage.sql.exec(self._CREATE_TABLE)

    async def __call__(self, transition: _Transition[StateT], /) -> StateT:
        """Atomically persist one exact candidate and return its confirmation."""

        previous = transition.previous_state
        candidate = transition.candidate_state
        self._validate_transition(previous, candidate)
        payload = self._encode(candidate)
        scope = self._scope_key(transition.scope)

        def commit() -> None:
            rows = self._storage.sql.exec(self._SELECT, scope).toArray()
            if previous is None:
                if rows:
                    raise _PersistenceConflictError("state scope already exists")
                self._storage.sql.exec(
                    self._INSERT,
                    scope,
                    str(candidate.run_id),
                    candidate.revision,
                    payload,
                )
                return

            if len(rows) != 1:
                raise _PersistenceConflictError("state scope is missing or duplicated")
            row = rows[0]
            if row.get("run_id") != str(previous.run_id) or row.get("revision") != previous.revision:
                raise _PersistenceConflictError("transition is based on a stale durable revision")
            updated = self._storage.sql.exec(
                self._UPDATE,
                str(candidate.run_id),
                candidate.revision,
                payload,
                scope,
                str(previous.run_id),
                previous.revision,
            )
            if updated.rowsWritten != 1:
                raise _PersistenceConflictError("transition lost its durable compare-and-swap")

        self._storage.transactionSync(commit)
        return candidate

    def _encode(self, state: StateT) -> bytes:
        payload = self._encode_state(state)
        if type(payload) is not bytes:
            raise TypeError("Commit encode must return exact bytes")
        return payload

    @staticmethod
    def _scope_key(scope: tuple[str, ...]) -> str:
        if any(type(part) is not str or not part for part in scope):
            raise ValueError("state scope parts must be non-empty exact strings")
        return dumps(scope, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _validate_transition(previous: StateT | None, candidate: StateT) -> None:
        if previous is None:
            if candidate.revision != 0:
                raise _PersistenceConflictError("initial state must use revision zero")
            return
        if candidate.run_id != previous.run_id:
            raise _PersistenceConflictError("a transition cannot replace its run identity")
        if candidate.revision != previous.revision + 1:
            raise _PersistenceConflictError("a transition must advance exactly one revision")


__all__ = ["Commit"]
