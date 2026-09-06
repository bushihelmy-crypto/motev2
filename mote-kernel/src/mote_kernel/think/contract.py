"""Typed values and capability contracts owned by the Think graph.

The execution engine carries one :class:`ThinkFrame` through the shared Hook
child.  The classes in this module are deliberately small, frozen nominal
envelopes: they describe the hand-off between Think stages but do not own a
state store, an invocation runner, or a retry policy.  Payloads held inside the
envelopes are admitted by the corresponding Port/Invocation owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, cast, runtime_checkable

from mote_kernel.hooks.contract import HookGraphValue, HookResult
from mote_kernel.state.graph_state import GraphNodeId

PayloadT_contra = TypeVar("PayloadT_contra", contravariant=True)
SystemPromptT_co = TypeVar("SystemPromptT_co", covariant=True)
PlaceholderT_co = TypeVar("PlaceholderT_co", covariant=True)
UserPromptT_co = TypeVar("UserPromptT_co", covariant=True)
ContextRequestT_contra = TypeVar("ContextRequestT_contra", contravariant=True)
ContextFrameT_co = TypeVar("ContextFrameT_co", covariant=True)
CompactRequestT_contra = TypeVar("CompactRequestT_contra", contravariant=True)
CompactedContextT_co = TypeVar("CompactedContextT_co", covariant=True)
InferenceRequestT_contra = TypeVar("InferenceRequestT_contra", contravariant=True)
InferenceResultT_co = TypeVar("InferenceResultT_co", covariant=True)
CommandRequestT_contra = TypeVar("CommandRequestT_contra", contravariant=True)
ThinkCoreResultT_co = TypeVar("ThinkCoreResultT_co", covariant=True)

PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")
CommandT = TypeVar("CommandT")
ThinkStepT = TypeVar("ThinkStepT")
HookCommandT = TypeVar("HookCommandT")
StageT = TypeVar("StageT", bound="ThinkStep")


class ThinkContractError(ValueError):
    """Raised when a Think-owned value crosses its typed boundary incorrectly."""


ValueT = TypeVar("ValueT")


def _require_present(value: ValueT | None, field: str, /) -> ValueT:
    """Reject an absent required outer field without inspecting its payload.

    Think intentionally does not implement a recursive data-only validator.
    The owner of each concrete payload is responsible for that deeper
    admission; the graph only rejects an absent hand-off value.
    """

    if value is None:
        raise ThinkContractError(f"{field} is required")
    return value


def _require_exact(value: ValueT, expected: type[ValueT], field: str, /) -> ValueT:
    if type(value) is not expected:
        raise ThinkContractError(f"{field} must be an exact {expected.__name__}")
    return cast(ValueT, value)


@dataclass(frozen=True, slots=True)
class ThinkRequest(HookGraphValue, Generic[PayloadT, HookStateT]):
    """The immutable graph input for one Think activation."""

    payload: PayloadT
    hook_state: HookStateT

    def __post_init__(self) -> None:
        _require_present(self.payload, "think request payload")
        _require_present(self.hook_state, "think request hook_state")


@dataclass(frozen=True, slots=True)
class PromptFrame(
    HookGraphValue,
    Generic[SystemPromptT, PlaceholderT, UserPromptT],
):
    """The three prompt components captured by the Prompt stage."""

    system: SystemPromptT
    placeholder: PlaceholderT
    user: UserPromptT

    def __post_init__(self) -> None:
        _require_present(self.system, "prompt system value")
        _require_present(self.placeholder, "prompt placeholder value")
        _require_present(self.user, "prompt user value")


@dataclass(frozen=True, slots=True)
class ContextFrame(HookGraphValue, Generic[ContextSnapshotT]):
    """The immutable context snapshot loaded for one turn."""

    snapshot: ContextSnapshotT

    def __post_init__(self) -> None:
        _require_present(self.snapshot, "context snapshot")


@dataclass(frozen=True, slots=True)
class CompactedContext(HookGraphValue, Generic[CompactedSnapshotT]):
    """The context selected for model inference and its token count."""

    snapshot: CompactedSnapshotT
    token_count: int

    def __post_init__(self) -> None:
        _require_present(self.snapshot, "compacted context snapshot")
        if type(self.token_count) is not int or self.token_count < 0:
            raise ThinkContractError("compacted context token_count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ModelBinding(HookGraphValue):
    """An immutable model identity captured at Think assembly time."""

    provider_id: str
    model_id: str
    revision: int

    def __post_init__(self) -> None:
        for field, value in (("provider_id", self.provider_id), ("model_id", self.model_id)):
            if type(value) is not str or not value or value != value.strip() or "\n" in value or "\r" in value:
                raise ThinkContractError(f"model binding {field} must be a canonical non-empty string")
        if type(self.revision) is not int or self.revision < 1:
            raise ThinkContractError("model binding revision must be a positive integer")


@dataclass(frozen=True, slots=True)
class InferenceResult(HookGraphValue, Generic[ModelOutputT]):
    """The provider-neutral, normalized model output."""

    output: ModelOutputT

    def __post_init__(self) -> None:
        _require_present(self.output, "inference output")


@dataclass(frozen=True, slots=True)
class ThinkCoreResult(HookGraphValue, Generic[CommandT]):
    """The structured command/turn payload produced by the Command stage."""

    command: CommandT

    def __post_init__(self) -> None:
        _require_present(self.command, "think core command")


@dataclass(frozen=True, slots=True)
class ContextRequest(
    HookGraphValue,
    Generic[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
):
    """The single request envelope sent to :class:`ContextPort`."""

    request: ThinkRequest[PayloadT, HookStateT]
    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]

    def __post_init__(self) -> None:
        _require_exact(self.request, ThinkRequest, "context request input")
        _require_exact(self.prompt, PromptFrame, "context request prompt")


@dataclass(frozen=True, slots=True)
class CompactRequest(
    HookGraphValue,
    Generic[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
):
    """The single request envelope sent to :class:`CompactPort`."""

    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]
    context: ContextFrame[ContextSnapshotT]

    def __post_init__(self) -> None:
        _require_exact(self.prompt, PromptFrame, "compact request prompt")
        _require_exact(self.context, ContextFrame, "compact request context")


@dataclass(frozen=True, slots=True)
class InferenceRequest(
    HookGraphValue,
    Generic[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
):
    """The final model request assembled from compacted context."""

    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]
    compacted: CompactedContext[CompactedSnapshotT]
    model: ModelBinding

    def __post_init__(self) -> None:
        _require_exact(self.prompt, PromptFrame, "inference request prompt")
        _require_exact(self.compacted, CompactedContext, "inference request compacted context")
        _require_exact(self.model, ModelBinding, "inference request model")


@dataclass(frozen=True, slots=True)
class ContextNodeInput(
    HookGraphValue,
    Generic[
        PayloadT,
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        HookCommandT,
    ],
):
    """Typed materialization input for the Context node.

    ``HookResult`` is intentionally the only predecessor value here.  The
    Context node validates its revised frame and then derives the
    ``ContextRequest`` sent to the Context Port.
    """

    request: ThinkRequest[PayloadT, HookStateT]
    hook_result: HookResult[
        ThinkFrame[PromptStep[SystemPromptT, PlaceholderT, UserPromptT], HookStateT],
        HookCommandT,
    ]

    def __post_init__(self) -> None:
        _require_exact(self.request, ThinkRequest, "context node request")
        _require_exact(self.hook_result, HookResult, "context node HookResult")


@dataclass(frozen=True, slots=True)
class CompactNodeInput(
    HookGraphValue,
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        HookCommandT,
    ],
):
    """Typed materialization input for the Compact node."""

    hook_result: HookResult[
        ThinkFrame[
            ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
            HookStateT,
        ],
        HookCommandT,
    ]

    def __post_init__(self) -> None:
        _require_exact(self.hook_result, HookResult, "compact node HookResult")


@dataclass(frozen=True, slots=True)
class InferenceNodeInput(
    HookGraphValue,
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
        HookCommandT,
    ],
):
    """Typed materialization input for the Inference node."""

    hook_result: HookResult[
        ThinkFrame[
            CompactStep[
                SystemPromptT,
                PlaceholderT,
                UserPromptT,
                ContextSnapshotT,
                CompactedSnapshotT,
            ],
            HookStateT,
        ],
        HookCommandT,
    ]

    def __post_init__(self) -> None:
        _require_exact(self.hook_result, HookResult, "inference node HookResult")


@dataclass(frozen=True, slots=True)
class CommandNodeInput(
    HookGraphValue,
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        CompactedSnapshotT,
        ModelOutputT,
        HookCommandT,
    ],
):
    """Typed materialization input for the Command node."""

    hook_result: HookResult[
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

    def __post_init__(self) -> None:
        _require_exact(self.hook_result, HookResult, "command node HookResult")


class ThinkStep(HookGraphValue):
    """Nominal base for the closed set of Think stage values."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class PromptStep(
    ThinkStep,
    Generic[SystemPromptT, PlaceholderT, UserPromptT],
):
    """The value produced after the Prompt stage completes."""

    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]

    def __post_init__(self) -> None:
        _require_exact(self.prompt, PromptFrame, "prompt step prompt")


