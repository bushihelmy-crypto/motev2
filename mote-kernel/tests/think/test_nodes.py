from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Never, cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookRequest, HookResult
from mote_kernel.think.command import CommandNode
from mote_kernel.think.compact import CompactNode
from mote_kernel.think.context import ContextNode
from mote_kernel.think.contract import (
    CommandStep,
    CompactedContext,
    CompactRequest,
    CompactStep,
    ContextFrame,
    ContextRequest,
    ContextStep,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    ModelBinding,
    PromptFrame,
    PromptStep,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)
from mote_kernel.think.inference import InferenceNode


@dataclass(frozen=True, slots=True)
class Payload(HookGraphValue):
    text: str


@dataclass(frozen=True, slots=True)
class State(HookGraphValue):
    turn: int


PROMPT: PromptFrame[str, str, str] = PromptFrame("system", "placeholder", "user")
REQUEST: ThinkRequest[Payload, State] = ThinkRequest(Payload("payload"), State(1))
CONTEXT: ContextFrame[tuple[str, ...]] = ContextFrame(("history",))
COMPACTED: CompactedContext[tuple[str, ...]] = CompactedContext(("history",), 1)
INFERENCE: InferenceResult[str] = InferenceResult("answer")
CORE: ThinkCoreResult[str] = ThinkCoreResult("command")
MODEL = ModelBinding("provider", "model", 1)


def _result(step: ThinkStep) -> HookResult[ThinkFrame[ThinkStep, State], HookGraphValue]:
    return HookResult(ThinkFrame(step, REQUEST.hook_state), ())


