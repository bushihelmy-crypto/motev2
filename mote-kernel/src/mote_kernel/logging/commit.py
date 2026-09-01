"""Commit-boundary diagnostics for graph transition callbacks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Generic, TypeVar

from mote_kernel.execution import Graph
from mote_kernel.logging.level import LogLevel
from mote_kernel.logging.port import LogSinkPort
from mote_kernel.logging.record import LogField, LogRecord, require_log_label

GraphValueT = TypeVar("GraphValueT")


def _transition_fields(transition: Graph.Transition[GraphValueT]) -> tuple[LogField, ...]:
    """Project optional coordinates without making diagnostics a commit precondition."""

    try:
        previous = transition.previous_state
        return (
            LogField("run_id", str(transition.candidate_state.run_id)),
            LogField("scope", "/".join(transition.scope) if transition.scope else None),
            LogField("scope_depth", len(transition.scope)),
            LogField("command_type", type(transition.command).__name__),
            LogField("candidate_revision", transition.candidate_state.revision),
            LogField("previous_revision", None if previous is None else previous.revision),
        )
    except (asyncio.CancelledError, Exception):
        return ()


@dataclass(frozen=True, slots=True)
class LoggedGraphCommit(Generic[GraphValueT]):
    """Log commit callback lifecycle without changing commit semantics.

    ``inner`` may be ``None`` for the process-local confirmation behavior used
    by ``Graph.run``.  A configured callback is called exactly once.  The
    wrapper reports a mismatch but returns it unchanged so the Kernel's own
    exact-successor check remains authoritative.
    """

    inner: Graph.Commit[GraphValueT] | None
    sink: LogSinkPort
    event: str = "commit"

    def __post_init__(self) -> None:
        require_log_label(self.event, "commit log event")
        require_log_label(f"{self.event}.cancelled", "commit log event")

    def _write(
        self,
        level: LogLevel,
        event: str,
        fields: tuple[LogField, ...],
        *,
        error: BaseException | None = None,
    ) -> None:
        try:
            diagnostic_fields = fields if error is None else (*fields, LogField("error_type", type(error).__name__))
            self.sink.write(LogRecord(level, event, fields=diagnostic_fields))
        except (asyncio.CancelledError, Exception):
            return

    async def __call__(
        self,
        transition: Graph.Transition[GraphValueT],
        /,
    ) -> Graph.State:
        fields = _transition_fields(transition)
        self._write(LogLevel.DEBUG, f"{self.event}.started", fields)
        started = perf_counter_ns()
        try:
            confirmed = transition.candidate_state if self.inner is None else await self.inner(transition)
        except asyncio.CancelledError as error:
            self._write(
                LogLevel.WARNING,
                f"{self.event}.cancelled",
                (
                    *fields,
                    LogField("duration_ns", perf_counter_ns() - started),
                ),
                error=error,
            )
            raise
        except Exception as error:
            self._write(
                LogLevel.ERROR,
                f"{self.event}.failed",
                (
                    *fields,
                    LogField("duration_ns", perf_counter_ns() - started),
                ),
                error=error,
            )
            raise
        exact = type(confirmed) is Graph.State and confirmed == transition.candidate_state
        self._write(
            LogLevel.INFO if exact else LogLevel.ERROR,
            f"{self.event}.accepted" if exact else f"{self.event}.mismatch",
            (
                *fields,
                LogField("outcome", "accepted" if exact else "mismatch"),
                LogField("duration_ns", perf_counter_ns() - started),
            ),
        )
        return confirmed


__all__ = ["LoggedGraphCommit"]
