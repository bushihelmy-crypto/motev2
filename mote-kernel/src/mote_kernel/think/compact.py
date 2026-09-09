"""Compact stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.failover.contract import TypedPortDecorator
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookResult
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import CompactBinding
from mote_kernel.think.contract import (
    CompactedContext,
    CompactPort,
    CompactRequest,
    CompactStep,
    ContextStep,
    ThinkContractError,
    ThinkFrame,
    admit_context_frame,
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
    failover: (
        TypedPortDecorator[
            CompactPort[
                CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
                CompactedContext[CompactedSnapshotT],
            ]
        ]
        | FailoverPortDecorator
        | None
    ) = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        port = apply_think_port_decorator(self.compact_port, self.failover, CompactPort, "CompactPort")
        object.__setattr__(self, "compact_port", port)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ThinkContractError("compact assembly snapshot key is malformed")
        try:
            method = port.compact
        except AttributeError as error:
            raise ThinkContractError("compact requires a CompactPort") from error
        if not callable(method):
            raise ThinkContractError("CompactPort.compact must be callable")

    async def __call__(
        self,
        activation: ConfigActivation[
            HookResult[
                ThinkFrame[
                    ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
                    HookStateT,
                ],
                HookCommandT,
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
        frame = admit_context_frame(activation.value)
        config = activation.activation_config
        step = frame.step
        request = CompactRequest(step.prompt, step.context)

        compact_port = self.compact_port
        if config is not None:
            selected = config.bind(
                CompactBinding[
                    SystemPromptT,
                    PlaceholderT,
                    UserPromptT,
                    ContextSnapshotT,
                    CompactedSnapshotT,
                ]()
            )
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                compact_port = apply_think_port_decorator(selected.port, self.failover, CompactPort, "CompactPort")
        compacted_value = await compact_port.compact(request)
        if type(compacted_value) is not CompactedContext:
            raise ThinkContractError("CompactPort.compact must return a CompactedContext")
        compacted = compacted_value
        next_frame = ThinkFrame(CompactStep(step.prompt, step.context, compacted), frame.hook_state)
        return HookActivationRequest(
            next_frame,
            frame.hook_state,
            GraphNodeId(str(ThinkNodeId.COMPACT)),
        )


__all__ = ["CompactNode"]