class StagePorts:
    def __init__(self) -> None:
        self.context_requests: list[ContextRequest[Payload, State, str, str, str]] = []
        self.compact_requests: list[CompactRequest[str, str, str, tuple[str, ...]]] = []
        self.inference_requests: list[InferenceRequest[str, str, str, tuple[str, ...]]] = []
        self.command_requests: list[InferenceResult[str]] = []

    async def load_context(
        self,
        request: ContextRequest[Payload, State, str, str, str],
        /,
    ) -> ContextFrame[tuple[str, ...]]:
        self.context_requests.append(request)
        return CONTEXT

    async def compact(
        self,
        request: CompactRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> CompactedContext[tuple[str, ...]]:
        self.compact_requests.append(request)
        return COMPACTED

    async def infer(
        self,
        request: InferenceRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> InferenceResult[str]:
        self.inference_requests.append(request)
        return INFERENCE

    async def build_command(
        self,
        request: InferenceResult[str],
        /,
    ) -> ThinkCoreResult[str]:
        self.command_requests.append(request)
        return CORE


class BadResultPorts(StagePorts):
    async def load_context(
        self,
        request: ContextRequest[Payload, State, str, str, str],
        /,
    ) -> ContextFrame[tuple[str, ...]]:
        del request
        return cast(Never, object())

    async def compact(
        self,
        request: CompactRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> CompactedContext[tuple[str, ...]]:
        del request
        return cast(Never, object())

    async def infer(
        self,
        request: InferenceRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> InferenceResult[str]:
        del request
        return cast(Never, object())

    async def build_command(
        self,
        request: InferenceResult[str],
        /,
    ) -> ThinkCoreResult[str]:
        del request
        return cast(Never, object())


class MissingContextPort:
    pass


class MissingCompactPort:
    pass


class MissingInferencePort:
    pass


class MissingCommandPort:
    pass


class NonCallableContextPort:
    load_context = None


class NonCallableCompactPort:
    compact = None


class NonCallableInferencePort:
    infer = None


class NonCallableCommandPort:
    build_command = None


def _context_node(port: object = StagePorts()) -> ContextNode[Payload, State, str, str, str, tuple[str, ...]]:
    return ContextNode(cast(Never, port))


def _compact_node(port: object = StagePorts()) -> CompactNode[State, str, str, str, tuple[str, ...], tuple[str, ...]]:
    return CompactNode(cast(Never, port))


def _inference_node(port: object = StagePorts()) -> InferenceNode[State, str, str, str, tuple[str, ...], str]:
    return InferenceNode(cast(Never, port), MODEL)


def _command_node(port: object = StagePorts()) -> CommandNode[State, str, str, str, tuple[str, ...], str, str]:
    return CommandNode(cast(Never, port))


@pytest.mark.asyncio
async def test_context_node_builds_one_request_and_next_frame() -> None:
    ports = StagePorts()
    node = _context_node(ports)
    prompt_step = PromptStep(PROMPT)

    output = await node(Graph.values(request=REQUEST, hook_result=_result(prompt_step)))

    hook_request = cast(HookRequest[ThinkFrame[ThinkStep, State], State], output["hook_request"])
    assert hook_request.state is REQUEST.hook_state
    step = cast(ContextStep[str, str, str, tuple[str, ...]], hook_request.value.step)
    assert step.context is CONTEXT
    assert ports.context_requests == [ContextRequest(REQUEST, PROMPT)]


@pytest.mark.asyncio
async def test_compact_node_builds_one_request_and_next_frame() -> None:
    ports = StagePorts()
    node = _compact_node(ports)
    context_step = ContextStep(PROMPT, CONTEXT)

    output = await node(Graph.values(hook_result=_result(context_step)))

    hook_request = cast(HookRequest[ThinkFrame[ThinkStep, State], State], output["hook_request"])
    step = cast(CompactStep[str, str, str, tuple[str, ...], tuple[str, ...]], hook_request.value.step)
    assert step.compacted is COMPACTED
    assert ports.compact_requests == [CompactRequest(PROMPT, CONTEXT)]


@pytest.mark.asyncio
async def test_inference_node_captures_the_exact_model_binding() -> None:
    ports = StagePorts()
    node = _inference_node(ports)
    compact_step = CompactStep(PROMPT, CONTEXT, COMPACTED)

    output = await node(Graph.values(hook_result=_result(compact_step)))

    hook_request = cast(HookRequest[ThinkFrame[ThinkStep, State], State], output["hook_request"])
    step = cast(InferenceStep[str, str, str, tuple[str, ...], str], hook_request.value.step)
    assert step.inference is INFERENCE
    assert len(ports.inference_requests) == 1
    assert ports.inference_requests[0].model is MODEL
    assert ports.inference_requests[0].compacted is COMPACTED
    assert ports.inference_requests[0].prompt is PROMPT


@pytest.mark.asyncio
async def test_command_node_only_structures_the_inference_result() -> None:
    ports = StagePorts()
    node = _command_node(ports)
    inference_step = InferenceStep(PROMPT, COMPACTED, INFERENCE)

    output = await node(Graph.values(hook_result=_result(inference_step)))

    hook_request = cast(HookRequest[ThinkFrame[ThinkStep, State], State], output["hook_request"])
    assert type(hook_request.value.step) is not InferenceStep
    assert type(hook_request.value.step) is not PromptStep
    step = cast(CommandStep[str, str, str, tuple[str, ...], str, str], hook_request.value.step)
    assert step.core is CORE
    assert ports.command_requests == [INFERENCE]


def _bad_context_none() -> object:
    return ContextNode[Payload, State, str, str, str, tuple[str, ...]](cast(Never, None))


def _bad_context_missing() -> object:
    return ContextNode[Payload, State, str, str, str, tuple[str, ...]](cast(Never, MissingContextPort()))


def _bad_context_non_callable() -> object:
    return ContextNode[Payload, State, str, str, str, tuple[str, ...]](cast(Never, NonCallableContextPort()))


def _bad_compact_none() -> object:
    return CompactNode[State, str, str, str, tuple[str, ...], tuple[str, ...]](cast(Never, None))


def _bad_compact_missing() -> object:
    return CompactNode[State, str, str, str, tuple[str, ...], tuple[str, ...]](cast(Never, MissingCompactPort()))


def _bad_compact_non_callable() -> object:
    return CompactNode[State, str, str, str, tuple[str, ...], tuple[str, ...]](cast(Never, NonCallableCompactPort()))


def _bad_inference_none() -> object:
    return InferenceNode[State, str, str, str, tuple[str, ...], str](cast(Never, None), MODEL)


def _bad_inference_missing() -> object:
    return InferenceNode[State, str, str, str, tuple[str, ...], str](cast(Never, MissingInferencePort()), MODEL)


def _bad_inference_non_callable() -> object:
    return InferenceNode[State, str, str, str, tuple[str, ...], str](cast(Never, NonCallableInferencePort()), MODEL)


def _bad_inference_model() -> object:
    return InferenceNode[State, str, str, str, tuple[str, ...], str](cast(Never, StagePorts()), cast(Never, object()))


def _bad_command_none() -> object:
    return CommandNode[State, str, str, str, tuple[str, ...], str, str](cast(Never, None))


def _bad_command_missing() -> object:
    return CommandNode[State, str, str, str, tuple[str, ...], str, str](cast(Never, MissingCommandPort()))


def _bad_command_non_callable() -> object:
    return CommandNode[State, str, str, str, tuple[str, ...], str, str](cast(Never, NonCallableCommandPort()))


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (_bad_context_none, "context requires"),
        (_bad_context_missing, "context requires"),
        (_bad_context_non_callable, "load_context"),
        (_bad_compact_none, "compact requires"),
        (_bad_compact_missing, "compact requires"),
        (_bad_compact_non_callable, "compact"),
        (_bad_inference_none, "inference requires"),
        (_bad_inference_missing, "inference requires"),
        (_bad_inference_non_callable, "infer"),
        (_bad_inference_model, "exact ModelBinding"),
        (_bad_command_none, "command requires"),
        (_bad_command_missing, "command requires"),
        (_bad_command_non_callable, "build_command"),
    ],
)
def test_non_prompt_stage_capabilities_are_admitted_at_assembly(factory: Callable[[], object], message: str) -> None:
    with pytest.raises(ThinkContractError, match=message):
        factory()


def _context_factory() -> object:
    return _context_node()


def _compact_factory() -> object:
    return _compact_node()


def _inference_factory() -> object:
    return _inference_node()


def _command_factory() -> object:
    return _command_node()


def _compact_with_port(port: object) -> object:
    return _compact_node(port)


def _inference_with_port(port: object) -> object:
    return _inference_node(port)


def _command_with_port(port: object) -> object:
    return _command_node(port)


def _bad_context_values() -> Graph.Values[HookGraphValue]:
    return Graph.values(request=REQUEST, hook_result=cast(HookGraphValue, object()))


def _bad_later_values() -> Graph.Values[HookGraphValue]:
    return Graph.values(hook_result=cast(HookGraphValue, object()))


NodeOperation = Callable[[Graph.Values[HookGraphValue]], Awaitable[Graph.Values[HookGraphValue]]]


def _operation(node: object) -> NodeOperation:
    return cast(NodeOperation, node)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_factory", "values"),
    [
        (_context_factory, _bad_context_values),
        (_compact_factory, _bad_later_values),
        (_inference_factory, _bad_later_values),
        (_command_factory, _bad_later_values),
    ],
)
async def test_non_prompt_stage_nodes_reject_non_hook_results(
    node_factory: Callable[[], object],
    values: Callable[[], Graph.Values[HookGraphValue]],
) -> None:
    with pytest.raises(ThinkContractError, match="input must be a HookResult"):
        await _operation(node_factory())(values())


@pytest.mark.asyncio
async def test_context_node_rejects_wrong_request_frame_and_state() -> None:
    node = _context_node()
    prompt_result = _result(PromptStep(PROMPT))

    with pytest.raises(ThinkContractError, match="ThinkRequest"):
        await node(Graph.values(request=cast(Never, object()), hook_result=prompt_result))
    with pytest.raises(ThinkContractError, match="ThinkFrame"):
        await node(Graph.values(request=REQUEST, hook_result=HookResult(cast(Never, object()), ())))
    wrong_state = ThinkFrame(PromptStep(PROMPT), State(2))
    with pytest.raises(ThinkContractError, match="state does not match"):
        await node(Graph.values(request=REQUEST, hook_result=HookResult(wrong_state, ())))
    with pytest.raises(ThinkContractError, match="PromptStep"):
        await node(Graph.values(request=REQUEST, hook_result=_result(ContextStep(PROMPT, CONTEXT))))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_factory", "step", "message"),
    [
        (_compact_factory, PromptStep(PROMPT), "ContextStep"),
        (_inference_factory, ContextStep(PROMPT, CONTEXT), "CompactStep"),
        (_command_factory, CompactStep(PROMPT, CONTEXT, COMPACTED), "InferenceStep"),
    ],
)
async def test_later_stage_nodes_reject_wrong_step(
    node_factory: Callable[[], object],
    step: ThinkStep,
    message: str,
) -> None:
    with pytest.raises(ThinkContractError, match=message):
        await _operation(node_factory())(Graph.values(hook_result=_result(step)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_factory", "message"),
    [
        (_compact_factory, "ThinkFrame"),
        (_inference_factory, "ThinkFrame"),
        (_command_factory, "ThinkFrame"),
    ],
)
async def test_later_stage_nodes_reject_a_hook_result_without_a_frame(
    node_factory: Callable[[], object],
    message: str,
) -> None:
    malformed = HookResult(cast(Never, object()), ())
    with pytest.raises(ThinkContractError, match=message):
        await _operation(node_factory())(Graph.values(hook_result=malformed))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_factory", "step", "message"),
    [
        (_compact_with_port, ContextStep(PROMPT, CONTEXT), "CompactedContext"),
        (_inference_with_port, CompactStep(PROMPT, CONTEXT, COMPACTED), "InferenceResult"),
        (_command_with_port, InferenceStep(PROMPT, COMPACTED, INFERENCE), "ThinkCoreResult"),
    ],
)
async def test_later_stage_nodes_reject_wrong_port_results(
    node_factory: Callable[[object], object],
    step: ThinkStep,
    message: str,
) -> None:
    ports = BadResultPorts()
    with pytest.raises(ThinkContractError, match=message):
        await _operation(node_factory(ports))(Graph.values(hook_result=_result(step)))


