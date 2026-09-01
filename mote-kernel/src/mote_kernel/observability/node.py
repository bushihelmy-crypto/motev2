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
    except asyncio.CancelledError:
        return
    except Exception:
        return


@dataclass(frozen=True, slots=True)
class ObservedNode:
    """Configure a provider-neutral span lifecycle for one node callable."""

    port: ObservabilityPort
    span_factory: NodeSpanFactory

    def __call__(
        self,
        inner: NodeOperation[InputT, OutputT],
    ) -> NodeOperation[InputT, OutputT]:
        return _ObservedNode(inner, self)


@dataclass(frozen=True, slots=True)
class _ObservedNode(Generic[InputT, OutputT]):
    """Apply one immutable observation configuration to a typed node callable."""

    inner: NodeOperation[InputT, OutputT]
    config: ObservedNode

    async def __call__(self, value: InputT, /) -> OutputT:
        span = self.config.span_factory()
        if type(span) is not Span:
            raise ObservationContractError("node span factory must return a Span")
        _record(self.config.port, SpanStarted(span))
        started = perf_counter_ns()
        try:
            result = await self.inner(value)
        except asyncio.CancelledError:
            error = ObservationError(category="node.cancelled")
            duration = perf_counter_ns() - started
            _record(self.config.port, SpanFinished(span.context, SpanStatus.ERROR, duration, error))
            raise
        except Exception:
            error = ObservationError(category="node.exception")
            duration = perf_counter_ns() - started
            _record(self.config.port, SpanFinished(span.context, SpanStatus.ERROR, duration, error))
            raise
        _record(self.config.port, SpanFinished(span.context, SpanStatus.OK, perf_counter_ns() - started))
        return result


__all__ = ["ObservedNode"]