@dataclass(frozen=True, slots=True)
class ContextStep(
    ThinkStep,
    Generic[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
):
    """The value produced after the Context stage completes."""

    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]
    context: ContextFrame[ContextSnapshotT]

    def __post_init__(self) -> None:
        _require_exact(self.prompt, PromptFrame, "context step prompt")
        _require_exact(self.context, ContextFrame, "context step context")


@dataclass(frozen=True, slots=True)
class CompactStep(
    ThinkStep,
    Generic[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT, CompactedSnapshotT],
):
    """The value produced after the Compact stage completes."""

    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]
    context: ContextFrame[ContextSnapshotT]
    compacted: CompactedContext[CompactedSnapshotT]

    def __post_init__(self) -> None:
        _require_exact(self.prompt, PromptFrame, "compact step prompt")
        _require_exact(self.context, ContextFrame, "compact step context")
        _require_exact(self.compacted, CompactedContext, "compact step compacted context")


@dataclass(frozen=True, slots=True)
class InferenceStep(
    ThinkStep,
    Generic[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT],
):
    """The value produced after the Inference stage completes."""

    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]
    compacted: CompactedContext[CompactedSnapshotT]
    inference: InferenceResult[ModelOutputT]

    def __post_init__(self) -> None:
        _require_exact(self.prompt, PromptFrame, "inference step prompt")
        _require_exact(self.compacted, CompactedContext, "inference step compacted context")
        _require_exact(self.inference, InferenceResult, "inference step result")


