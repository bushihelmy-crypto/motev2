"""Provider-neutral span decoration around one asynchronous node call."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Generic, Protocol, TypeAlias, TypeVar

from mote_kernel.observability.port import ObservabilityPort
from mote_kernel.observability.record import ObservationError, SpanFinished, SpanStarted
from mote_kernel.observability.span import ObservationContractError, Span, SpanStatus

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
NodeOperation: TypeAlias = Callable[[InputT], Awaitable[OutputT]]


class NodeSpanFactory(Protocol):
    """Create a fresh span for each invocation.

    The factory is where Role/runtime code supplies run, scope, parent, and
    node identities.  Calling it per invocation keeps the wrapper safe for
    concurrent graph runs and avoids hidden context state in Kernel.
    """

    def __call__(self, /) -> Span: ...


def _record(port: ObservabilityPort, observation: SpanStarted | SpanFinished) -> None:
    """Keep an observation sink from changing node result semantics."""

    try:
        port.record(observation)
    except (asyncio.CancelledError, Exception):
        return


@dataclass(frozen=True, slots=True)
class ObservedNode(Generic[InputT, OutputT]):
    """Wrap one asynchronous operation with a start/finish span lifecycle.

    The wrapper observes invocation success, ordinary exceptions, and
    cancellation.  It never converts or swallows the operation's result or
    exception.  Graph-specific ``Success``/``Failure`` values remain the
    execution/settlement concern; a returned value is an invocation success.
    """

    inner: NodeOperation[InputT, OutputT]
    port: ObservabilityPort
    span_factory: NodeSpanFactory

    async def __call__(self, value: InputT, /) -> OutputT:
        span = self.span_factory()
        if type(span) is not Span:
            raise ObservationContractError("node span factory must return a Span")
        _record(self.port, SpanStarted(span))
        started = perf_counter_ns()
        try:
            result = await self.inner(value)
        except asyncio.CancelledError:
            error = ObservationError(category="node.cancelled")
            duration = perf_counter_ns() - started
            _record(self.port, SpanFinished(span.context, SpanStatus.ERROR, duration, error))
            raise
        except Exception:
            error = ObservationError(category="node.exception")
            duration = perf_counter_ns() - started
            _record(self.port, SpanFinished(span.context, SpanStatus.ERROR, duration, error))
            raise
        _record(self.port, SpanFinished(span.context, SpanStatus.OK, perf_counter_ns() - started))
        return result


__all__ = ["ObservedNode"]
