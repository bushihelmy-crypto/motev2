"""Deterministic tests for the shared invocation policy adapters."""

from __future__ import annotations

import asyncio
import typing
from abc import ABC, abstractmethod
from dataclasses import FrozenInstanceError, dataclass
from math import inf, nan
from typing import cast

import pytest

from mote_kernel.invocation import (
    Invocation,
    InvocationAdmission,
    InvocationBoundaryError,
    InvocationContractError,
    InvocationTypeContract,
    InvocationTypeError,
    invoke_best_effort,
    invoke_strict,
    invoke_typed,
)


@dataclass(frozen=True, slots=True)
class _SuccessfulInvocation:
    calls: list[int]

    async def invoke(self, request: int, /) -> str:
        self.calls.append(request)
        return f"result:{request}"


@dataclass(frozen=True, slots=True)
class _FailingInvocation:
    error: BaseException
    calls: list[int]

    async def invoke(self, request: int, /) -> str:
        self.calls.append(request)
        raise self.error


@dataclass(frozen=True, slots=True)
class _DtoRequest:
    value: int


@dataclass(frozen=True, slots=True)
class _DtoResult:
    value: str


@dataclass
class _TypedInvocation:
    result: _DtoResult
    calls: list[_DtoRequest]

    async def invoke(self, request: _DtoRequest, /) -> _DtoResult:
        self.calls.append(request)
        return self.result


@dataclass
class _RecordingAdmission:
    calls: list[str]

    def admit_request(self, request: _DtoRequest, /) -> _DtoRequest:
        self.calls.append("request")
        return request

    def admit_result(self, result: _DtoResult, /) -> _DtoResult:
        self.calls.append("result")
        return result


class _ProtocolDto(typing.Protocol):
    value: int


class _ConcreteProtocolDto(_ProtocolDto):
    value = 1


class _AbstractDto(ABC):
    @abstractmethod
    def value(self) -> int:
        """Expose one abstract DTO member."""


def test_invocation_protocol_remains_structural() -> None:
    capability: Invocation[int, str] = _SuccessfulInvocation([])

    assert isinstance(capability, Invocation)


def test_invocation_admission_protocol_is_structural() -> None:
    admission = _RecordingAdmission([])

    assert isinstance(admission, InvocationAdmission)


def test_invocation_type_contract_is_immutable_and_accepts_nominal_classes() -> None:
    contract = InvocationTypeContract(_DtoRequest, _DtoResult)

    assert contract.request_type is _DtoRequest
    assert contract.result_type is _DtoResult
    with pytest.raises(FrozenInstanceError):
        contract.request_type = _DtoResult  # type: ignore[misc]


def test_invocation_type_contract_uses_type_identity_for_nominal_admission() -> None:
    class ConcreteRequest:
        pass

    original_module = ConcreteRequest.__module__
    original_qualname = ConcreteRequest.__qualname__
    try:
        ConcreteRequest.__module__ = "typing"
        ConcreteRequest.__qualname__ = "Any"
        contract = InvocationTypeContract(ConcreteRequest, _DtoResult)
    finally:
        ConcreteRequest.__module__ = original_module
        ConcreteRequest.__qualname__ = original_qualname

    assert contract.request_type is ConcreteRequest


def test_invocation_type_contract_accepts_a_concrete_protocol_implementation() -> None:
    contract = InvocationTypeContract(_ConcreteProtocolDto, _DtoResult)

    assert contract.request_type is _ConcreteProtocolDto


@pytest.mark.parametrize(
    "invalid_type",
    (
        object,
        typing.Any,
        list,
        dict,
        set,
        bytearray,
        memoryview,
        _ProtocolDto,
        _AbstractDto,
        cast(type[_DtoRequest], "not-a-type"),
    ),
    ids=(
        "object",
        "any",
        "list",
        "dict",
        "set",
        "bytearray",
        "memoryview",
        "protocol",
        "abstract",
        "non-type",
    ),
)
def test_invocation_type_contract_rejects_non_concrete_request_types(invalid_type: object) -> None:
    with pytest.raises(InvocationContractError, match="request type"):
        InvocationTypeContract(cast(type[_DtoRequest], invalid_type), _DtoResult)