@dataclass(frozen=True, slots=True)
class CommandStep(
    ThinkStep,
    Generic[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT, CommandT],
):
    """The terminal value produced after the Command stage completes."""

    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]
    compacted: CompactedContext[CompactedSnapshotT]
    inference: InferenceResult[ModelOutputT]
    core: ThinkCoreResult[CommandT]

    def __post_init__(self) -> None:
        _require_exact(self.prompt, PromptFrame, "command step prompt")
        _require_exact(self.compacted, CompactedContext, "command step compacted context")
        _require_exact(self.inference, InferenceResult, "command step result")
        _require_exact(self.core, ThinkCoreResult, "command step core result")


# This tuple is deliberately closed and owned by this module.  ThinkFrame does
# not accept an arbitrary consumer-defined ThinkStep subclass.
_THINK_STEP_VARIANTS: tuple[type[ThinkStep], ...] = (
    PromptStep,
    ContextStep,
    CompactStep,
    InferenceStep,
    CommandStep,
)


@dataclass(frozen=True, slots=True)
class ThinkFrame(HookGraphValue, Generic[ThinkStepT, HookStateT]):
    """The single value envelope exchanged with the shared Hook child."""

    step: ThinkStepT
    hook_state: HookStateT

    def __post_init__(self) -> None:
        if type(self.step) not in _THINK_STEP_VARIANTS:
            raise ThinkContractError("think frame must contain a known ThinkStep")
        _require_present(self.hook_state, "think frame hook_state")


def admit_prompt_frame(
    result: HookResult[
        ThinkFrame[PromptStep[SystemPromptT, PlaceholderT, UserPromptT], HookStateT],
        HookCommandT,
    ],
    state: HookStateT,
    /,
) -> ThinkFrame[PromptStep[SystemPromptT, PlaceholderT, UserPromptT], HookStateT]:
    """Admit the frame returned by the Prompt Hook activation."""

    return _admit_stage_frame(
        result,
        expected_step=PromptStep,
        expected_node=GraphNodeId("prompt"),
        stage="context",
        expected_state=state,
    )


def admit_context_frame(
    result: HookResult[
        ThinkFrame[ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT], HookStateT],
        HookCommandT,
    ],
    /,
) -> ThinkFrame[ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT], HookStateT]:
    """Admit the frame returned by the Context Hook activation."""

    return _admit_stage_frame(
        result,
        expected_step=ContextStep,
        expected_node=GraphNodeId("context"),
        stage="compact",
    )


