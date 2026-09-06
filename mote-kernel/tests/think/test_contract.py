"""Boundary and value tests for the Think-owned immutable envelopes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from typing import Never, cast

import pytest

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
    ThinkRoute,
    ThinkStep,
)


@dataclass(frozen=True, slots=True)
class Payload:
    value: str


@dataclass(frozen=True, slots=True)
class HookState:
    turn: int


PROMPT = PromptFrame("system", "placeholder", "user")
CONTEXT = ContextFrame(("history",))
COMPACTED = CompactedContext(("history",), 0)
MODEL = ModelBinding("provider", "model", 1)
INFERENCE = InferenceResult("answer")
CORE = ThinkCoreResult("command")
REQUEST = ThinkRequest(Payload("payload"), HookState(1))


def _request_payload_none() -> object:
    return ThinkRequest(cast(Never, None), HookState(1))


def _request_state_none() -> object:
    return ThinkRequest(Payload("payload"), cast(Never, None))


def _prompt_system_none() -> object:
    return PromptFrame(cast(Never, None), "placeholder", "user")


def _prompt_placeholder_none() -> object:
    return PromptFrame("system", cast(Never, None), "user")


def _prompt_user_none() -> object:
    return PromptFrame("system", "placeholder", cast(Never, None))


def _context_none() -> object:
    return ContextFrame(cast(Never, None))


def _compacted_none() -> object:
    return CompactedContext(cast(Never, None), 0)


def _inference_none() -> object:
    return InferenceResult(cast(Never, None))


def _core_none() -> object:
    return ThinkCoreResult(cast(Never, None))


def _frame_state_none() -> object:
    return ThinkFrame(PromptStep(PROMPT), cast(Never, None))


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (_request_payload_none, "think request payload"),
        (_request_state_none, "think request hook_state"),
        (_prompt_system_none, "prompt system value"),
        (_prompt_placeholder_none, "prompt placeholder value"),
        (_prompt_user_none, "prompt user value"),
        (_context_none, "context snapshot"),
        (_compacted_none, "compacted context snapshot"),
        (_inference_none, "inference output"),
        (_core_none, "think core command"),
        (_frame_state_none, "think frame hook_state"),
    ],
)
def test_required_envelope_fields_reject_none(factory: Callable[[], object], field: str) -> None:
    with pytest.raises(ThinkContractError, match=field):
        factory()


def test_envelopes_allow_falsy_but_present_payloads() -> None:
    assert PromptFrame("", "", "") == PromptFrame("", "", "")
    assert ContextFrame(()) == ContextFrame(())
    assert CompactedContext((), 0) == CompactedContext((), 0)
    assert InferenceResult(False) == InferenceResult(False)
    assert ThinkCoreResult(0) == ThinkCoreResult(0)


def test_compacted_context_accepts_zero_and_rejects_only_negative_or_non_exact_int() -> None:
    assert CompactedContext(("history",), 0).token_count == 0
    for invalid in (-1, True, 1.0):
        with pytest.raises(ThinkContractError, match="token_count"):
            CompactedContext(("history",), cast(Never, invalid))


def test_exact_request_envelopes_reject_wrong_member_wrappers() -> None:
    with pytest.raises(ThinkContractError, match="ThinkRequest"):
        ContextRequest(cast(Never, object()), PROMPT)
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        ContextRequest(REQUEST, cast(Never, object()))
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        CompactRequest(cast(Never, object()), CONTEXT)
    with pytest.raises(ThinkContractError, match="ContextFrame"):
        CompactRequest(PROMPT, cast(Never, object()))
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        InferenceRequest(cast(Never, object()), COMPACTED, MODEL)
    with pytest.raises(ThinkContractError, match="CompactedContext"):
        InferenceRequest(PROMPT, cast(Never, object()), MODEL)
    with pytest.raises(ThinkContractError, match="ModelBinding"):
        InferenceRequest(PROMPT, COMPACTED, cast(Never, object()))


def test_exact_step_envelopes_reject_wrong_member_wrappers() -> None:
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        PromptStep(cast(Never, object()))
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        ContextStep(cast(Never, object()), CONTEXT)
    with pytest.raises(ThinkContractError, match="ContextFrame"):
        ContextStep(PROMPT, cast(Never, object()))
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        CompactStep(cast(Never, object()), CONTEXT, COMPACTED)
    with pytest.raises(ThinkContractError, match="ContextFrame"):
        CompactStep(PROMPT, cast(Never, object()), COMPACTED)
    with pytest.raises(ThinkContractError, match="CompactedContext"):
        CompactStep(PROMPT, CONTEXT, cast(Never, object()))
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        InferenceStep(cast(Never, object()), COMPACTED, INFERENCE)
    with pytest.raises(ThinkContractError, match="CompactedContext"):
        InferenceStep(PROMPT, cast(Never, object()), INFERENCE)
    with pytest.raises(ThinkContractError, match="InferenceResult"):
        InferenceStep(PROMPT, COMPACTED, cast(Never, object()))
    with pytest.raises(ThinkContractError, match="PromptFrame"):
        CommandStep(cast(Never, object()), COMPACTED, INFERENCE, CORE)
    with pytest.raises(ThinkContractError, match="CompactedContext"):
        CommandStep(PROMPT, cast(Never, object()), INFERENCE, CORE)
    with pytest.raises(ThinkContractError, match="InferenceResult"):
        CommandStep(PROMPT, COMPACTED, cast(Never, object()), CORE)
    with pytest.raises(ThinkContractError, match="ThinkCoreResult"):
        CommandStep(PROMPT, COMPACTED, INFERENCE, cast(Never, object()))


@pytest.mark.parametrize(
    "step",
    [
        PromptStep(PROMPT),
        ContextStep(PROMPT, CONTEXT),
        CompactStep(PROMPT, CONTEXT, COMPACTED),
        InferenceStep(PROMPT, COMPACTED, INFERENCE),
        CommandStep(PROMPT, COMPACTED, INFERENCE, CORE),
    ],
)
def test_frame_accepts_each_closed_stage_variant(step: ThinkStep) -> None:
    frame = ThinkFrame(step, REQUEST.hook_state)
    assert frame.step is step
    assert frame.hook_state is REQUEST.hook_state


def test_frame_rejects_an_arbitrary_consumer_step_even_when_it_has_the_right_base() -> None:
    class ConsumerStep(ThinkStep):
        __slots__ = ()

    with pytest.raises(ThinkContractError, match="known ThinkStep"):
        ThinkFrame(ConsumerStep(), REQUEST.hook_state)


def test_think_route_is_a_closed_string_enum_for_diagnostics() -> None:
    assert tuple(route.value for route in ThinkRoute) == (
        "context",
        "compact",
        "inference",
        "command",
        "finish",
    )
    assert ThinkRoute.CONTEXT == "context"


def test_envelopes_are_frozen_and_slot_based() -> None:
    values = (
        REQUEST,
        PROMPT,
        CONTEXT,
        COMPACTED,
        MODEL,
        INFERENCE,
        CORE,
        ContextRequest(REQUEST, PROMPT),
        CompactRequest(PROMPT, CONTEXT),
        InferenceRequest(PROMPT, COMPACTED, MODEL),
        PromptStep(PROMPT),
        ContextStep(PROMPT, CONTEXT),
        CompactStep(PROMPT, CONTEXT, COMPACTED),
        InferenceStep(PROMPT, COMPACTED, INFERENCE),
        CommandStep(PROMPT, COMPACTED, INFERENCE, CORE),
        ThinkFrame(PromptStep(PROMPT), REQUEST.hook_state),
    )
    for value in values:
        assert "__dict__" not in type(value).__slots__
    with pytest.raises(FrozenInstanceError):
        REQUEST.payload = cast(Never, Payload("replacement"))  # type: ignore[misc]
