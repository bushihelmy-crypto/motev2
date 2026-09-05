"""Tests for the Think Port-to-Invocation adapters."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass
from typing import Generic, Never, TypeVar, cast

import pytest

from mote_kernel.think.contract import CommandPort as CommandPortContract
from mote_kernel.think.contract import CompactPort as CompactPortContract
from mote_kernel.think.contract import ContextPort as ContextPortContract
from mote_kernel.think.contract import InferencePort as InferencePortContract
from mote_kernel.think.contract import PromptPort as PromptPortContract
from mote_kernel.think.port import CommandPort, CompactPort, ContextPort, InferencePort, PromptPort

RequestT = TypeVar("RequestT")
ResultT = TypeVar("ResultT")


@dataclass
class _RecordingInvocation(Generic[RequestT, ResultT]):
    result: ResultT
    calls: list[RequestT]

    async def invoke(self, request: RequestT, /) -> ResultT:
        self.calls.append(request)
        return self.result


@dataclass
class _RaisingInvocation(Generic[RequestT, ResultT]):
    error: BaseException

    async def invoke(self, _request: RequestT, /) -> ResultT:
        raise self.error


class _NonCallableInvocation:
    @property
    def invoke(self) -> None:
        return None


def test_think_ports_are_frozen_slot_adapters_and_match_contracts() -> None:
    prompt: PromptPort[str, str, str, str] = PromptPort(
        _RecordingInvocation[str, str]("system", []),
        _RecordingInvocation[str, str]("placeholder", []),
        _RecordingInvocation[str, str]("user", []),
    )
    context: ContextPort[str, str] = ContextPort(_RecordingInvocation[str, str]("context", []))
    compact: CompactPort[str, str] = CompactPort(_RecordingInvocation[str, str]("compact", []))
    inference: InferencePort[str, str] = InferencePort(_RecordingInvocation[str, str]("inference", []))
    command: CommandPort[str, str] = CommandPort(_RecordingInvocation[str, str]("command", []))

    assert isinstance(prompt, PromptPortContract)
    assert isinstance(context, ContextPortContract)
    assert isinstance(compact, CompactPortContract)
    assert isinstance(inference, InferencePortContract)
    assert isinstance(command, CommandPortContract)
    for port in (prompt, context, compact, inference, command):
        assert "__dict__" not in type(port).__slots__
    with pytest.raises(FrozenInstanceError):
        context.invocation = cast(object, None)  # type: ignore[misc]


@pytest.mark.asyncio
async def test_prompt_port_forwards_each_operation_once() -> None:
    payload = "payload"
    system = _RecordingInvocation[str, str]("system", [])
    placeholder = _RecordingInvocation[str, str]("placeholder", [])
    user = _RecordingInvocation[str, str]("user", [])
    port: PromptPort[str, str, str, str] = PromptPort(system, placeholder, user)

    assert await port.load_system_prompt(payload) == "system"
    assert await port.load_placeholder(payload) == "placeholder"
    assert await port.load_user_prompt(payload) == "user"
    assert system.calls == [payload]
    assert placeholder.calls == [payload]
    assert user.calls == [payload]


@pytest.mark.asyncio
async def test_stage_ports_forward_the_exact_request_once() -> None:
    request = "request"
    context_result = "context"
    compact_result = "compact"
    inference_result = "inference"
    command_result = "command"
    context_invocation = _RecordingInvocation[str, str](context_result, [])
    compact_invocation = _RecordingInvocation[str, str](compact_result, [])
    inference_invocation = _RecordingInvocation[str, str](inference_result, [])
    command_invocation = _RecordingInvocation[str, str](command_result, [])

    assert await ContextPort[str, str](context_invocation).load_context(request) == context_result
    assert await CompactPort[str, str](compact_invocation).compact(request) == compact_result
    assert await InferencePort[str, str](inference_invocation).infer(request) == inference_result
    assert await CommandPort[str, str](command_invocation).build_command(request) == command_result
    assert context_invocation.calls == [request]
    assert compact_invocation.calls == [request]
    assert inference_invocation.calls == [request]
    assert command_invocation.calls == [request]


@pytest.mark.asyncio
async def test_ports_propagate_invocation_errors() -> None:
    with pytest.raises(RuntimeError):
        await PromptPort[str, str, str, str](
            _RaisingInvocation[str, str](RuntimeError("system")),
            _RecordingInvocation[str, str]("placeholder", []),
            _RecordingInvocation[str, str]("user", []),
        ).load_system_prompt("payload")
    with pytest.raises(RuntimeError):
        await ContextPort[str, str](_RaisingInvocation[str, str](RuntimeError("context"))).load_context("request")
    with pytest.raises(RuntimeError):
        await CompactPort[str, str](_RaisingInvocation[str, str](RuntimeError("compact"))).compact("request")
    with pytest.raises(RuntimeError):
        await InferencePort[str, str](_RaisingInvocation[str, str](RuntimeError("inference"))).infer("request")
    with pytest.raises(RuntimeError):
        await CommandPort[str, str](_RaisingInvocation[str, str](RuntimeError("command"))).build_command("request")


@pytest.mark.asyncio
async def test_ports_propagate_invocation_cancellation() -> None:
    port = ContextPort[str, str](_RaisingInvocation[str, str](asyncio.CancelledError("cancelled")))

    with pytest.raises(asyncio.CancelledError):
        await port.load_context("request")


def test_ports_reject_missing_or_non_callable_invocations_at_assembly() -> None:
    with pytest.raises(ValueError, match="system_invocation"):
        PromptPort[str, str, str, str](
            cast(Never, None),
            _RecordingInvocation[str, str]("placeholder", []),
            _RecordingInvocation[str, str]("user", []),
        )
    with pytest.raises(ValueError, match="system_invocation"):
        PromptPort[str, str, str, str](
            cast(Never, _NonCallableInvocation()),
            _RecordingInvocation[str, str]("placeholder", []),
            _RecordingInvocation[str, str]("user", []),
        )
    with pytest.raises(ValueError, match="placeholder_invocation"):
        PromptPort[str, str, str, str](
            _RecordingInvocation[str, str]("system", []),
            cast(Never, None),
            _RecordingInvocation[str, str]("user", []),
        )
    with pytest.raises(ValueError, match="user_invocation"):
        PromptPort[str, str, str, str](
            _RecordingInvocation[str, str]("system", []),
            _RecordingInvocation[str, str]("placeholder", []),
            cast(Never, None),
        )
    with pytest.raises(ValueError, match=r"ContextPort\.invocation"):
        ContextPort[str, str](cast(Never, None))
    with pytest.raises(ValueError, match=r"CompactPort\.invocation"):
        CompactPort[str, str](cast(Never, None))
    with pytest.raises(ValueError, match=r"InferencePort\.invocation"):
        InferencePort[str, str](cast(Never, None))
    with pytest.raises(ValueError, match=r"CommandPort\.invocation"):
        CommandPort[str, str](cast(Never, None))
