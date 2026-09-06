"""Compact stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.hooks.contract import HookGraphValue, HookRequest
from mote_kernel.state.graph_state import GraphNodeId
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

HookStateT = TypeVar("HookStateT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")


MethodT = TypeVar("MethodT")


def _require_port_method(method: MethodT, name: str, /) -> MethodT:
    if not callable(method):
        raise ThinkContractError(f"CompactPort.{name} must be callable")
    return method


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

    compact_port: (
        CompactPort[
            CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
            CompactedContext[CompactedSnapshotT],
        ]
        | None
    )

    def __post_init__(self) -> None:
        if self.compact_port is None:
            raise ThinkContractError("compact requires a CompactPort")
        try:
            method = self.compact_port.compact
        except AttributeError as error:
            raise ThinkContractError("compact requires a CompactPort") from error
        _require_port_method(method, "compact")

    async def __call__(
        self,
        value: CompactNodeInput[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            HookGraphValue,
        ],
        /,
    ) -> HookRequest[
        ThinkFrame[
            CompactStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT, CompactedSnapshotT],
            HookStateT,
        ],
        HookStateT,
    ]:
        frame = admit_context_frame(value.hook_result)
        step = frame.step
        request = CompactRequest(step.prompt, step.context)

        port = self.compact_port
        if port is None:
            raise ThinkContractError("compact requires a CompactPort")
        compacted_value = await port.compact(request)
        if type(compacted_value) is not CompactedContext:
            raise ThinkContractError("CompactPort.compact must return a CompactedContext")
        compacted = compacted_value
        next_frame = ThinkFrame(CompactStep(step.prompt, step.context, compacted), frame.hook_state)
        return HookRequest(next_frame, frame.hook_state, GraphNodeId("compact"))


__all__ = ["CompactNode"]
