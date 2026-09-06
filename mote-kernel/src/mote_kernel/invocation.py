"""The narrow typed invocation seam shared by Kernel domains.

The object implementing :class:`Invocation` is supplied by composition.  It
may be backed by a local call, a Unix socket, HTTP, gRPC, or another transport
selected by ``mote-infra/invocation`` configuration.  Kernel only chooses the
error policy at this seam:

* :func:`invoke_strict` is the required path and propagates the invocation
  error unchanged;
* :func:`invoke_typed` is the required DTO path and admits the request/result
  envelope around one strict invocation;
* :func:`invoke_best_effort` is the diagnostic path and drops an invocation
  adapter's own failure without changing the caller's business result.

Neither helper resolves a transport or retries a request.  Resolution and
transport mechanics remain owned by the infrastructure implementation.
"""

import asyncio
import math
from dataclasses import dataclass
from typing import Final, Generic, Protocol, TypeVar, runtime_checkable

from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.ports import canonical_nominal_type

BEST_EFFORT_TIMEOUT_SECONDS: Final = 1.0

RequestT_contra = TypeVar("RequestT_contra", contravariant=True)
ResultT_co = TypeVar("ResultT_co", covariant=True)
RequestT = TypeVar("RequestT")
ResultT = TypeVar("ResultT")
ValueT = TypeVar("ValueT")


class InvocationContractError(ValueError):
    """Raised when a value does not satisfy an Invocation DTO boundary."""


class InvocationTypeError(InvocationContractError):
    """Raised when a request/result fails an exact DTO type admission."""


class InvocationBoundaryError(InvocationContractError):
    """Admission failure produced by the shared ``invoke_typed`` boundary."""


class InvocationBoundaryAdmissionError(InvocationBoundaryError):
    """Internal marker for failures raised by ``invoke_typed`` admission.

    The marker keeps Port adapters from inferring error provenance from
    ``BaseException.__cause__``.  An Invocation implementation is allowed to
    raise the public ``InvocationBoundaryError`` itself; that exact object
    must cross the Port unchanged.
    """


def _validate_nominal_type(value_type: type[ValueT], field: str, /) -> None:
    """Require one concrete, non-container runtime class for an envelope."""

    try:
        canonical_nominal_type(value_type)
    except GraphValidationError as error:
        raise InvocationContractError(f"invocation {field} type must be one concrete nominal class") from error


def _admit_exact(value: ValueT, expected: type[ValueT], field: str, /) -> ValueT:
    if type(value) is not expected:
        raise InvocationTypeError(f"invocation {field} must be an exact {expected.__name__}")
    return value


@runtime_checkable
class Invocation(Protocol[RequestT_contra, ResultT_co]):
    """Strictly invoke one owner-defined typed Port request.

    Implementations are expected to return the declared result or raise.  The
    protocol deliberately contains no transport, configuration, retry, or
    fallback details.  It is a Port-to-Runtime adapter seam, not a graph-node
    runner; Graph nodes keep the separate Kernel-owned ``NodeCallable``
    contract.
    """

    async def invoke(self, request: RequestT_contra, /) -> ResultT_co: ...


@runtime_checkable
class InvocationAdmission(Protocol[RequestT, ResultT]):
    """Optional owner-specific pure admission for an Invocation envelope.

    The generic Invocation layer performs exact outer-class checks.  A domain
    owner may additionally supply this protocol to check its own immutable
    envelope invariants without moving business semantics into transport.
    """

    def admit_request(self, request: RequestT, /) -> RequestT: ...

    def admit_result(self, result: ResultT, /) -> ResultT: ...


@dataclass(frozen=True, slots=True)
class InvocationTypeContract(Generic[RequestT, ResultT]):
    """Immutable exact-class contract for one Invocation request/result pair.

    The two envelope types are runtime nominal classes.  Top types, Protocol
    declarations, abstract classes, and mutable container classes are rejected
    during composition; field-level schema rules remain with the owner
    admission supplied by the composition root.
    """

    request_type: type[RequestT]
    result_type: type[ResultT]
    admission: InvocationAdmission[RequestT, ResultT] | None = None

    def __post_init__(self) -> None:
        _validate_nominal_type(self.request_type, "request")
        _validate_nominal_type(self.result_type, "result")
        if self.admission is not None:
            try:
                request_admitter = self.admission.admit_request
                result_admitter = self.admission.admit_result
            except AttributeError as error:
                raise InvocationContractError("invocation admission must expose request/result admission") from error
            if not callable(request_admitter) or not callable(result_admitter):
                raise InvocationContractError("invocation admission methods must be callable")

    def admit_request(self, request: RequestT, /) -> RequestT:
        admitted = _admit_exact(request, self.request_type, "request")
        if self.admission is not None:
            admitted = self.admission.admit_request(admitted)
            admitted = _admit_exact(admitted, self.request_type, "admitted request")
        return admitted

    def admit_result(self, result: ResultT, /) -> ResultT:
        admitted = _admit_exact(result, self.result_type, "result")
        if self.admission is not None:
            admitted = self.admission.admit_result(admitted)
            admitted = _admit_exact(admitted, self.result_type, "admitted result")
        return admitted


def _validate_invocation_capability(invocation: Invocation[RequestT, ResultT] | None, /) -> None:
    if not isinstance(invocation, Invocation) or not callable(invocation.invoke):
        raise InvocationContractError("invocation requires a callable Invocation capability")


async def invoke_strict(
    invocation: Invocation[RequestT_contra, ResultT_co],
    request: RequestT_contra,
    /,
) -> ResultT_co:
    """Run one required invocation and preserve its exact outcome."""

    return await invocation.invoke(request)


async def invoke_typed(
    invocation: Invocation[RequestT, ResultT],
    request: RequestT,
    contract: InvocationTypeContract[RequestT, ResultT],
    /,
) -> ResultT:
    """Admit one DTO request, invoke once, and admit the DTO result.

    The helper is deliberately transport- and policy-neutral.  It performs no
    retries, failover, state writes, or Graph outcome translation; invocation
    exceptions and cancellation are allowed to propagate unchanged.
    """

    _validate_invocation_capability(invocation)
    if type(contract) is not InvocationTypeContract:
        raise InvocationContractError("invoke_typed requires an InvocationTypeContract")
    try:
        admitted_request = contract.admit_request(request)
    except InvocationContractError as error:
        raise InvocationBoundaryAdmissionError(str(error)) from error
    result = await invoke_strict(invocation, admitted_request)
    try:
        return contract.admit_result(result)
    except InvocationContractError as error:
        raise InvocationBoundaryAdmissionError(str(error)) from error


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


# ``Invocation`` remains the only star-imported contract.  Policy and DTO
# admission selection are explicit at each owner boundary through the helpers
# above; transport and resolution stay in the configured infrastructure
# implementation.
__all__ = ["Invocation"]
