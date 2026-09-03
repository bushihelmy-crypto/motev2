"""Commit-boundary diagnostics for graph transition callbacks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Generic, TypeVar

from mote_kernel.execution import Graph
from mote_kernel.logging.emit import write_diagnostic
from mote_kernel.logging.level import LogLevel
from mote_kernel.logging.port import LogSinkPort
from mote_kernel.logging.record import LogField, require_log_label

GraphValueT = TypeVar("GraphValueT")


def _candidate_state(transition: Graph.Transition[GraphValueT]) -> Graph.State | None:
    """Read the candidate once so diagnostic projection cannot re-read it later."""

    try:
        candidate = transition.candidate_state
    except asyncio.CancelledError:
        return None
    except Exception:
        return None
    return candidate if type(candidate) is Graph.State else None


def _transition_fields(
    transition: Graph.Transition[GraphValueT],
    candidate: Graph.State | None,
) -> tuple[LogField, ...]:
    """Project optional coordinates without making diagnostics a commit precondition."""

    if candidate is None:
        return ()
    try:
        previous = transition.previous_state
        return (
            LogField("run_id", str(candidate.run_id)),
            LogField("scope", "/".join(transition.scope) if transition.scope else None),
            LogField("scope_depth", len(transition.scope)),
            LogField("command_type", type(transition.command).__name__),
            LogField("candidate_revision", candidate.revision),
            LogField("previous_revision", None if previous is None else previous.revision),
        )
    except asyncio.CancelledError:
        return ()
    except Exception:
        return ()


@dataclass(frozen=True, slots=True)
class LoggedGraphCommit:
    """Configure lifecycle diagnostics for one caller-owned commit callback."""

    sink: LogSinkPort
    event: str = field(default="commit", kw_only=True)

    def __post_init__(self) -> None:
        require_log_label(self.event, "commit log event")
        require_log_label(f"{self.event}.cancelled", "commit log event")

    def __call__(
        self,
        inner: Graph.Commit[GraphValueT],
    ) -> Graph.Commit[GraphValueT]:
        return _LoggedCommit(inner, self)


@dataclass(frozen=True, slots=True)
class _LoggedCommit(Generic[GraphValueT]):
    """Apply one immutable logging configuration to a typed commit callback."""

    inner: Graph.Commit[GraphValueT]
    config: LoggedGraphCommit

    async def __call__(
        self,
        transition: Graph.Transition[GraphValueT],
        /,
    ) -> Graph.State:
        candidate = _candidate_state(transition)
        fields = _transition_fields(transition, candidate)
        await write_diagnostic(self.config.sink, LogLevel.DEBUG, f"{self.config.event}.started", fields)
        started = perf_counter_ns()
        try:
            returned = await self.inner(transition)
        except asyncio.CancelledError as error:
            with suppress(asyncio.CancelledError):
                await write_diagnostic(
                    self.config.sink,
                    LogLevel.WARNING,
                    f"{self.config.event}.cancelled",
                    (
                        *fields,
                        LogField("duration_ns", perf_counter_ns() - started),
                    ),
                    error=error,
                )
            raise
        except Exception as error:
            with suppress(asyncio.CancelledError):
                await write_diagnostic(
                    self.config.sink,
                    LogLevel.ERROR,
                    f"{self.config.event}.failed",
                    (
                        *fields,
                        LogField("duration_ns", perf_counter_ns() - started),
                    ),
                    error=error,
                )
            raise
        exact = candidate is not None and type(returned) is Graph.State and returned == candidate
        with suppress(asyncio.CancelledError):
            await write_diagnostic(
                self.config.sink,
                LogLevel.INFO if exact else LogLevel.ERROR,
                f"{self.config.event}.accepted" if exact else f"{self.config.event}.mismatch",
                (
                    *fields,
                    LogField("outcome", "accepted" if exact else "mismatch"),
                    LogField("duration_ns", perf_counter_ns() - started),
                ),
            )
        return returned


__all__ = ["LoggedGraphCommit"]
