"""Inference stage callable for the Think graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.hooks.contract import HookGraphValue, HookRequest
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.contract import (
    InferenceNodeInput,
    InferencePort,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    ModelBinding,
    ThinkContractError,
    ThinkFrame,
    admit_compact_frame,
)

HookStateT = TypeVar("HookStateT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
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
    model_binding: ModelBinding

    def __post_init__(self) -> None:
        if operator.is_(self.inference_port, None):
            raise ThinkContractError("inference requires an InferencePort")
        try:
            method = self.inference_port.infer
        except AttributeError as error:
            raise ThinkContractError("inference requires an InferencePort") from error
        if not callable(method):
            raise ThinkContractError("InferencePort.infer must be callable")
        if type(self.model_binding) is not ModelBinding:
            raise ThinkContractError("inference requires an exact ModelBinding")

    async def __call__(
        self,
        value: InferenceNodeInput[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            CompactedSnapshotT,
            HookGraphValue,
        ],
        /,
    ) -> HookRequest[
        ThinkFrame[
            InferenceStep[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT],
            HookStateT,
        ],
        HookStateT,
    ]:
        frame = admit_compact_frame(value.hook_result)
        step = frame.step
        compacted = step.compacted
        prompt = step.prompt
        request = InferenceRequest(prompt, compacted, self.model_binding)

        result_value = await self.inference_port.infer(request)
        if type(result_value) is not InferenceResult:
            raise ThinkContractError("InferencePort.infer must return an InferenceResult")
        result = result_value
        next_frame = ThinkFrame(InferenceStep(prompt, compacted, result), frame.hook_state)
        return HookRequest(next_frame, frame.hook_state, GraphNodeId("inference"))


__all__ = ["InferenceNode"]