@pytest.mark.parametrize(
    "invalid_type",
    (
        object,
        typing.Any,
        list,
        dict,
        set,
        bytearray,
        memoryview,
        _ProtocolDto,
        _AbstractDto,
        cast(type[_DtoResult], "not-a-type"),
    ),
    ids=(
        "object",
        "any",
        "list",
        "dict",
        "set",
        "bytearray",
        "memoryview",
        "protocol",
        "abstract",
        "non-type",
    ),
)
def test_invocation_type_contract_rejects_non_concrete_result_types(invalid_type: object) -> None:
    with pytest.raises(InvocationContractError, match="result type"):
        InvocationTypeContract(_DtoRequest, cast(type[_DtoResult], invalid_type))


def test_invocation_type_contract_rejects_an_invalid_admission_shape() -> None:
    with pytest.raises(InvocationContractError, match="admission"):
        InvocationTypeContract(_DtoRequest, _DtoResult, cast(InvocationAdmission[_DtoRequest, _DtoResult], object()))

    class NonCallableAdmission:
        @property
        def admit_request(self) -> None:
            return None

        @property
        def admit_result(self) -> None:
            return None

    with pytest.raises(InvocationContractError, match="admission"):
        InvocationTypeContract(
            _DtoRequest,
            _DtoResult,
            cast(InvocationAdmission[_DtoRequest, _DtoResult], NonCallableAdmission()),
        )


@pytest.mark.asyncio
async def test_invoke_typed_admits_request_and_result_around_one_call() -> None:
    request = _DtoRequest(3)
    result = _DtoResult("ok")
    invocation = _TypedInvocation(result, [])
    admission = _RecordingAdmission([])
    contract = InvocationTypeContract(_DtoRequest, _DtoResult, admission)

    assert await invoke_typed(invocation, request, contract) is result
    assert invocation.calls == [request]
    assert admission.calls == ["request", "result"]


@pytest.mark.asyncio
async def test_invoke_typed_admits_without_owner_specific_admission() -> None:
    request = _DtoRequest(4)
    result = _DtoResult("plain")
    invocation = _TypedInvocation(result, [])

    assert await invoke_typed(invocation, request, InvocationTypeContract(_DtoRequest, _DtoResult)) is result


@pytest.mark.asyncio
async def test_invoke_typed_rejects_request_before_invocation() -> None:
    invocation = _TypedInvocation(_DtoResult("unused"), [])
    contract = InvocationTypeContract(_DtoRequest, _DtoResult)

    with pytest.raises(InvocationContractError, match="request"):
        await invoke_typed(invocation, cast(_DtoRequest, _DtoResult("wrong")), contract)
    assert invocation.calls == []


@pytest.mark.asyncio
async def test_invoke_typed_wraps_admission_failures_but_preserves_invocation_failures() -> None:
    contract = InvocationTypeContract(_DtoRequest, _DtoResult)
    with pytest.raises(InvocationBoundaryError) as admission_error:
        await invoke_typed(
            _TypedInvocation(_DtoResult("unused"), []),
            cast(_DtoRequest, _DtoResult("wrong")),
            contract,
        )
    assert admission_error.value.__cause__ is not None

    source_error = InvocationTypeError("source type error")

    @dataclass
    class SourceErrorInvocation:
        async def invoke(self, _request: _DtoRequest, /) -> _DtoResult:
            raise source_error

    with pytest.raises(InvocationTypeError) as invocation_error:
        await invoke_typed(SourceErrorInvocation(), _DtoRequest(1), contract)
    assert invocation_error.value is source_error

    boundary_error = InvocationBoundaryError("source boundary failure")

    @dataclass
    class BoundaryErrorInvocation:
        async def invoke(self, _request: _DtoRequest, /) -> _DtoResult:
            raise boundary_error

    with pytest.raises(InvocationBoundaryError) as boundary_invocation_error:
        await invoke_typed(BoundaryErrorInvocation(), _DtoRequest(1), contract)
    assert boundary_invocation_error.value is boundary_error


@pytest.mark.asyncio
async def test_invoke_typed_rejects_result_after_invocation() -> None:
    @dataclass
    class WrongResultInvocation:
        calls: list[_DtoRequest]

        async def invoke(self, request: _DtoRequest, /) -> _DtoResult:
            self.calls.append(request)
            return cast(_DtoResult, _DtoRequest(4))

    request = _DtoRequest(3)
    invocation = WrongResultInvocation([])
    contract = InvocationTypeContract(_DtoRequest, _DtoResult)

    with pytest.raises(InvocationContractError, match="result"):
        await invoke_typed(invocation, request, contract)
    assert invocation.calls == [request]


