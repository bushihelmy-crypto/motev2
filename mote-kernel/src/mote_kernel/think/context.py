"""Context stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.hooks.contract import HookGraphValue, HookRequest
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.contract import (
    ContextFrame,
    ContextNodeInput,
    ContextPort,
    ContextRequest,
    ContextStep,
    ThinkContractError,
    ThinkFrame,
    admit_prompt_frame,
)

PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")


MethodT = TypeVar("MethodT")


def _require_port_method(method: MethodT, name: str, /) -> MethodT:
    if not callable(method):
        raise ThinkContractError(f"ContextPort.{name} must be callable")
    return method


@dataclass(frozen=True, slots=True)
class ContextNode(
    Generic[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
):
    """Load one context snapshot after the Prompt Hook activation."""

    context_port: (
        ContextPort[
            ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
            ContextFrame[ContextSnapshotT],
        ]
        | None
    )

    def __post_init__(self) -> None:
        if self.context_port is None:
            raise ThinkContractError("context requires a ContextPort")
        try:
            method = self.context_port.load_context
        except AttributeError as error:
            raise ThinkContractError("context requires a ContextPort") from error
        _require_port_method(method, "load_context")

    async def __call__(
        self,
        value: ContextNodeInput[
            PayloadT,
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            HookGraphValue,
        ],
        /,
    ) -> HookRequest[
        ThinkFrame[ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT], HookStateT],
        HookStateT,
    ]:
        request = value.request
        frame = admit_prompt_frame(value.hook_result, request.hook_state)
        prompt = frame.step.prompt
        context_request = ContextRequest(request, prompt)

        port = self.context_port
        if port is None:
            raise ThinkContractError("context requires a ContextPort")
        context_value = await port.load_context(context_request)
        if type(context_value) is not ContextFrame:
            raise ThinkContractError("ContextPort.load_context must return a ContextFrame")
        context = context_value
        next_frame = ThinkFrame(ContextStep(prompt, context), frame.hook_state)
        return HookRequest(next_frame, frame.hook_state, GraphNodeId("context"))


__all__ = ["ContextNode"]
