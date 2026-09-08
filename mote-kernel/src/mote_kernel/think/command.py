"""Command stage callable for the Think graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookResult
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import CommandBinding
from mote_kernel.think.contract import (
    CommandPort,
    CommandStep,
    InferenceResult,
    InferenceStep,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    admit_inference_frame,
)
from mote_kernel.think.identity import ThinkNodeId

HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
HookCommandT = TypeVar("HookCommandT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")
CommandT = TypeVar("CommandT")


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

    command_port: CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]]

    def __post_init__(self) -> None:
        if operator.is_(self.command_port, None):
            raise ThinkContractError("command requires a CommandPort")
        try:
            method = self.command_port.build_command
        except AttributeError as error:
            raise ThinkContractError("command requires a CommandPort") from error
        if not callable(method):
            raise ThinkContractError("CommandPort.build_command must be callable")

    async def __call__(
        self,
        activation: ConfigActivation[
            HookResult[
                ThinkFrame[
                    InferenceStep[
                        SystemPromptT,
                        PlaceholderT,
                        UserPromptT,
                        CompactedSnapshotT,
                        ModelOutputT,
                    ],
                    HookStateT,
                ],
                HookCommandT,
            ]
        ],
        /,
    ) -> HookActivationRequest[
        ThinkFrame[
            CommandStep[
                SystemPromptT,
                PlaceholderT,
                UserPromptT,
                CompactedSnapshotT,
                ModelOutputT,
                CommandT,
            ],
            HookStateT,
        ],
        HookStateT,
    ]:
        frame = admit_inference_frame(activation.value)
        config = activation.activation_config
        step = frame.step
        command_port = self.command_port
        if config is not None:
            command_port = config.bind(CommandBinding[ModelOutputT, CommandT]()).port
        core_value = await command_port.build_command(step.inference)
        if type(core_value) is not ThinkCoreResult:
            raise ThinkContractError("CommandPort.build_command must return a ThinkCoreResult")
        core = core_value
        next_frame = ThinkFrame(
            CommandStep(step.prompt, step.compacted, step.model, step.inference, core),
            frame.hook_state,
        )
        return HookActivationRequest(
            next_frame,
            frame.hook_state,
            GraphNodeId(str(ThinkNodeId.COMMAND)),
        )


__all__ = ["CommandNode"]
