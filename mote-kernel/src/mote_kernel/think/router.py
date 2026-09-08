"""Model-routing stage callable for the Think graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import RouterBinding
from mote_kernel.think.contract import (
    ModelBinding,
    RouterNodeInput,
    RouterPort,
    RouterRequest,
    RouterStep,
    ThinkContractError,
    ThinkFrame,
    admit_compact_frame,
)

HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")


@dataclass(frozen=True, slots=True)
class RouterNode(
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
    ],
):
    """Select the model binding immediately before model inference."""

    router_port: RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]]

    def __post_init__(self) -> None:
        if operator.is_(self.router_port, None):
            raise ThinkContractError("router requires a RouterPort")
        try:
            method = self.router_port.route_model
        except AttributeError as error:
            raise ThinkContractError("router requires a RouterPort") from error
        if not callable(method):
            raise ThinkContractError("RouterPort.route_model must be callable")

    async def __call__(
        self,
        activation: ConfigActivation[
            RouterNodeInput[
                HookStateT,
                SystemPromptT,
                PlaceholderT,
                UserPromptT,
                ContextSnapshotT,
                CompactedSnapshotT,
                HookGraphValue,
            ]
        ],
        /,
    ) -> HookActivationRequest[
        ThinkFrame[
            RouterStep[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
            HookStateT,
        ],
        HookStateT,
    ]:
        value = activation.value
        frame = admit_compact_frame(value.hook_result)
        config = activation.activation_config
        step = frame.step
        request = RouterRequest(step.prompt, step.compacted)

        router_port = self.router_port
        if config is not None:
            router_port = config.bind(
                RouterBinding[
                    SystemPromptT,
                    PlaceholderT,
                    UserPromptT,
                    CompactedSnapshotT,
                ]()
            ).port
        model_value = await router_port.route_model(request)
        if type(model_value) is not ModelBinding:
            raise ThinkContractError("RouterPort.route_model must return a ModelBinding")
        model = model_value
        next_frame = ThinkFrame(RouterStep(step.prompt, step.compacted, model), frame.hook_state)
        return HookActivationRequest(next_frame, frame.hook_state, GraphNodeId("router"))


__all__ = ["RouterNode"]