@pytest.mark.asyncio
async def test_context_node_rejects_wrong_port_result() -> None:
    with pytest.raises(ThinkContractError, match="ContextFrame"):
        await _context_node(BadResultPorts())(Graph.values(request=REQUEST, hook_result=_result(PromptStep(PROMPT))))


@pytest.mark.asyncio
async def test_stage_cancellation_is_not_converted_to_a_value() -> None:
    class CancellingInferencePort(StagePorts):
        async def infer(
            self,
            request: InferenceRequest[str, str, str, tuple[str, ...]],
            /,
        ) -> InferenceResult[str]:
            del request
            raise asyncio.CancelledError

    node = _inference_node(CancellingInferencePort())
    with pytest.raises(asyncio.CancelledError):
        await node(Graph.values(hook_result=_result(CompactStep(PROMPT, CONTEXT, COMPACTED))))


@pytest.mark.parametrize("token_count", [-1, True, 1.5])
def test_compacted_context_requires_a_non_negative_exact_integer(token_count: object) -> None:
    with pytest.raises(ThinkContractError, match="token_count"):
        CompactedContext(cast(Never, ("history",)), cast(Never, token_count))


@pytest.mark.parametrize(
    ("provider_id", "model_id", "revision", "message"),
    [
        ("", "model", 1, "provider_id"),
        (" provider", "model", 1, "provider_id"),
        ("provider", "", 1, "model_id"),
        ("provider", "model ", 1, "model_id"),
        ("provider", "model", 0, "revision"),
        ("provider", "model", True, "revision"),
    ],
)
def test_model_binding_rejects_non_canonical_identity_or_revision(
    provider_id: object,
    model_id: object,
    revision: object,
    message: str,
) -> None:
    with pytest.raises(ThinkContractError, match=message):
        ModelBinding(cast(Never, provider_id), cast(Never, model_id), cast(Never, revision))
