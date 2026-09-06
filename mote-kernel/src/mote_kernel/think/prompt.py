"""Prompt stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookRequest
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.contract import (
    PromptFrame,
    PromptPort,
    PromptStep,
    ThinkContractError,
    ThinkFrame,
    ThinkRequest,
)

PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")


@dataclass(frozen=True, slots=True)
class PromptNode(
    Generic[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
):
    """Call one PromptPort's three operations and hand the frame to shared Hook."""

    prompt_port: PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT] | None

    def __post_init__(self) -> None:
        port = self.prompt_port
        if port is None:
            raise ThinkContractError("prompt requires a PromptPort")
        try:
            load_system_prompt = port.load_system_prompt
            load_placeholder = port.load_placeholder
            load_user_prompt = port.load_user_prompt
        except AttributeError as error:
            raise ThinkContractError("prompt requires a PromptPort") from error
        if not callable(load_system_prompt):
            raise ThinkContractError("PromptPort.load_system_prompt must be callable")
        if not callable(load_placeholder):
            raise ThinkContractError("PromptPort.load_placeholder must be callable")
        if not callable(load_user_prompt):
            raise ThinkContractError("PromptPort.load_user_prompt must be callable")

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        request_value = values["request"]
        if type(request_value) is not ThinkRequest:
            raise ThinkContractError("prompt input must be a ThinkRequest")
        request = cast(ThinkRequest[PayloadT, HookStateT], request_value)

        prompt_port = cast(
            PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT],
            self.prompt_port,
        )
        system = await prompt_port.load_system_prompt(request.payload)
        placeholder = await prompt_port.load_placeholder(request.payload)
        user = await prompt_port.load_user_prompt(request.payload)

        prompt = PromptFrame[SystemPromptT, PlaceholderT, UserPromptT](system, placeholder, user)
        frame = ThinkFrame(PromptStep(prompt), request.hook_state)
        return Graph.values(hook_request=HookRequest(frame, request.hook_state, GraphNodeId("prompt")))


__all__ = ["PromptNode"]
