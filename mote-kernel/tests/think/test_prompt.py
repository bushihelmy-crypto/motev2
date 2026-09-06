from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Never, cast

import pytest

import mote_kernel.think as think_package
import mote_kernel.think.command as command_package
import mote_kernel.think.compact as compact_package
import mote_kernel.think.context as context_package
import mote_kernel.think.inference as inference_package
import mote_kernel.think.node as think_assembly_package
import mote_kernel.think.prompt as prompt_package
from mote_kernel.hooks.contract import HookRequest
from mote_kernel.think import ThinkNode
from mote_kernel.think.command import CommandNode
from mote_kernel.think.compact import CompactNode
from mote_kernel.think.context import ContextNode
from mote_kernel.think.contract import (
    PromptFrame,
    PromptStep,
    ThinkContractError,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)
from mote_kernel.think.inference import InferenceNode
from mote_kernel.think.prompt import PromptNode


@dataclass(frozen=True, slots=True)
class Payload:
    text: str


@dataclass(frozen=True, slots=True)
class HookState:
    turn: int


class RecordingPromptPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Payload]] = []

    async def load_system_prompt(self, payload: Payload, /) -> str:
        self.calls.append(("system", payload))
        return "system prompt"

    async def load_placeholder(self, payload: Payload, /) -> str:
        self.calls.append(("placeholder", payload))
        return "placeholder content"

    async def load_user_prompt(self, payload: Payload, /) -> str:
        self.calls.append(("user", payload))
        return payload.text


def _node(port: RecordingPromptPort) -> PromptNode[Payload, HookState, str, str, str]:
    return PromptNode(port)


def test_flattened_stage_modules_and_top_level_expose_only_graph_nodes() -> None:
    assert think_package.__all__ == ["ThinkNode"]
    assert think_package.ThinkNode is ThinkNode
    assert think_assembly_package.__all__ == ["ThinkNode"]
    assert think_assembly_package.ThinkNode is ThinkNode
    assert prompt_package.__all__ == ["PromptNode"]
    assert prompt_package.PromptNode is PromptNode
    assert command_package.__all__ == ["CommandNode"]
    assert command_package.CommandNode is CommandNode
    assert compact_package.__all__ == ["CompactNode"]
    assert compact_package.CompactNode is CompactNode
    assert context_package.__all__ == ["ContextNode"]
    assert context_package.ContextNode is ContextNode
    assert inference_package.__all__ == ["InferenceNode"]
    assert inference_package.InferenceNode is InferenceNode


@pytest.mark.asyncio
async def test_prompt_loads_system_placeholder_and_user_in_order_once() -> None:
    port = RecordingPromptPort()
    payload = Payload("user text")
    state = HookState(3)
    request = ThinkRequest(payload, state)

    output = await _node(port)(request)

    assert type(output) is HookRequest
    assert port.calls == [
        ("system", payload),
        ("placeholder", payload),
        ("user", payload),
    ]
    hook_request = output
    assert hook_request.state is state
    assert type(hook_request.value) is ThinkFrame
    frame = hook_request.value
    assert frame.hook_state is state
    assert type(frame.step) is PromptStep
    assert frame.step.prompt == PromptFrame("system prompt", "placeholder content", "user text")


@pytest.mark.asyncio
async def test_prompt_passes_the_exact_payload_to_each_port_operation() -> None:
    port = RecordingPromptPort()
    payload = Payload("same object")

    await _node(port)(ThinkRequest(payload, HookState(1)))

    assert all(seen_payload is payload for _operation, seen_payload in port.calls)


class _FailingPromptPort(RecordingPromptPort):
    async def load_placeholder(self, payload: Payload, /) -> str:
        self.calls.append(("placeholder", payload))
        raise RuntimeError("placeholder unavailable")


@pytest.mark.asyncio
async def test_prompt_does_not_call_later_port_operations_after_an_error() -> None:
    port = _FailingPromptPort()

    with pytest.raises(RuntimeError, match="placeholder unavailable"):
        await _node(port)(ThinkRequest(Payload("text"), HookState(1)))

    assert [operation for operation, _payload in port.calls] == ["system", "placeholder"]


class _CancellingPromptPort:
    async def load_system_prompt(self, _payload: Payload, /) -> str:
        raise asyncio.CancelledError

    async def load_placeholder(self, _payload: Payload, /) -> str:
        raise AssertionError("placeholder operation must not run")

    async def load_user_prompt(self, _payload: Payload, /) -> str:
        raise AssertionError("user operation must not run")


class _NonCallablePromptPort:
    load_system_prompt = None
    load_placeholder = None
    load_user_prompt = None


@pytest.mark.parametrize("method", ("load_system_prompt", "load_placeholder", "load_user_prompt"))
def test_prompt_rejects_each_non_callable_port_method_at_assembly(method: str) -> None:
    port = RecordingPromptPort()
    setattr(port, method, None)

    with pytest.raises(ThinkContractError, match=method):
        PromptNode(cast(Never, port))


@pytest.mark.asyncio
async def test_prompt_propagates_cancellation_without_running_later_port_operations() -> None:
    node: PromptNode[Payload, HookState, str, str, str] = PromptNode(
        _CancellingPromptPort(),
    )

    with pytest.raises(asyncio.CancelledError):
        await node(ThinkRequest(Payload("text"), HookState(1)))


def test_prompt_rejects_a_port_with_non_callable_methods_at_assembly() -> None:
    port = _NonCallablePromptPort()

    with pytest.raises(ThinkContractError, match="PromptPort"):
        PromptNode(cast(Never, port))


def test_prompt_rejects_missing_port_before_activation() -> None:
    with pytest.raises(ThinkContractError, match="PromptPort"):
        PromptNode(cast(Never, None))


def test_prompt_rejects_a_port_with_a_missing_method_at_assembly() -> None:
    class MissingPlaceholderPort:
        async def load_system_prompt(self, _payload: Payload, /) -> str:
            return "system"

        async def load_user_prompt(self, _payload: Payload, /) -> str:
            return "user"

    with pytest.raises(ThinkContractError, match="PromptPort"):
        PromptNode(cast(Never, MissingPlaceholderPort()))


@pytest.mark.asyncio
async def test_prompt_rejects_a_forged_missing_port_before_operations() -> None:
    node = cast(
        PromptNode[Payload, HookState, str, str, str],
        object.__new__(PromptNode),
    )
    object.__setattr__(node, "prompt_port", None)

    with pytest.raises(ThinkContractError, match="PromptPort"):
        await node(ThinkRequest(Payload("text"), HookState(1)))


@pytest.mark.parametrize(
    "field",
    ("system", "placeholder", "user"),
)
def test_prompt_frame_requires_all_three_components(field: str) -> None:
    with pytest.raises(ThinkContractError, match="required"):
        if field == "system":
            PromptFrame(None, "placeholder", "user")
        elif field == "placeholder":
            PromptFrame("system", None, "user")
        else:
            PromptFrame("system", "placeholder", None)


def test_prompt_step_requires_a_prompt_frame() -> None:
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        PromptStep(cast(Never, object()))


def test_think_frame_requires_a_think_step() -> None:
    with pytest.raises(ThinkContractError, match="ThinkStep"):
        ThinkFrame(cast(Never, object()), HookState(1))


def test_think_frame_rejects_a_consumer_defined_think_step_subclass() -> None:
    class FakeStep(ThinkStep):
        __slots__ = ()

    with pytest.raises(ThinkContractError, match="ThinkStep"):
        ThinkFrame(FakeStep(), HookState(1))
