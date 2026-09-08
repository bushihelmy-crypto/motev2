"""Inference stage callable for the Think graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import InferenceBinding
from mote_kernel.think.contract import (
    InferenceNodeInput,
    InferencePort,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    ThinkContractError,
    ThinkFrame,
    admit_router_frame,
)

HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")


@dataclass(frozen=True, slots=True)
class InferenceNode(
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        CompactedSnapshotT,
        ModelOutputT,
    ],
):
    """Assemble the final model request and invoke the injected model Port."""

    inference_port: InferencePort[
        InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
        InferenceResult[ModelOutputT],
    ]

    def __post_init__(self) -> None:
        if operator.is_(self.inference_port, None):
            raise ThinkContractError("inference requires an InferencePort")
        try:
            method = self.inference_port.infer
        except AttributeError as error:
            raise ThinkContractError("inference requires an InferencePort") from error
        if not callable(method):
            raise ThinkContractError("InferencePort.infer must be callable")

    async def __call__(
        self,
        activation: ConfigActivation[
            InferenceNodeInput[
                HookStateT,
                SystemPromptT,
                PlaceholderT,
                UserPromptT,
                CompactedSnapshotT,
                HookGraphValue,
            ]
        ],
        /,
    ) -> HookActivationRequest[
        ThinkFrame[
            InferenceStep[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT],
            HookStateT,
        ],
        HookStateT,
    ]:
        value = activation.value
        frame = admit_router_frame(value.hook_result)
        config = activation.activation_config
        step = frame.step
        compacted = step.compacted
        prompt = step.prompt
        model = step.model
        request = InferenceRequest(prompt, compacted, model)

        inference_port = self.inference_port
        if config is not None:
            inference_port = config.bind(
                InferenceBinding[
                    SystemPromptT,
                    PlaceholderT,
                    UserPromptT,
                    CompactedSnapshotT,
                    ModelOutputT,
                ]()
            ).port
        result_value = await inference_port.infer(request)
        if type(result_value) is not InferenceResult:
            raise ThinkContractError("InferencePort.infer must return an InferenceResult")
        result = result_value
        next_frame = ThinkFrame(InferenceStep(prompt, compacted, model, result), frame.hook_state)
        return HookActivationRequest(next_frame, frame.hook_state, GraphNodeId("inference"))


__all__ = ["InferenceNode"]
