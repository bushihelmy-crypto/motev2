from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar, cast

import pytest

from mote_infra_persistence_cloudflare import Commit

ResultT = TypeVar("ResultT")
SqlValue = bytes | float | int | str | None


@dataclass(frozen=True, slots=True)
class State:
    run_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class Transition:
    scope: tuple[str, ...]
    previous_state: State | None
    candidate_state: State


class CloudflareCursor:
    def __init__(self, rows: Sequence[Mapping[str, SqlValue]] = (), rows_written: int = 0) -> None:
        self._rows = rows
        self._rows_written = rows_written

    @property
    def rowsWritten(self) -> int:  # noqa: N802 - Cloudflare API spelling
        return self._rows_written

    def toArray(self) -> Sequence[Mapping[str, SqlValue]]:  # noqa: N802 - Cloudflare API spelling
        return self._rows


class CloudflareSql:
    """Test double for Cloudflare storage.sql; it is not a local SQLite backend."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, SqlValue]] = {}
        self.force_zero_writes = False

    def exec(self, query: str, *bindings: SqlValue) -> CloudflareCursor:
        statement = query.lstrip().split(maxsplit=1)[0]
        if statement == "CREATE":
            return CloudflareCursor()
        if statement == "SELECT":
            row = self.rows.get(cast(str, bindings[0]))
            return CloudflareCursor(() if row is None else (row,))
        if statement == "INSERT":
            scope, run_id, revision, payload = bindings
            self.rows[cast(str, scope)] = {
                "run_id": run_id,
                "revision": revision,
                "payload": payload,
            }
            return CloudflareCursor(rows_written=1)
        if statement == "UPDATE":
            run_id, revision, payload, scope, expected_run_id, expected_revision = bindings
            key = cast(str, scope)
            row = self.rows.get(key)
            if (
                row is None
                or row["run_id"] != expected_run_id
                or row["revision"] != expected_revision
                or self.force_zero_writes
            ):
                return CloudflareCursor()
            self.rows[key] = {"run_id": run_id, "revision": revision, "payload": payload}
            return CloudflareCursor(rows_written=1)
        raise AssertionError(f"unexpected Cloudflare SQL statement: {statement}")


class CloudflareStorage:
    """Test double for the Durable Object storage handle injected in production."""

    def __init__(self) -> None:
        self._sql = CloudflareSql()

    @property
    def sql(self) -> CloudflareSql:
        return self._sql

    def transactionSync(self, callback: Callable[[], ResultT], /) -> ResultT:  # noqa: N802
        snapshot = {scope: dict(row) for scope, row in self.sql.rows.items()}
        try:
            return callback()
        except BaseException:
            self.sql.rows = snapshot
            raise


def transitions() -> tuple[Transition, Transition, Transition]:
    zero = State("run", 0)
    one = State("run", 1)
    two = State("run", 2)
    return (
        Transition((), None, zero),
        Transition((), zero, one),
        Transition((), one, two),
    )


@pytest.mark.asyncio
async def test_commit_uses_cloudflare_sql_and_exact_revision_cas() -> None:
    storage = CloudflareStorage()
    commit = Commit[State](storage, encode=lambda state: f"state:{state.revision}".encode())
    changes = transitions()

    for transition in changes:
        assert await commit(transition) is transition.candidate_state

    assert storage.sql.rows["[]"]["revision"] == 2
    assert storage.sql.rows["[]"]["payload"] == b"state:2"
    with pytest.raises(RuntimeError, match="stale"):
        await commit(changes[-1])


@pytest.mark.asyncio
async def test_commit_rejects_existing_initial_scope_and_lost_cas() -> None:
    storage = CloudflareStorage()
    commit = Commit[State](storage, encode=lambda _state: b"state")
    initial, update, _ = transitions()
    await commit(initial)

    with pytest.raises(RuntimeError, match="already exists"):
        await commit(initial)

    storage.sql.force_zero_writes = True
    with pytest.raises(RuntimeError, match="lost"):
        await commit(update)
    assert storage.sql.rows["[]"]["revision"] == 0


@pytest.mark.asyncio
async def test_commit_validates_encoder_scope_and_revision_contract() -> None:
    initial, update, _ = transitions()
    bad_encoder = Commit[State](CloudflareStorage(), encode=lambda _state: cast(bytes, "not-bytes"))
    with pytest.raises(TypeError, match="exact bytes"):
        await bad_encoder(initial)

    invalid_scope = Transition(("",), None, initial.candidate_state)
    with pytest.raises(ValueError, match="non-empty"):
        await Commit[State](CloudflareStorage(), encode=lambda _state: b"state")(invalid_scope)

    with pytest.raises(RuntimeError, match="revision zero"):
        await Commit[State](CloudflareStorage(), encode=lambda _state: b"state")(Transition((), None, State("run", 1)))
    with pytest.raises(RuntimeError, match="run identity"):
        await Commit[State](CloudflareStorage(), encode=lambda _state: b"state")(
            Transition((), update.previous_state, State("other", 1))
        )
    with pytest.raises(RuntimeError, match="exactly one"):
        await Commit[State](CloudflareStorage(), encode=lambda _state: b"state")(
            Transition((), update.previous_state, State("run", 9))
        )


@pytest.mark.asyncio
async def test_commit_rejects_update_when_cloudflare_row_is_missing() -> None:
    _, update, _ = transitions()
    commit = Commit[State](CloudflareStorage(), encode=lambda _state: b"state")

    with pytest.raises(RuntimeError, match="missing"):
        await commit(update)
