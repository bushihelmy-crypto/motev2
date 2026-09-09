"""Command stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.failover.contract import TypedPortDecorator
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
from mote_kernel.think.failover import (
    FailoverPortDecorator,
    apply_think_port_decorator,
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
    failover: (
        TypedPortDecorator[CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]]]
        | FailoverPortDecorator
        | None
    ) = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        port = apply_think_port_decorator(self.command_port, self.failover, CommandPort, "CommandPort")
        object.__setattr__(self, "command_port", port)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ThinkContractError("command assembly snapshot key is malformed")
        try:
            method = port.build_command
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
            selected = config.bind(CommandBinding[ModelOutputT, CommandT]())
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                command_port = apply_think_port_decorator(selected.port, self.failover, CommandPort, "CommandPort")
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
