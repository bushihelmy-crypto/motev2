"""Deterministic tests for the shared invocation policy adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import inf, nan
from typing import cast

import pytest

from mote_kernel.invocation import (
    Invocation,
    invoke_best_effort,
    invoke_strict,
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


def test_invocation_protocol_remains_structural() -> None:
    capability: Invocation[int, str] = _SuccessfulInvocation([])

    assert isinstance(capability, Invocation)


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
