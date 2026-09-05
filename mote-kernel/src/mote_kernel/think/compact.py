"""Compact stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookRequest, HookResult
from mote_kernel.think.contract import (
    CompactedContext,
    CompactPort,
    CompactRequest,
    CompactStep,
    ContextStep,
    ThinkContractError,
    ThinkFrame,
    ThinkStep,
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
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        hook_value = values["hook_result"]
        if type(hook_value) is not HookResult:
            raise ThinkContractError("compact input must be a HookResult")
        hook_result = cast(HookResult[ThinkFrame[ThinkStep, HookStateT], HookGraphValue], hook_value)
        frame_value = hook_result.value
        if type(frame_value) is not ThinkFrame:
            raise ThinkContractError("compact HookResult must contain a ThinkFrame")
        frame = frame_value
        raw_step = frame.step
        if type(raw_step) is not ContextStep:
            raise ThinkContractError("compact requires a ContextStep")
        step = cast(ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT], raw_step)
        request = CompactRequest(step.prompt, step.context)

        port = cast(
            CompactPort[
                CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
                CompactedContext[CompactedSnapshotT],
            ],
            self.compact_port,
        )
        compacted_value = await port.compact(request)
        if type(compacted_value) is not CompactedContext:
            raise ThinkContractError("CompactPort.compact must return a CompactedContext")
        compacted = compacted_value
        next_frame = ThinkFrame(CompactStep(step.prompt, step.context, compacted), frame.hook_state)
        return Graph.values(hook_request=HookRequest(next_frame, frame.hook_state))


__all__ = ["CompactNode"]
