"""Prompt stage callable for the Think graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import PromptBinding
from mote_kernel.think.contract import (
    PromptFrame,
    PromptPort,
    PromptStep,
    ThinkContractError,
    ThinkFrame,
    ThinkRequest,
)
from mote_kernel.think.identity import ThinkNodeId

PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")


@dataclass(frozen=True, slots=True)
class PromptNode(
    Generic[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
):
    """Call one PromptPort's three operations and hand the frame to shared Hook."""

    prompt_port: PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]

    def __post_init__(self) -> None:
        port = self.prompt_port
        if operator.is_(port, None):
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
        activation: ConfigActivation[ThinkRequest[PayloadT, HookStateT]],
        /,
    ) -> HookActivationRequest[
        ThinkFrame[PromptStep[SystemPromptT, PlaceholderT, UserPromptT], HookStateT],
        HookStateT,
    ]:
        request = activation.value
        config = activation.activation_config
        port = self.prompt_port
        if config is not None:
            port = config.bind(PromptBinding[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]()).port
        system = await port.load_system_prompt(request.payload)
        placeholder = await port.load_placeholder(request.payload)
        user = await port.load_user_prompt(request.payload)

        prompt = PromptFrame[SystemPromptT, PlaceholderT, UserPromptT](system, placeholder, user)
        frame = ThinkFrame(PromptStep(prompt), request.hook_state)
        return HookActivationRequest(
            frame,
            request.hook_state,
            GraphNodeId(str(ThinkNodeId.PROMPT)),
        )


__all__ = ["PromptNode"]
