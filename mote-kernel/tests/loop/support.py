"""Self-contained values and capabilities used by the ReAct boundary tests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.contract import (
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
    AuthorizationDecision,
    AuthorizationInput,
    AuthorizationInterruptView,
    AuthorizationRequestRef,
    AuthorizedInvocation,
    CanonicalArguments,
    ResolvedInvocation,
    ResolvePortResult,
    SettledActResult,
    SettlementProjection,
    SettleStageValue,
    ToolExchangeWriteRequest,
    ToolExchangeWriteResult,
    ToolExecutionResult,
)
from mote_kernel.act.contract import (
    HookStateProjection as ActHookStateProjection,
)
from mote_kernel.act.identity import (
    ActHookStage,
    ActInvocationKey,
    ArgumentsDigest,
    CallerIdentityRef,
    OpaqueArguments,
    OpaqueAuthorizationHandle,
    OpaqueDefinitionReference,
    OpaqueExecutionOutcome,
    OpaqueGraphFailureReason,
    OpaqueProtocolPayload,
    OpaqueToolExchangeReceipt,
    ToolBindingRef,
    ToolCallId,
    ToolExchangeScopeId,
    ToolExecutionIdentity,
    ToolPairingIdentity,
    ToolSelector,
)
from mote_kernel.act.node import ActNode
from mote_kernel.execution import Graph
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import (
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookResult,
    HookStageResult,
    HookTransitionAdmission,
)
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.hooks.plan import HookPlan, HookPriorityPlan
from mote_kernel.loop.admission import ReActPayloadAdmission
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.contract import (
    AssistantBatch,
    Available,
    BackgroundTaskSnapshot,
    ConfigBatch,
    ConfigSettlementReceipt,
    Conflict,
    ContextAppendReceipt,
    DeliveryAck,
    Empty,
    NonConfigObservationFamily,
    Observation,
    ObservationBatch,
    ObservationBatchReceipt,
    ObservationDelivery,
    ObservationKind,
    ObservationPayload,
    ObservationRead,
    ObserveHookCommand,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
    ToolBatch,
    UserBatch,
    UserObservation,
    WriteObservationStageValue,
)
from mote_kernel.observe.contract import (
    HookStateProjection as ObserveHookStateProjection,
)
from mote_kernel.observe.identity import (
    CursorRange,
    DeliveryAckReference,
    DeliveryId,
    ObservationBoundary,
    ObservationCursor,
    ObservationWait,
    ObserveHookStage,
    WaitRegistration,
)
from mote_kernel.observe.node import ObserveNode
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId
from mote_kernel.think.contract import (
    CommandStep,
    CompactedContext,
    CompactRequest,
    ContextFrame,
    ContextRequest,
    InferenceRequest,
    InferenceResult,
    ModelBinding,
    PromptFrame,
    RouterRequest,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)
from mote_kernel.think.node import ThinkNode


@dataclass(frozen=True, slots=True)
class SharedState(ObserveHookStateProjection, ActHookStateProjection):
    """One concrete state class intentionally shared by all three children."""

    marker: str = "state"
    cursor: ObservationCursor = field(default_factory=lambda: ObservationCursor("stream", 0))


@dataclass(frozen=True, slots=True)
class ObserveCommand(ObserveHookCommand):
    name: str = "observe"


@dataclass(frozen=True, slots=True)
class ThinkCommand(HookGraphValue):
    name: str = "think"


@dataclass(frozen=True, slots=True)
class ActCommand(ActHookCommand):
    name: str = "act"


@dataclass(frozen=True, slots=True)
class Priority:
    ordinal: int


@dataclass(frozen=True, slots=True)
class ObservationText(ObservationPayload):
    text: str


@dataclass(frozen=True, slots=True)
class ThinkPayload(HookGraphValue):
    text: str


ValueT = TypeVar("ValueT", bound=HookGraphValue)
StateT = TypeVar("StateT", bound=HookGraphValue)
CommandT = TypeVar("CommandT", bound=HookGraphValue)


@dataclass(frozen=True, slots=True)
class PassThroughInvocation(Generic[ValueT, StateT, CommandT]):
    """A deterministic Hook Invocation that preserves the value unchanged."""

    async def invoke(
        self,
        request: HookInvocationRequest[Priority, ValueT, StateT],
        /,
    ) -> HookStageResult[ValueT, CommandT]:
        return HookStageResult(request.request.value)


def hook_plan() -> HookPlan[Priority]:
    return HookPlan(HookPriorityPlan(Priority(1)), HookPriorityPlan(Priority(2)))


def make_hook(
    definition_id: str,
    value_type: type[ValueT],
    state_type: type[StateT],
    command_type: type[CommandT],
    transition: HookTransitionAdmission[ValueT, StateT, CommandT] | None = None,
) -> HookNode[Priority, ValueT, StateT, CommandT]:
    admission = HookPayloadAdmission(
        Priority,
        value_type,
        state_type,
        command_type,
        transition,
    )
    return HookNode(
        HookSlotId(
            GraphDefinitionId(definition_id),
            GraphDefinitionVersion(1),
            GraphNodeId("hook"),
            HookStage.AFTER_NODE,
        ),
        hook_plan(),
        PassThroughInvocation[ValueT, StateT, CommandT](),
        admission,
    )


def observe_admission() -> ObservePayloadAdmission:
    return ObservePayloadAdmission(
        SharedState,
        ObserveCommand,
        ObservationText,
        ObservationText,
        ObservationText,
        ObservationText,
    )


def act_admission() -> ActPayloadAdmission[SharedState, ActCommand]:
    return ActPayloadAdmission(SharedState, ActCommand)


def react_admission() -> ReActPayloadAdmission[
    SharedState,
    ObserveCommand,
    SharedState,
    ThinkCommand,
    SharedState,
    ActCommand,
]:
    return ReActPayloadAdmission(
        observe_admission(),
        SharedState,
        ThinkCommand,
        act_admission(),
    )


def cursor(sequence: int) -> ObservationCursor:
    return ObservationCursor("stream", sequence)


def delivery(
    sequence: int,
    observation: Observation,
    delivery_id: str,
    *,
    revision: int = 1,
) -> ObservationDelivery[Observation]:
    return ObservationDelivery(
        DeliveryId(delivery_id),
        cursor(sequence),
        cursor(sequence + 1),
        observation,
        revision,
    )


def available(*deliveries: ObservationDelivery[Observation]) -> Available:
    batch = ObservationBatch(tuple(deliveries))
    return Available(
        batch,
        ObservationBoundary(
            "stream",
            deliveries[0].cursor_before,
            deliveries[-1].cursor_after,
            deliveries[0].observation_revision,
        ),
    )


def empty(sequence: int = 0, *, revision: int = 1) -> ObservationRead:
    point = cursor(sequence)
    boundary = ObservationBoundary("stream", point, point, revision)
    return Empty(
        ObservationWait("stream", point, revision, "delivery"),
        boundary,
    )


class ObservePorts:
    """In-memory capabilities with no hidden state outside the test object."""

    def __init__(self, reads: tuple[ObservationRead, ...] = ()) -> None:
        self.reads = list(reads)
        self.read_cursors: list[ObservationCursor] = []
        self.waits: list[ObservationWait] = []
        self._last_boundary: ObservationBoundary | None = None

    async def read_after(self, requested: ObservationCursor, /) -> ObservationRead:
        self.read_cursors.append(requested)
        if not self.reads:
            raise AssertionError("unexpected queue read")
        read = self.reads.pop(0)
        if type(read) in (Available, Empty):
            boundary = cast(Available | Empty, read).boundary
        else:
            boundary = cast(Conflict, read).boundary
        if boundary.cursor_before != requested:
            raise AssertionError("queue read did not start at the requested cursor")
        self._last_boundary = boundary
        return read

    async def register_wait(self, wait: ObservationWait, /) -> WaitRegistration:
        self.waits.append(wait)
        return WaitRegistration("wait-1", wait.stream_id, wait.after_cursor, wait.observation_revision)

    async def snapshot(self, boundary: ObservationBoundary, /) -> BackgroundTaskSnapshot:
        return BackgroundTaskSnapshot(boundary.observation_revision, ())

    async def apply(self, batch: ConfigBatch, /) -> ConfigSettlementReceipt:
        boundary = self._require_boundary()
        return ConfigSettlementReceipt(batch.delivery_ids, boundary, boundary, "config-settlement")

    async def append(
        self,
        batch: ToolBatch | UserBatch | AssistantBatch,
        /,
    ) -> ContextAppendReceipt:
        boundary = self._require_boundary()
        family = (
            NonConfigObservationFamily.TOOL
            if type(batch) is ToolBatch
            else NonConfigObservationFamily.USER
            if type(batch) is UserBatch
            else NonConfigObservationFamily.ASSISTANT
        )
        return ContextAppendReceipt(batch.delivery_ids, boundary, boundary, "context-settlement", family)

    async def acknowledge(
        self,
        delivery_ids: tuple[DeliveryId, ...],
        receipt: ObservationBatchReceipt,
        /,
    ) -> DeliveryAck:
        return DeliveryAck(receipt.ack_reference)

    def encode_graph_input(self, values: Graph.Values[HookGraphValue], /) -> bytes:
        request = cast(ObserveRequest[SharedState], values["request"])
        return f"{request.cursor.stream_id}:{request.cursor.sequence}:{request.hook_state.marker}".encode()

    def decode_graph_input(self, payload: bytes, /) -> Graph.Values[HookGraphValue]:
        stream, sequence, marker = payload.decode().split(":")
        return Graph.values(request=ObserveRequest(ObservationCursor(stream, int(sequence)), SharedState(marker)))

    @property
    def codec_id(self) -> str:
        return "loop-observe-input"

    @property
    def codec_version(self) -> int:
        return 1

    def _require_boundary(self) -> ObservationBoundary:
        boundary = self._last_boundary
        if boundary is None:
            raise AssertionError("settlement called before a queue read")
        return boundary


class ThinkPorts:
    def __init__(self) -> None:
        self.requests: list[ThinkRequest[ThinkPayload, SharedState]] = []
        self.payloads: list[ThinkPayload] = []

    async def load_system_prompt(self, payload: ThinkPayload, /) -> str:
        # Prompt is the first Think stage, so this records one request per
        # Think activation without introducing a second test-only runner.
        self.payloads.append(payload)
        return f"system:{payload.text}"

    async def load_placeholder(self, payload: ThinkPayload, /) -> str:
        return "placeholder"

    async def load_user_prompt(self, payload: ThinkPayload, /) -> str:
        return payload.text

    async def load_context(
        self,
        request: ContextRequest[ThinkPayload, SharedState, str, str, str],
        /,
    ) -> ContextFrame[tuple[str, ...]]:
        self.requests.append(request.request)
        return ContextFrame((request.request.payload.text,))

    async def compact(
        self,
        request: CompactRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> CompactedContext[tuple[str, ...]]:
        return CompactedContext(request.context.snapshot, 1)

    async def route_model(
        self,
        request: RouterRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> ModelBinding:
        return ModelBinding("provider", "model", 1)

    async def infer(
        self,
        request: InferenceRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> InferenceResult[str]:
        return InferenceResult(request.prompt.user)

    async def build_command(self, request: InferenceResult[str], /) -> ThinkCoreResult[str]:
        return ThinkCoreResult(request.output)


class ActPorts:
    def __init__(self) -> None:
        self.requests: list[ActRequest] = []
        self._resolved: ResolvedInvocation | None = None
        self._encoded_inputs: dict[bytes, Graph.Values[HookGraphValue]] = {}

    async def resolve(self, request: ActRequest, /) -> ResolvePortResult:
        self.requests.append(request)
        resolved = ResolvedInvocation(
            request,
            OpaqueDefinitionReference("definition"),
            ToolBindingRef("binding"),
            CanonicalArguments(request.arguments, ArgumentsDigest(b"digest")),
        )
        self._resolved = resolved
        return resolved

    async def request_authorization(self, invocation: ResolvedInvocation, /) -> AuthorizationRequestRef:
        return AuthorizationRequestRef(invocation.request.pairing, OpaqueAuthorizationHandle(b"handle"))

    def encode_interrupt(self, request_ref: AuthorizationRequestRef, /) -> bytes:
        return request_ref.handle.value

    def build_resume_input(
        self,
        interrupt: AuthorizationInterruptView,
        decision: AuthorizationDecision,
        /,
    ) -> AuthorizationInput:
        if self._resolved is None:
            raise AssertionError("resume requested before resolve")
        return AuthorizationInput.resumed(
            self._resolved,
            AuthorizationRequestRef(
                self._resolved.request.pairing,
                OpaqueAuthorizationHandle(interrupt.request_payload),
            ),
            decision,
        )

    def encode_graph_input(self, values: Graph.Values[HookGraphValue], /) -> bytes:
        self._encoded_inputs[b"act-input"] = values
        return b"act-input"

    def decode_graph_input(self, payload: bytes, /) -> Graph.Values[HookGraphValue]:
        return self._encoded_inputs[payload]

    @property
    def codec_id(self) -> str:
        return "loop-act-input"

    @property
    def codec_version(self) -> int:
        return 1

    async def execute(self, invocation: AuthorizedInvocation, /) -> ToolExecutionResult:
        identity = ToolExecutionIdentity(
            invocation.resolved.request.pairing,
            invocation.resolved.binding,
            invocation.resolved.arguments.digest,
        )
        return ToolExecutionResult(identity, OpaqueExecutionOutcome(b"outcome"))

    async def project(self, result: ToolExecutionResult, /) -> SettlementProjection:
        return SettlementProjection(result.identity, OpaqueProtocolPayload(b"projection"))

    async def write(self, request: ToolExchangeWriteRequest, /) -> ToolExchangeWriteResult:
        return ToolExchangeWriteResult(OpaqueToolExchangeReceipt(b"receipt"))


def make_observe_node(
    ports: ObservePorts,
    *,
    definition_id: str = "loop.observe",
) -> ObserveNode[Priority, SharedState, ObserveCommand]:
    admission = observe_admission()
    hook = make_hook(
        definition_id,
        ObserveHookEnvelope,
        SharedState,
        ObserveCommand,
        admission,
    )
    return ObserveNode(
        definition_id,
        queue_port=ports,
        background_task_port=ports,
        config_port=ports,
        context_port=ports,
        ack_port=ports,
        resume_port=ports,
        hook=hook,
        admission=admission,
    )


def make_think_node(
    ports: ThinkPorts,
    *,
    definition_id: str = "loop.think",
) -> ThinkNode[Priority, SharedState, ThinkCommand]:
    hook = make_hook(
        definition_id,
        cast(type[ThinkFrame[ThinkStep, SharedState]], ThinkFrame),
        SharedState,
        ThinkCommand,
    )
    return ThinkNode(
        definition_id,
        prompt_port=ports,
        context_port=ports,
        compact_port=ports,
        router_port=ports,
        inference_port=ports,
        command_port=ports,
        hook_state_type=SharedState,
        hook=hook,
    )


def make_act_node(
    ports: ActPorts,
    *,
    definition_id: str = "loop.act",
) -> ActNode[Priority, SharedState, ActCommand]:
    admission = act_admission()
    hook = make_hook(
        definition_id,
        ActHookEnvelope,
        SharedState,
        ActCommand,
        admission,
    )
    return ActNode(
        definition_id,
        resolve_port=ports,
        authorize_port=ports,
        execute_port=ports,
        settlement_port=ports,
        exchange_writer=ports,
        hook=hook,
        failure_reason=OpaqueGraphFailureReason("denied"),
        admission=admission,
    )


def valid_observe_result(sequence: int = 0) -> ObserveResult:
    before = cursor(sequence)
    after = cursor(sequence + 1)
    delivery_value = UserObservation(ObservationText("hello"))
    delivery_value = cast(UserObservation[ObservationPayload], delivery_value)
    from mote_kernel.observe.contract import ObservationDelivery

    delivery_value_as_observation = ObservationDelivery(
        DeliveryId(f"delivery-{sequence}"),
        before,
        after,
        delivery_value,
        1,
    )
    batch = ObservationBatch((delivery_value_as_observation,))
    boundary = ObservationBoundary("stream", before, after, 1)
    receipt = ContextAppendReceipt(
        batch.delivery_ids,
        boundary,
        boundary,
        "context-settlement",
        NonConfigObservationFamily.USER,
    )
    batch_receipt = ObservationBatchReceipt(
        boundary,
        boundary,
        None,
        receipt,
        DeliveryAckReference("stream", batch.delivery_ids, "batch-settlement"),
    )
    return ObserveResult(
        batch,
        ObservationKind.USER,
        batch.delivery_ids,
        CursorRange(before, after),
        BackgroundTaskSnapshot(1, ()),
        batch_receipt,
    )


def valid_observe_boundary(sequence: int = 0) -> HookResult[ObserveHookEnvelope, ObserveCommand]:
    result = valid_observe_result(sequence)
    envelope = ObserveHookEnvelope(
        ObserveHookStage.AFTER_WRITE_OBSERVATION,
        WriteObservationStageValue(result),
        SharedState(cursor=cursor(sequence)),
    )
    return HookResult(envelope, (ObserveCommand(),), GraphNodeId("write_observation"))


def valid_think_boundary() -> HookResult[ThinkFrame[ThinkStep, SharedState], ThinkCommand]:
    prompt = PromptFrame("system", "placeholder", "user")
    compacted = CompactedContext(("user",), 1)
    model = ModelBinding("provider", "model", 1)
    inference = InferenceResult("answer")
    core = ThinkCoreResult("command")
    step = CommandStep(prompt, compacted, model, inference, core)
    frame = ThinkFrame(step, SharedState())
    return cast(
        HookResult[ThinkFrame[ThinkStep, SharedState], ThinkCommand],
        HookResult(frame, (ThinkCommand(),), GraphNodeId("command")),
    )


def valid_act_request(state: SharedState | None = None) -> ActRequest:
    actual_state = SharedState() if state is None else state
    pairing = ToolPairingIdentity(
        ToolExchangeScopeId("scope"),
        ActInvocationKey("invocation"),
        ToolCallId("call"),
    )
    return ActRequest(
        pairing,
        ToolSelector("tool"),
        OpaqueArguments(b"{}"),
        CallerIdentityRef(b"caller"),
        actual_state,
    )


def valid_act_boundary() -> HookResult[ActHookEnvelope, ActCommand]:
    request = valid_act_request()
    resolved = ResolvedInvocation(
        request,
        OpaqueDefinitionReference("definition"),
        ToolBindingRef("binding"),
        CanonicalArguments(request.arguments, ArgumentsDigest(b"digest")),
    )
    execution = ToolExecutionResult(
        ToolExecutionIdentity(resolved.request.pairing, resolved.binding, resolved.arguments.digest),
        OpaqueExecutionOutcome(b"outcome"),
    )
    projection = SettlementProjection(execution.identity, OpaqueProtocolPayload(b"projection"))
    settled = ActHookEnvelope(
        ActHookStage.SETTLE,
        SettleStageValue(SettledActResult(projection, OpaqueToolExchangeReceipt(b"receipt"))),
        SharedState(),
    )
    return HookResult(settled, (ActCommand(),), GraphNodeId("settle"))


def next_state(boundary: HookResult[ObserveHookEnvelope, ObserveCommand]) -> SharedState:
    result = cast(WriteObservationStageValue, boundary.value.payload).result
    return replace(cast(SharedState, boundary.value.hook_state), cursor=result.cursor_range.after)
