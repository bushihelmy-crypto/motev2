"""Command stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookRequest, HookResult
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.contract import (
    CommandPort,
    CommandStep,
    InferenceResult,
    InferenceStep,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    ThinkStep,
)

HookStateT = TypeVar("HookStateT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")
CommandT = TypeVar("CommandT")


MethodT = TypeVar("MethodT")


def _require_port_method(method: MethodT, name: str, /) -> MethodT:
    if not callable(method):
        raise ThinkContractError(f"CommandPort.{name} must be callable")
    return method


@dataclass(frozen=True, slots=True)
class CommandNode(
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        CompactedSnapshotT,
        ModelOutputT,
        CommandT,
    ],
):
    """Structure the normalized inference result without executing it."""

    command_port: CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]] | None

    def __post_init__(self) -> None:
        if self.command_port is None:
            raise ThinkContractError("command requires a CommandPort")
        try:
            method = self.command_port.build_command
        except AttributeError as error:
            raise ThinkContractError("command requires a CommandPort") from error
        _require_port_method(method, "build_command")

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        hook_value = values["hook_result"]
        if type(hook_value) is not HookResult:
            raise ThinkContractError("command input must be a HookResult")
        hook_result = cast(HookResult[ThinkFrame[ThinkStep, HookStateT], HookGraphValue], hook_value)
        frame_value = hook_result.value
        if type(frame_value) is not ThinkFrame:
            raise ThinkContractError("command HookResult must contain a ThinkFrame")
        frame = frame_value
        raw_step = frame.step
        if type(raw_step) is not InferenceStep:
            raise ThinkContractError("command requires an InferenceStep")
        if hook_result.node_id is not None and hook_result.node_id != GraphNodeId("inference"):
            raise ThinkContractError("command requires a HookResult produced by inference")
        step = cast(
            InferenceStep[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT],
            raw_step,
        )
        port = cast(CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]], self.command_port)
        core_value = await port.build_command(step.inference)
        if type(core_value) is not ThinkCoreResult:
            raise ThinkContractError("CommandPort.build_command must return a ThinkCoreResult")
        core = core_value
        next_frame = ThinkFrame(
            CommandStep(step.prompt, step.compacted, step.inference, core),
            frame.hook_state,
        )
        return Graph.values(hook_request=HookRequest(next_frame, frame.hook_state, GraphNodeId("command")))


__all__ = ["CommandNode"]