@pytest.mark.asyncio
async def test_invoke_typed_rejects_an_admission_result_that_changes_nominal_type() -> None:
    class ChangingAdmission:
        def admit_request(self, request: _DtoRequest, /) -> _DtoRequest:
            return request

        def admit_result(self, result: _DtoResult, /) -> _DtoResult:
            return cast(_DtoResult, _DtoRequest(5))

    invocation = _TypedInvocation(_DtoResult("ok"), [])
    contract = InvocationTypeContract(_DtoRequest, _DtoResult, ChangingAdmission())

    with pytest.raises(InvocationContractError, match="admitted result"):
        await invoke_typed(invocation, _DtoRequest(3), contract)


@pytest.mark.asyncio
async def test_invoke_typed_preserves_invocation_errors_and_cancellation() -> None:
    error = RuntimeError("typed failure")
    failing = _FailingInvocation(error, [])
    contract = InvocationTypeContract(int, str)

    with pytest.raises(RuntimeError) as raised:
        await invoke_typed(failing, 1, contract)
    assert raised.value is error

    cancellation = asyncio.CancelledError("typed cancellation")
    cancelled = _FailingInvocation(cancellation, [])
    with pytest.raises(asyncio.CancelledError) as raised_cancel:
        await invoke_typed(cancelled, 1, contract)
    assert raised_cancel.value is cancellation


@pytest.mark.asyncio
async def test_invoke_typed_rejects_invalid_invocation_or_contract() -> None:
    contract = InvocationTypeContract(int, str)

    class NonCallableInvocation:
        @property
        def invoke(self) -> None:
            return None

    with pytest.raises(InvocationContractError, match="Invocation capability"):
        await invoke_typed(cast(Invocation[int, str], NonCallableInvocation()), 1, contract)
    with pytest.raises(InvocationContractError, match="InvocationTypeContract"):
        await invoke_typed(_SuccessfulInvocation([]), 1, cast(InvocationTypeContract[int, str], object()))


@pytest.mark.asyncio
async def test_strict_path_returns_typed_result_and_forwards_once() -> None:
    calls: list[int] = []
    invocation = _SuccessfulInvocation(calls)

    assert await invoke_strict(invocation, 7) == "result:7"
    assert await invoke_strict(invocation, 8) == "result:8"
    assert calls == [7, 8]


@pytest.mark.asyncio
async def test_strict_path_preserves_the_exact_error_object() -> None:
    calls: list[int] = []
    error = RuntimeError("strict failure")
    invocation = _FailingInvocation(error, calls)

    with pytest.raises(RuntimeError) as raised:
        await invoke_strict(invocation, 1)
    assert raised.value is error

    with pytest.raises(RuntimeError) as wrapped:
        await invoke_strict(invocation, 2)
    assert wrapped.value is error
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_strict_path_preserves_cancellation() -> None:
    cancellation = asyncio.CancelledError("strict cancellation")
    invocation = _FailingInvocation(cancellation, [])

    with pytest.raises(asyncio.CancelledError) as raised:
        await invoke_strict(invocation, 1)
    assert raised.value is cancellation


