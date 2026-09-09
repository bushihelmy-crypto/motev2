"""Context stage callable for the Think graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.failover.contract import TypedPortDecorator
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import ContextBinding
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
from mote_kernel.think.failover import (
    FailoverPortDecorator,
    apply_think_port_decorator,
)
from mote_kernel.think.identity import ThinkNodeId

PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
HookCommandT = TypeVar("HookCommandT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")


@dataclass(frozen=True, slots=True)
class ContextNode(
    Generic[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
):
    """Load one context snapshot after the Prompt Hook activation."""

    context_port: ContextPort[
        ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
        ContextFrame[ContextSnapshotT],
    ]
    failover: (
        TypedPortDecorator[
            ContextPort[
                ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
                ContextFrame[ContextSnapshotT],
            ]
        ]
        | FailoverPortDecorator
        | None
    ) = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        port = apply_think_port_decorator(self.context_port, self.failover, ContextPort, "ContextPort")
        object.__setattr__(self, "context_port", port)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ThinkContractError("context assembly snapshot key is malformed")
        if operator.is_(port, None):
            raise ThinkContractError("context requires a ContextPort")
        try:
            method = port.load_context
        except AttributeError as error:
            raise ThinkContractError("context requires a ContextPort") from error
        if not callable(method):
            raise ThinkContractError("ContextPort.load_context must be callable")

    async def __call__(
        self,
        activation: ConfigActivation[
            ContextNodeInput[
                PayloadT,
                HookStateT,
                SystemPromptT,
                PlaceholderT,
                UserPromptT,
                HookCommandT,
            ]
        ],
        /,
    ) -> HookActivationRequest[
        ThinkFrame[ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT], HookStateT],
        HookStateT,
    ]:
        value = activation.value
        request = value.request
        frame = admit_prompt_frame(value.hook_result, request.hook_state)
        config = activation.activation_config
        prompt = frame.step.prompt
        context_request = ContextRequest(request, prompt)

        context_port = self.context_port
        if config is not None:
            selected = config.bind(
                ContextBinding[
                    PayloadT,
                    HookStateT,
                    SystemPromptT,
                    PlaceholderT,
                    UserPromptT,
                    ContextSnapshotT,
                ]()
            )
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                context_port = apply_think_port_decorator(selected.port, self.failover, ContextPort, "ContextPort")
        context_value = await context_port.load_context(context_request)
        if type(context_value) is not ContextFrame:
            raise ThinkContractError("ContextPort.load_context must return a ContextFrame")
        context = context_value
        next_frame = ThinkFrame(ContextStep(prompt, context), frame.hook_state)
        return HookActivationRequest(
            next_frame,
            frame.hook_state,
            GraphNodeId(str(ThinkNodeId.CONTEXT)),
        )


__all__ = ["ContextNode"]