def admit_compact_frame(
    result: HookResult[
        ThinkFrame[
            CompactStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT, CompactedSnapshotT],
            HookStateT,
        ],
        HookCommandT,
    ],
    /,
) -> ThinkFrame[
    CompactStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT, CompactedSnapshotT],
    HookStateT,
]:
    """Admit the frame returned by the Compact Hook activation."""

    return _admit_stage_frame(
        result,
        expected_step=CompactStep,
        expected_node=GraphNodeId("compact"),
        stage="inference",
    )


def admit_inference_frame(
    result: HookResult[
        ThinkFrame[
            InferenceStep[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT],
            HookStateT,
        ],
        HookCommandT,
    ],
    /,
) -> ThinkFrame[
    InferenceStep[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT],
    HookStateT,
]:
    """Admit the frame returned by the Inference Hook activation."""

    return _admit_stage_frame(
        result,
        expected_step=InferenceStep,
        expected_node=GraphNodeId("inference"),
        stage="command",
    )


def _admit_stage_frame(
    result: HookResult[ThinkFrame[StageT, HookStateT], HookCommandT],
    *,
    expected_step: type[StageT],
    expected_node: GraphNodeId,
    stage: str,
    expected_state: HookStateT | None = None,
) -> ThinkFrame[StageT, HookStateT]:
    """Admit one Hook result at a Think stage boundary.

    The execution adapter admits the outer ``HookResult`` class.  The nested
    Hook is nevertheless allowed to revise its value, so Think keeps this
    small semantic guard for the frame, predecessor identity, state (when the
    stage has the original request), and closed step kind.  Generic recovery
    is concentrated here; stage nodes receive a concrete frame and never
    recover a type from a ``Graph.Values`` mapping.
    """

    raw_frame = result.value
    if type(raw_frame) is not ThinkFrame:
        raise ThinkContractError(f"{stage} HookResult must contain a ThinkFrame")
    frame = raw_frame
    if expected_state is not None and frame.hook_state != expected_state:
        raise ThinkContractError(f"{stage} HookResult state does not match the ThinkRequest state")
    if result.node_id is not None and result.node_id != expected_node:
        raise ThinkContractError(f"{stage} requires a HookResult produced by {expected_node}")
    raw_step = frame.step
    if type(raw_step) is not expected_step:
        raise ThinkContractError(f"{stage} requires a {expected_step.__name__}")
    return frame


@runtime_checkable
class PromptPort(
    Protocol[
        PayloadT_contra,
        SystemPromptT_co,
        PlaceholderT_co,
        UserPromptT_co,
    ]
):
    """One Prompt capability with three ordered collection operations."""

    async def load_system_prompt(self, payload: PayloadT_contra, /) -> SystemPromptT_co: ...

    async def load_placeholder(self, payload: PayloadT_contra, /) -> PlaceholderT_co: ...

    async def load_user_prompt(self, payload: PayloadT_contra, /) -> UserPromptT_co: ...


@runtime_checkable
class ContextPort(Protocol[ContextRequestT_contra, ContextFrameT_co]):
    """Load one immutable context snapshot for a typed request."""

    async def load_context(self, request: ContextRequestT_contra, /) -> ContextFrameT_co: ...


@runtime_checkable
class CompactPort(Protocol[CompactRequestT_contra, CompactedContextT_co]):
    """Compact one context request into a model-facing snapshot."""

    async def compact(self, request: CompactRequestT_contra, /) -> CompactedContextT_co: ...


@runtime_checkable
class InferencePort(Protocol[InferenceRequestT_contra, InferenceResultT_co]):
    """Invoke the already assembled model request once."""

    async def infer(self, request: InferenceRequestT_contra, /) -> InferenceResultT_co: ...


@runtime_checkable
class CommandPort(Protocol[CommandRequestT_contra, ThinkCoreResultT_co]):
    """Structure one normalized inference result into a core command."""

    async def build_command(self, request: CommandRequestT_contra, /) -> ThinkCoreResultT_co: ...


__all__ = [
    "CommandPort",
    "CommandStep",
    "CompactPort",
    "CompactRequest",
    "CompactStep",
    "CompactedContext",
    "ContextFrame",
    "ContextPort",
    "ContextRequest",
    "ContextStep",
    "InferencePort",
    "InferenceRequest",
    "InferenceResult",
    "InferenceStep",
    "ModelBinding",
    "PromptFrame",
    "PromptPort",
    "PromptStep",
    "ThinkContractError",
    "ThinkCoreResult",
    "ThinkFrame",
    "ThinkRequest",
    "ThinkStep",
]