@pytest.mark.asyncio
async def test_best_effort_path_drops_adapter_failures_and_forwards_once() -> None:
    ordinary_calls: list[int] = []
    ordinary = _FailingInvocation(RuntimeError("diagnostic failure"), ordinary_calls)
    cancelled_calls: list[int] = []
    cancelled = _FailingInvocation(asyncio.CancelledError("diagnostic cancellation"), cancelled_calls)

    assert await invoke_best_effort(ordinary, 1) is None
    assert await invoke_best_effort(ordinary, 2) is None
    assert await invoke_best_effort(cancelled, 3) is None
    assert await invoke_best_effort(cancelled, 4) is None
    assert ordinary_calls == [1, 2]
    assert cancelled_calls == [3, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize("translated", (False, True), ids=("cancelled", "translated"))
async def test_best_effort_path_drops_an_expired_adapter_call(translated: bool) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    @dataclass(frozen=True, slots=True)
    class ExpiringInvocation:
        async def invoke(self, _request: int, /) -> str:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                if translated:
                    raise RuntimeError("timeout translated") from None
                raise
            return "released"

    await asyncio.wait_for(
        invoke_best_effort(ExpiringInvocation(), 13, timeout_seconds=0.01),
        timeout=0.5,
    )
    assert entered.is_set()


@pytest.mark.asyncio
async def test_best_effort_path_drops_timeout_from_a_cooperative_adapter() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    @dataclass(frozen=True, slots=True)
    class BlockingInvocation:
        async def invoke(self, _request: int, /) -> str:
            entered.set()
            await release.wait()
            return "never"

    await invoke_best_effort(BlockingInvocation(), 13, timeout_seconds=0.01)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_best_effort_path_drops_timeout_consumed_by_adapter() -> None:
    entered = asyncio.Event()

    @dataclass(frozen=True, slots=True)
    class CancellationConsumingInvocation:
        async def invoke(self, _request: int, /) -> str:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "cancelled"
            return "released"

    await invoke_best_effort(CancellationConsumingInvocation(), 13, timeout_seconds=0.01)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_best_effort_path_propagates_caller_cancellation_after_timeout_started() -> None:
    timeout_seen = asyncio.Event()

    @dataclass(frozen=True, slots=True)
    class CleaningInvocation:
        async def invoke(self, _request: int, /) -> str:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                timeout_seen.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return "cleanup translated caller cancellation"
            return "released"

    task = asyncio.create_task(invoke_best_effort(CleaningInvocation(), 13, timeout_seconds=0.01))
    await asyncio.wait_for(timeout_seen.wait(), timeout=0.5)
    task.cancel("caller cancellation during adapter cleanup")

    with pytest.raises(asyncio.CancelledError):
        await task
    # The timeout context consumes only its own cancellation request.  The
    # caller's request remains visible after the adapter translated the second
    # cancellation into a normal return.
    assert task.cancelling() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timeout_seconds",
    (True, 0, -1, inf, nan, cast(float, "invalid")),
    ids=("bool", "zero", "negative", "infinite", "nan", "wrong-type"),
)
async def test_best_effort_rejects_an_invalid_timeout(timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        await invoke_best_effort(_SuccessfulInvocation([]), 14, timeout_seconds=timeout_seconds)


@pytest.mark.asyncio
async def test_best_effort_path_does_not_swallow_system_level_base_exceptions() -> None:
    class SystemSignal(BaseException):
        pass

    signal = SystemSignal("stop")
    invocation = _FailingInvocation(signal, [])

    with pytest.raises(SystemSignal) as raised:
        await invoke_best_effort(invocation, 1)
    assert raised.value is signal


@pytest.mark.asyncio
async def test_best_effort_path_propagates_cancellation_requested_on_calling_task() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    @dataclass(frozen=True, slots=True)
    class BlockingInvocation:
        async def invoke(self, request: int, /) -> str:
            calls.append(request)
            entered.set()
            await release.wait()
            return "released"

    task = asyncio.create_task(invoke_best_effort(BlockingInvocation(), 9))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [9]


@pytest.mark.asyncio
@pytest.mark.parametrize("translated", (False, True), ids=("swallowed", "translated"))
async def test_best_effort_path_does_not_let_adapter_consume_calling_task_cancellation(
    translated: bool,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    @dataclass(frozen=True, slots=True)
    class CancellationConsumingInvocation:
        async def invoke(self, _request: int, /) -> str:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                if translated:
                    raise RuntimeError("translated cancellation") from None
                return "cancelled but consumed"
            return "released"

    task = asyncio.create_task(invoke_best_effort(CancellationConsumingInvocation(), 11))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_best_effort_path_propagates_cancellation_pending_before_entry() -> None:
    calls: list[int] = []

    @dataclass(frozen=True, slots=True)
    class InvocationThatMustNotRun:
        async def invoke(self, request: int, /) -> str:
            calls.append(request)
            return "unexpected"

    task = asyncio.current_task()
    assert task is not None
    task.cancel("caller cancelled before diagnostic")

    with pytest.raises(asyncio.CancelledError):
        await invoke_best_effort(InvocationThatMustNotRun(), 10)
    assert calls == []


@pytest.mark.asyncio
async def test_best_effort_path_propagates_cancellation_after_successful_adapter_return() -> None:
    @dataclass(frozen=True, slots=True)
    class CancellingInvocation:
        async def invoke(self, _request: int, /) -> str:
            task = asyncio.current_task()
            assert task is not None
            task.cancel("caller cancelled after diagnostic")
            return "completed"

    task = asyncio.create_task(invoke_best_effort(CancellingInvocation(), 12))
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_best_effort_policy_forwards_one_request_once() -> None:
    """The policy helper never retries a delegate."""

    calls: list[int] = []
    invocation = _SuccessfulInvocation(calls)

    assert await invoke_best_effort(invocation, 5) is None
    assert calls == [5]
