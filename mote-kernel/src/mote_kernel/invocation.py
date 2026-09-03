"""The narrow typed invocation seam shared by Kernel domains.

The object implementing :class:`Invocation` is supplied by composition.  It
may be backed by a local call, a Unix socket, HTTP, gRPC, or another transport
selected by ``mote-infra/invocation`` configuration.  Kernel only chooses the
error policy at this seam:

* :func:`invoke_strict` is the required path and propagates the invocation
  error unchanged;
* :func:`invoke_best_effort` is the diagnostic path and drops an invocation
  adapter's own failure without changing the caller's business result.

Neither helper resolves a transport or retries a request.  Resolution and
transport mechanics remain owned by the infrastructure implementation.
"""

import asyncio
import math
from typing import Final, Protocol, TypeVar, runtime_checkable

BEST_EFFORT_TIMEOUT_SECONDS: Final = 1.0

RequestT_contra = TypeVar("RequestT_contra", contravariant=True)
ResultT_co = TypeVar("ResultT_co", covariant=True)


@runtime_checkable
class Invocation(Protocol[RequestT_contra, ResultT_co]):
    """Strictly invoke one owner-defined typed request.

    Implementations are expected to return the declared result or raise.  The
    protocol deliberately contains no transport, configuration, retry, or
    fallback details.
    """

    async def invoke(self, request: RequestT_contra, /) -> ResultT_co: ...


async def invoke_strict(
    invocation: Invocation[RequestT_contra, ResultT_co],
    request: RequestT_contra,
    /,
) -> ResultT_co:
    """Run one required invocation and preserve its exact outcome."""

    return await invocation.invoke(request)


async def invoke_best_effort(
    invocation: Invocation[RequestT_contra, ResultT_co],
    request: RequestT_contra,
    /,
    *,
    timeout_seconds: float = BEST_EFFORT_TIMEOUT_SECONDS,
) -> None:
    """Run one diagnostic invocation and isolate only adapter-owned failures.

    ``CancelledError`` raised directly by an invocation adapter is treated as
    a failed diagnostic.  Cancellation requested on the *calling* task while
    the adapter is running remains a business cancellation and is propagated.
    The invocation is given a finite cooperative deadline; timeout is an
    adapter-owned diagnostic failure.  The cancellation counter distinguishes
    a new cancellation request observed on the calling task from a directly
    raised adapter ``CancelledError``; when the deadline is expired, one
    cancellation count is reserved for ``asyncio.timeout`` and any additional
    count still propagates as a caller cancellation.  The distinction is
    intentionally limited to the normal cooperative contract: Invocation
    implementations must not mutate the calling task's cancellation state.
    Adapter-side ``uncancel()`` is outside this contract for now.
    """

    if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
        raise ValueError("best-effort timeout must be a finite positive number")
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout):
        raise ValueError("best-effort timeout must be a finite positive number")

    task = asyncio.current_task()
    assert task is not None
    # A cancellation requested before this coroutine starts is delivered at
    # its first suspension point.  Give the task that suspension point before
    # recording the baseline; otherwise a pending caller cancellation would
    # look indistinguishable from an adapter-raised ``CancelledError``.
    if task.cancelling():
        await asyncio.sleep(0)
    cancellation_count = task.cancelling()
    async with asyncio.timeout(timeout) as deadline:
        # The timeout contributes exactly one cancellation request while its
        # body is still running.  A larger delta is an additional caller
        # request, even when the adapter is cleaning up the timeout.
        # Read the task count before the deadline state so a simultaneous
        # caller request is never hidden by an already-expired deadline.
        invocation_error: Exception | asyncio.CancelledError | None = None
        try:
            await invocation.invoke(request)
        except asyncio.CancelledError as error:
            invocation_error = error
        except Exception as error:
            invocation_error = error
        observed_cancellations = task.cancelling()
        deadline_expired = deadline.expired()
        if observed_cancellations > cancellation_count + int(deadline_expired):
            if isinstance(invocation_error, asyncio.CancelledError):
                raise invocation_error
            raise asyncio.CancelledError from None
        return


# ``Invocation`` remains the only star-imported contract.  Policy selection is
# explicit at each owner boundary through the two helpers above; transport and
# resolution stay in the configured infrastructure implementation.
__all__ = ["Invocation"]
