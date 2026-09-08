"""Compact stage callable for the Think graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import CompactBinding
from mote_kernel.think.contract import (
    CompactedContext,
    CompactNodeInput,
    CompactPort,
    CompactRequest,
    CompactStep,
    ThinkContractError,
    ThinkFrame,
    admit_context_frame,
)

HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")


@dataclass(frozen=True, slots=True)
class CompactNode(
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
    ],
):
    """Compact the Context frame selected by the preceding Hook result."""

    compact_port: CompactPort[
        CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
        CompactedContext[CompactedSnapshotT],
    ]

    def __post_init__(self) -> None:
        if operator.is_(self.compact_port, None):
            raise ThinkContractError("compact requires a CompactPort")
        try:
            method = self.compact_port.compact
        except AttributeError as error:
            raise ThinkContractError("compact requires a CompactPort") from error
        if not callable(method):
            raise ThinkContractError("CompactPort.compact must be callable")

    async def __call__(
        self,
        activation: ConfigActivation[
            CompactNodeInput[
                HookStateT,
                SystemPromptT,
                PlaceholderT,
                UserPromptT,
                ContextSnapshotT,
                HookGraphValue,
            ]
        ],
        /,
    ) -> HookActivationRequest[
        ThinkFrame[
            CompactStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT, CompactedSnapshotT],
            HookStateT,
        ],
        HookStateT,
    ]:
        value = activation.value
        frame = admit_context_frame(value.hook_result)
        config = activation.activation_config
        step = frame.step
        request = CompactRequest(step.prompt, step.context)

        compact_port = self.compact_port
        if config is not None:
            compact_port = config.bind(
                CompactBinding[
                    SystemPromptT,
                    PlaceholderT,
                    UserPromptT,
                    ContextSnapshotT,
                    CompactedSnapshotT,
                ]()
            ).port
        compacted_value = await compact_port.compact(request)
        if type(compacted_value) is not CompactedContext:
            raise ThinkContractError("CompactPort.compact must return a CompactedContext")
        compacted = compacted_value
        next_frame = ThinkFrame(CompactStep(step.prompt, step.context, compacted), frame.hook_state)
        return HookActivationRequest(next_frame, frame.hook_state, GraphNodeId("compact"))


__all__ = ["CompactNode"]
