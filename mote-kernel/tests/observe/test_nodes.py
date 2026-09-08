"""Deterministic tests for the Observe nested graph boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Never, Protocol, cast

import pytest

import mote_kernel.observe as observe_package
from mote_kernel.config import ConfigActivation
from mote_kernel.execution import Graph
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import (
    HookActivationRequest,
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.hooks.plan import HookPlan, HookPriorityPlan
from mote_kernel.observe import ObserveNode
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.contract import (
    AssistantBatch,
    AssistantObservation,
    Available,
    BackgroundTaskSnapshot,
    ConfigApplyResult,
    ConfigBatch,
    ConfigObservation,
    ConfigSettlementReceipt,
    Conflict,
    ContextAppendReceipt,
    DeliveryAck,
    Empty,
    GetObservationStageValue,
    HookStateProjection,
    NonConfigObservationFamily,
    Observation,
    ObservationBatch,
    ObservationBatchReceipt,
    ObservationConflict,
    ObservationDelivery,
    ObservationKind,
    ObservationPayload,
    ObservationRead,
    ObserveContractError,
    ObserveFrame,
    ObserveHookCommand,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
    ToolBatch,
    ToolObservation,
    UserBatch,
    UserObservation,
    WriteObservationStageValue,
)
from mote_kernel.observe.identity import (
    BlockingTaskRef,
    DeliveryAckReference,
    DeliveryId,
    ObservationBoundary,
    ObservationCursor,
    ObservationWait,
    ObserveHookStage,
    WaitRegistration,
)
from mote_kernel.observe.node import GetObservationNode, WriteObservationNode
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId


@dataclass(frozen=True, slots=True)
class _State(HookStateProjection):
    marker: str = "state"


@dataclass(frozen=True, slots=True)
class _Command(ObserveHookCommand):
    stage: str


class _ConfigPayload(str, ObservationPayload):
    """Immutable nominal Config payload used by the Observe test assembly."""


class _ToolPayload(str, ObservationPayload):
    """Immutable nominal Tool payload used by the Observe test assembly."""


class _UserPayload(str, ObservationPayload):
    """Immutable nominal User payload used by the Observe test assembly."""


class _AssistantPayload(str, ObservationPayload):
    """Immutable nominal Assistant payload used by the Observe test assembly."""


@dataclass(frozen=True, slots=True)
class _Priority:
    ordinal: int


def _plan() -> HookPlan[_Priority]:
    return HookPlan(
        HookPriorityPlan(_Priority(1)),
        HookPriorityPlan(_Priority(2)),
    )


class _HookInvocation:
    def __init__(self) -> None:
        self.requests: list[HookInvocationRequest[_Priority, ObserveHookEnvelope]] = []

    async def invoke(
        self,
        request: HookInvocationRequest[_Priority, ObserveHookEnvelope],
        /,
    ) -> HookStageResult[ObserveHookEnvelope, _Command]:
        self.requests.append(request)
        return HookStageResult(request.payload, (_Command(request.payload.stage.value),))


_ObserveHook = HookNode[_Priority, ObserveHookEnvelope, _State, _Command]
_ObserveGraph = ObserveNode[_Priority, _State, _Command]


class _BuilderNode(Protocol):
    @property
    def node_id(self) -> GraphNodeId: ...


class _NestedBuilderNode(_BuilderNode, Protocol):
    @property
    def graph(self) -> Graph[HookGraphValue]: ...


class _InspectableObserveGraph(_ObserveGraph):
    """Test-only adapter exposing the immutable assembly snapshot explicitly."""

    @property
    def builder_nodes(self) -> tuple[_BuilderNode, ...]:
        return self._builder_state.nodes


@dataclass
class _Ports:
    reads: list[ObservationRead] = field(default_factory=lambda: list[ObservationRead]())
    contexts: list[tuple[object, ...]] = field(default_factory=lambda: list[tuple[object, ...]]())
    configs: list[tuple[DeliveryId, ...]] = field(default_factory=lambda: list[tuple[DeliveryId, ...]]())
    acknowledgements: list[tuple[DeliveryId, ...]] = field(default_factory=lambda: list[tuple[DeliveryId, ...]]())
    waits: list[ObservationWait] = field(default_factory=lambda: list[ObservationWait]())
    snapshots: list[BackgroundTaskSnapshot] = field(default_factory=lambda: list[BackgroundTaskSnapshot]())
    settlement_snapshots: list[BackgroundTaskSnapshot] = field(default_factory=lambda: list[BackgroundTaskSnapshot]())
    _last_boundary: ObservationBoundary | None = None

    def set_boundary(self, boundary: ObservationBoundary, /) -> None:
        self._last_boundary = boundary

    async def read_after(self, _cursor: ObservationCursor, /) -> ObservationRead:
        if not self.reads:
            raise AssertionError("unexpected queue read")
        read = self.reads.pop(0)
        if type(read) is Available or type(read) is Empty:
            self._last_boundary = read.boundary
        else:
            self._last_boundary = cast(Conflict, read).boundary
        return read

    async def register_wait(self, wait: ObservationWait, /) -> WaitRegistration:
        self.waits.append(wait)
        return WaitRegistration("wait-1", wait.stream_id, wait.after_cursor, wait.observation_revision)

    async def snapshot(self, boundary: ObservationBoundary, /) -> BackgroundTaskSnapshot:
        if self.snapshots:
            return self.snapshots.pop(0)
        return BackgroundTaskSnapshot(boundary.observation_revision, ())

    async def apply(self, batch: ConfigBatch, /) -> ConfigApplyResult:
        self.configs.append(batch.delivery_ids)
        boundary = self._last_boundary
        if boundary is None:
            raise AssertionError("Config settlement lacks its read boundary")
        snapshot = self.settlement_snapshots.pop(0) if self.settlement_snapshots else None
        return ConfigApplyResult(ConfigSettlementReceipt(batch.delivery_ids, boundary, boundary, "config-1", snapshot))

    async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt:
        self.contexts.append(
            tuple(
                cast(
                    ConfigObservation[str] | ToolObservation[str] | UserObservation[str] | AssistantObservation[str],
                    delivery.payload,
                ).payload
                for delivery in batch.deliveries
            )
        )
        boundary = self._last_boundary
        if boundary is None:
            raise AssertionError("Context settlement lacks its read boundary")
        snapshot = self.settlement_snapshots.pop(0) if self.settlement_snapshots else None
        family = (
            NonConfigObservationFamily.TOOL
            if type(batch) is ToolBatch
            else NonConfigObservationFamily.USER
            if type(batch) is UserBatch
            else NonConfigObservationFamily.ASSISTANT
        )
        return ContextAppendReceipt(batch.delivery_ids, boundary, boundary, "context-1", family, snapshot)

    async def acknowledge(
        self,
        delivery_ids: tuple[DeliveryId, ...],
        receipt: ObservationBatchReceipt,
        /,
    ) -> DeliveryAck:
        self.acknowledgements.append(delivery_ids)
        return DeliveryAck(receipt.ack_reference)

    def encode_graph_input(self, values: Graph.Values[HookGraphValue], /) -> bytes:
        request = cast(ObserveRequest[_State], values["request"])
        return f"{request.cursor.stream_id}|{request.cursor.sequence}|{request.hook_state.marker}".encode()

    def decode_graph_input(self, payload: bytes, /) -> Graph.Values[HookGraphValue]:
        stream, sequence, marker = payload.decode().split("|")
        return Graph.values(request=ObserveRequest(ObservationCursor(stream, int(sequence)), _State(marker)))

    @property
    def codec_id(self) -> str:
        return "observe-test-input"

    @property
    def codec_version(self) -> int:
        return 1


def _cursor(sequence: int) -> ObservationCursor:
    return ObservationCursor("stream", sequence)


def _delivery(sequence: int, payload: Observation, delivery_id: str) -> ObservationDelivery[Observation]:
    if type(payload) is ConfigObservation:
        payload = ConfigObservation(_ConfigPayload(str(cast(ConfigObservation[object], payload).payload)))
    elif type(payload) is ToolObservation:
        payload = ToolObservation(_ToolPayload(str(cast(ToolObservation[object], payload).payload)))
    elif type(payload) is UserObservation:
        payload = UserObservation(_UserPayload(str(cast(UserObservation[object], payload).payload)))
    elif type(payload) is AssistantObservation:
        payload = AssistantObservation(_AssistantPayload(str(cast(AssistantObservation[object], payload).payload)))
    return ObservationDelivery(
        DeliveryId(delivery_id),
        _cursor(sequence),
        _cursor(sequence + 1),
        payload,
        1,
    )


def _available(*deliveries: ObservationDelivery[Observation]) -> Available:
    batch = ObservationBatch(tuple(deliveries))
    return Available(batch, ObservationBoundary("stream", deliveries[0].cursor_before, deliveries[-1].cursor_after, 1))


def _conflict(*deliveries: ObservationDelivery[Observation]) -> Conflict:
    batch = ObservationBatch(tuple(deliveries))
    conflict = ObservationConflict(batch.deliveries, batch.non_config_families)
    boundary = ObservationBoundary("stream", deliveries[0].cursor_before, deliveries[-1].cursor_after, 1)
    return Conflict(conflict, boundary)


def _empty(sequence: int = 0) -> Empty:
    cursor = _cursor(sequence)
    boundary = ObservationBoundary("stream", cursor, cursor, 1)
    return Empty(ObservationWait("stream", cursor, 1, "delivery"), boundary)


def _hook(
    definition_id: str,
    invocation: _HookInvocation,
    observe_admission: ObservePayloadAdmission | None = None,
) -> _ObserveHook:
    transition_admission = _admission() if observe_admission is None else observe_admission
    admission = HookPayloadAdmission(
        _Priority,
        ObserveHookEnvelope,
        _State,
        _Command,
        transition_admission,
    )
    slot = HookSlotId(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(1),
        GraphNodeId("hook"),
        HookStage.AFTER_NODE,
    )
    return HookNode(slot, _plan(), invocation, admission)


def _observe(ports: _Ports, invocation: _HookInvocation) -> _InspectableObserveGraph:
    admission = _admission()
    return _observe_with_hook(ports, invocation, _hook("observe.test", invocation, admission), admission)


def _observe_with_hook(
    ports: _Ports,
    invocation: _HookInvocation,
    hook: _ObserveHook,
    admission: ObservePayloadAdmission | None = None,
) -> _InspectableObserveGraph:
    if admission is None:
        transition_admission = hook.payload_admission.transition_admission if type(hook) is HookNode else None
        admission = transition_admission if type(transition_admission) is ObservePayloadAdmission else _admission()
    return _InspectableObserveGraph(
        "observe.test",
        queue_port=ports,
        background_task_port=ports,
        config_port=ports,
        context_port=ports,
        ack_port=ports,
        resume_port=ports,
        hook=hook,
        admission=admission,
    )


def _request(sequence: int = 0) -> ObserveRequest[_State]:
    return ObserveRequest(_cursor(sequence), _State())


def _observation_payload(observation: Observation) -> object:
    if type(observation) is ConfigObservation:
        return cast(ConfigObservation[object], observation).payload
    if type(observation) is ToolObservation:
        return cast(ToolObservation[object], observation).payload
    if type(observation) is UserObservation:
        return cast(UserObservation[object], observation).payload
    return cast(AssistantObservation[object], observation).payload


# Public test support names keep the graph/admission suites independent from
# this module's implementation-private fixture spelling.
ObserveTestState = _State
ObserveTestCommand = _Command
ObserveTestConfigPayload = _ConfigPayload
ObserveTestToolPayload = _ToolPayload
ObserveTestUserPayload = _UserPayload
ObserveTestAssistantPayload = _AssistantPayload
ObserveTestPriority = _Priority
ObserveTestPorts = _Ports
ObserveTestHookInvocation = _HookInvocation
make_cursor = _cursor
make_delivery = _delivery
make_available = _available
make_conflict = _conflict
make_empty = _empty
make_hook = _hook
make_observe = _observe
make_observe_with_hook = _observe_with_hook
make_request = _request


def builder_node_ids(observe: _InspectableObserveGraph) -> tuple[str, ...]:
    # This helper intentionally inspects the private builder snapshot: the
    # test locks the assembly invariant without adding a production
    # introspection API solely for tests.
    return tuple(str(candidate.node_id) for candidate in observe.builder_nodes)


def builder_hook_is_shared(observe: _InspectableObserveGraph) -> bool:
    candidate = cast(_NestedBuilderNode, observe.builder_nodes[1])
    return candidate.graph is observe.hook


@pytest.mark.asyncio
async def test_available_user_batch_is_fifo_and_passes_shared_hook_twice() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(
        _available(
            _delivery(0, UserObservation("u1"), "d1"),
            _delivery(1, UserObservation("u2"), "d2"),
        )
    )
    observe = _observe(ports, invocation)

    result = await observe.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    hook_result = cast(HookResult[ObserveHookEnvelope, _Command], result.outputs["result"])
    assert len(invocation.requests) == 4
    assert tuple(request.payload.stage for request in invocation.requests[:2]) == (
        ObserveHookStage.AFTER_GET_OBSERVATION,
        ObserveHookStage.AFTER_GET_OBSERVATION,
    )
    assert tuple(request.payload.stage for request in invocation.requests[2:]) == (
        ObserveHookStage.AFTER_WRITE_OBSERVATION,
        ObserveHookStage.AFTER_WRITE_OBSERVATION,
    )
    assert hook_result.value.stage.value == "after_write_observation"
    assert hook_result.node_id == GraphNodeId("write_observation")
    assert result.state.completion_route == "write_observation"
    payload = hook_result.value.payload
    assert isinstance(payload, WriteObservationStageValue)
    assert payload.result.current_state is ObservationKind.USER
    assert ports.contexts == [("u1", "u2")]
    assert ports.configs == []
    await observe.acknowledge(payload.result)
    assert ports.acknowledgements == [(DeliveryId("d1"), DeliveryId("d2"))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observation", "expected_kind"),
    [
        (ConfigObservation("config"), ObservationKind.CONFIG),
        (ToolObservation("tool"), ObservationKind.TOOL),
        (UserObservation("user"), ObservationKind.USER),
        (AssistantObservation("assistant"), ObservationKind.ASSISTANT),
    ],
    ids=["config", "tool", "user", "assistant"],
)
async def test_each_observation_family_returns_its_four_value_kind(
    observation: Observation,
    expected_kind: ObservationKind,
) -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(_available(_delivery(0, observation, "delivery-1")))

    result = await _observe(ports, invocation).run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    hook_result = cast(HookResult[ObserveHookEnvelope, _Command], result.outputs["result"])
    stage = hook_result.value.payload
    assert isinstance(stage, WriteObservationStageValue)
    assert stage.result.current_state is expected_kind
    if expected_kind is ObservationKind.CONFIG:
        assert ports.configs == [(DeliveryId("delivery-1"),)]
        assert ports.contexts == []
    else:
        assert ports.configs == []
        assert ports.contexts == [(_observation_payload(observation),)]


@pytest.mark.asyncio
async def test_config_batch_is_fifo_and_only_its_effective_value_is_last() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(
        _available(
            _delivery(0, ConfigObservation("first"), "config-1"),
            _delivery(1, ConfigObservation("second"), "config-2"),
        )
    )

    result = await _observe(ports, invocation).run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    output = cast(HookResult[ObserveHookEnvelope, _Command], result.outputs["result"])
    stage = output.value.payload
    assert isinstance(stage, WriteObservationStageValue)
    assert stage.result.current_state is ObservationKind.CONFIG
    assert ports.configs == [(DeliveryId("config-1"), DeliveryId("config-2"))]
    assert ports.contexts == []


@pytest.mark.asyncio
async def test_conflicting_non_config_families_fail_closed_before_task_or_settlement_ports() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(
        _conflict(
            _delivery(0, ToolObservation("tool"), "tool-1"),
            _delivery(1, UserObservation("user"), "user-1"),
        )
    )

    result = await _observe(ports, invocation).run(Graph.values(request=_request()))

    assert isinstance(result, Graph.FailedResult)
    assert "mutually exclusive" in result.failures[0].failure
    assert ports.contexts == []
    assert ports.configs == []
    assert ports.acknowledgements == []
    assert ports.snapshots == []
    assert invocation.requests == []


def test_observe_frame_rejects_a_conflicting_batch_even_when_constructed_directly() -> None:
    deliveries = (
        _delivery(0, ToolObservation("tool"), "tool-1"),
        _delivery(1, UserObservation("user"), "user-1"),
    )
    batch = ObservationBatch(deliveries)
    boundary = ObservationBoundary("stream", _cursor(0), _cursor(2), 1)
    snapshot = BackgroundTaskSnapshot(1, ())

    with pytest.raises(ObserveContractError, match="conflicting"):
        ObserveFrame(batch, boundary, snapshot)


def test_admission_revalidates_a_forged_context_batch_before_use() -> None:
    delivery = _delivery(0, UserObservation("user"), "user-1")
    forged = object.__new__(ToolBatch)
    object.__setattr__(forged, "deliveries", (delivery,))

    with pytest.raises(ObserveContractError, match="different observation family"):
        _admission().admit_context_batch(forged)


@pytest.mark.asyncio
async def test_background_task_snapshot_is_carried_to_the_parent_result_without_a_second_query() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    task = BlockingTaskRef("task", 1, "wait-for-result")
    snapshot = BackgroundTaskSnapshot(1, (task,))
    ports.snapshots.append(snapshot)
    ports.reads.append(_available(_delivery(0, AssistantObservation("done"), "assistant-1")))

    result = await _observe(ports, invocation).run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    output = cast(HookResult[ObserveHookEnvelope, _Command], result.outputs["result"])
    stage = output.value.payload
    assert isinstance(stage, WriteObservationStageValue)
    assert stage.result.background_task_snapshot == snapshot
    assert stage.result.background_task_snapshot.has_blocking_tasks
    assert len(ports.snapshots) == 0


@pytest.mark.asyncio
async def test_task_revision_mismatch_fails_before_entering_shared_hook() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(_available(_delivery(0, UserObservation("user"), "user-1")))
    ports.snapshots.append(BackgroundTaskSnapshot(2, ()))

    with pytest.raises(ObserveContractError, match="revision"):
        await _observe(ports, invocation).run(Graph.values(request=_request()))

    assert invocation.requests == []
    assert ports.contexts == []
    assert ports.configs == []


@pytest.mark.asyncio
async def test_settlement_cannot_replace_the_snapshot_at_the_same_boundary() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(_available(_delivery(0, ConfigObservation("config"), "config-1")))
    ports.snapshots.append(BackgroundTaskSnapshot(1, ()))
    ports.settlement_snapshots.append(BackgroundTaskSnapshot(1, (BlockingTaskRef("task", 1, "wait"),)))

    with pytest.raises(ObserveContractError, match="without advancing its boundary"):
        await _observe(ports, invocation).run(Graph.values(request=_request()))

    assert ports.acknowledgements == []


@pytest.mark.asyncio
async def test_interleaved_config_and_user_projects_each_fifo_subset() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(
        _available(
            _delivery(0, ConfigObservation("c1"), "c1"),
            _delivery(1, UserObservation("u1"), "u1"),
            _delivery(2, ConfigObservation("c2"), "c2"),
            _delivery(3, UserObservation("u2"), "u2"),
        )
    )
    observe = _observe(ports, invocation)
    result = await observe.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    output = cast(HookResult[ObserveHookEnvelope, _Command], result.outputs["result"])
    payload = output.value.payload
    assert isinstance(payload, WriteObservationStageValue)
    assert payload.result.current_state is ObservationKind.USER
    assert ports.configs == [(DeliveryId("c1"), DeliveryId("c2"))]
    assert ports.contexts == [("u1", "u2")]


@pytest.mark.asyncio
async def test_empty_queue_interrupts_and_resume_rereads_wait_cursor() -> None:
    ports = _Ports([_empty()])
    invocation = _HookInvocation()
    observe = _observe(ports, invocation)
    awaiting = await observe.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    assert len(awaiting.interrupts) == 1
    wait = ObservationWait.decode(awaiting.interrupts[0].request_payload)
    assert wait.after_cursor == _cursor(0)

    ports.reads.append(_available(_delivery(0, UserObservation("wake"), "wake-1")))
    action = observe.resume_observation(
        awaiting=awaiting,
        interrupt_id=str(awaiting.interrupts[0].interrupt_id),
        hook_state=_State(),
    )
    resumed = await observe.run(state=awaiting.state, continuation=awaiting.continuation, resume=(action,))

    assert isinstance(resumed, Graph.CompletedResult)
    assert ports.contexts == [("wake",)]
    assert ports.waits == [_empty().wait]


@pytest.mark.asyncio
async def test_resume_rejects_unknown_interrupts_and_malformed_wait_payloads() -> None:
    ports = _Ports([_empty()])
    invocation = _HookInvocation()
    observe = _observe(ports, invocation)
    awaiting = await observe.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)

    with pytest.raises(ObserveContractError, match="exactly one"):
        observe.resume_observation(awaiting=awaiting, interrupt_id="missing", hook_state=_State())

    interrupt = awaiting.interrupts[0]
    object.__setattr__(interrupt, "request_payload", b"not-an-observation-wait")
    with pytest.raises(ObserveContractError, match="valid ObservationWait"):
        observe.resume_observation(
            awaiting=awaiting,
            interrupt_id=str(interrupt.interrupt_id),
            hook_state=_State(),
        )


def test_public_observe_package_exports_only_the_graph_entry_point() -> None:
    assert observe_package.__all__ == ["ObserveNode"]
    assert observe_package.ObserveNode is ObserveNode
    assert not hasattr(observe_package, "ObserveRequest")


def test_empty_boundary_cannot_advance_without_a_delivery() -> None:
    cursor = _cursor(0)
    boundary = ObservationBoundary("stream", cursor, _cursor(1), 1)
    with pytest.raises(ObserveContractError, match="cannot advance"):
        Empty(ObservationWait("stream", _cursor(1), 1, "delivery"), boundary)


def test_resume_capability_is_required_for_a_durable_observe_graph() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    with pytest.raises(ObserveContractError, match="ObservationResumePort"):
        ObserveNode(
            "observe.missing-resume",
            queue_port=ports,
            background_task_port=ports,
            config_port=ports,
            context_port=ports,
            ack_port=ports,
            resume_port=cast(Never, None),
            hook=_hook("observe.missing-resume", invocation),
            admission=_admission(),
        )


@pytest.mark.parametrize(
    ("definition_id", "version", "message"),
    [
        (" bad", 1, "definition_id"),
        ("observe.test", 0, "version"),
    ],
)
def test_observe_rejects_noncanonical_graph_identity_before_assembly(
    definition_id: str,
    version: int,
    message: str,
) -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    with pytest.raises(ObserveContractError, match=message):
        ObserveNode(
            definition_id,
            version=version,
            queue_port=ports,
            background_task_port=ports,
            config_port=ports,
            context_port=ports,
            ack_port=ports,
            resume_port=ports,
            hook=_hook("observe.test", invocation),
            admission=_admission(),
        )


def test_observe_rejects_a_forged_hook_slot_outer_type() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    hook = _hook("observe.test", invocation)
    object.__setattr__(hook, "_slot", cast(Never, object()))

    with pytest.raises(ObserveContractError, match="HookSlotId"):
        _observe_with_hook(ports, invocation, hook)


def test_observe_rejects_a_forged_hook_payload_admission_outer_type() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    hook = _hook("observe.test", invocation)
    object.__setattr__(hook, "_payload_admission", cast(Never, object()))

    with pytest.raises(ObserveContractError, match="HookPayloadAdmission"):
        _observe_with_hook(ports, invocation, hook, _admission())


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("value", "value type"),
        ("state", "state type"),
        ("command", "command type"),
        ("transition", "for transitions"),
    ],
)
def test_observe_rejects_wrong_shared_hook_concrete_bindings_before_graph_assembly(
    fault: str,
    message: str,
) -> None:
    class OtherValue(HookGraphValue):
        pass

    @dataclass(frozen=True, slots=True)
    class OtherState(HookStateProjection):
        pass

    @dataclass(frozen=True, slots=True)
    class OtherCommand(ObserveHookCommand):
        pass

    ports = _Ports()
    invocation = _HookInvocation()
    admission = _admission()
    hook = _hook("observe.test", invocation, admission)
    value_type = cast(type[ObserveHookEnvelope], OtherValue) if fault == "value" else ObserveHookEnvelope
    state_type = cast(type[_State], OtherState) if fault == "state" else _State
    command_type = cast(type[_Command], OtherCommand) if fault == "command" else _Command
    hook_admission = HookPayloadAdmission(
        _Priority,
        value_type,
        state_type,
        command_type,
        _admission() if fault == "transition" else admission,
    )
    object.__setattr__(hook, "_payload_admission", hook_admission)

    with pytest.raises(ObserveContractError, match=message):
        _observe_with_hook(ports, invocation, hook, admission)


def test_observe_captures_resume_codec_metadata_exactly_once() -> None:
    class SingleReadMetadataPorts(_Ports):
        def __init__(self) -> None:
            super().__init__()
            self.codec_id_reads = 0
            self.codec_version_reads = 0

        @property
        def codec_id(self) -> str:
            self.codec_id_reads += 1
            if self.codec_id_reads > 1:
                raise AssertionError("codec_id was read more than once")
            return "observe-single-read"

        @property
        def codec_version(self) -> int:
            self.codec_version_reads += 1
            if self.codec_version_reads > 1:
                raise AssertionError("codec_version was read more than once")
            return 1

    ports = SingleReadMetadataPorts()
    invocation = _HookInvocation()

    _observe_with_hook(ports, invocation, _hook("observe.test", invocation))

    assert ports.codec_id_reads == 1
    assert ports.codec_version_reads == 1


def test_observe_rejects_a_hook_subclass_before_graph_assembly() -> None:
    class HookSubclass(_ObserveHook):
        pass

    subclass = cast(_ObserveHook, object.__new__(HookSubclass))
    with pytest.raises(ObserveContractError, match="shared HookNode"):
        _observe_with_hook(_Ports(), _HookInvocation(), subclass)


def _admission() -> ObservePayloadAdmission:
    return ObservePayloadAdmission(
        _State,
        _Command,
        _ConfigPayload,
        _ToolPayload,
        _UserPayload,
        _AssistantPayload,
    )


def _get_node(ports: _Ports) -> GetObservationNode[_State, _Command]:
    return GetObservationNode(ports, ports, _admission())


def _write_node(ports: _Ports) -> WriteObservationNode[_State, _Command]:
    return WriteObservationNode(ports, ports, _admission())


def _after_get_result(available: Available) -> HookResult[ObserveHookEnvelope, _Command]:
    frame = ObserveFrame(available.batch, available.boundary, BackgroundTaskSnapshot(1, ()))
    envelope = ObserveHookEnvelope(
        ObserveHookStage.AFTER_GET_OBSERVATION,
        GetObservationStageValue(frame),
        _State(),
    )
    return HookResult(envelope, (), GraphNodeId("get_observation"))


@pytest.mark.asyncio
async def test_get_observation_node_builds_the_first_hook_request() -> None:
    ports = _Ports()
    ports.reads.append(_available(_delivery(0, UserObservation("user"), "user")))
    node = _get_node(ports)
    request_input = _request()

    request = await node(ConfigActivation(request_input))

    assert isinstance(request, HookActivationRequest)
    assert request.node_id == GraphNodeId("get_observation")
    assert request.state is request_input.hook_state
    assert request.value.stage is ObserveHookStage.AFTER_GET_OBSERVATION
    assert isinstance(request.value.payload, GetObservationStageValue)
    assert request.value.payload.frame.batch.delivery_ids == (DeliveryId("user"),)
    assert request.value.hook_state is request.state
    assert ports.contexts == []
    assert ports.configs == []


@pytest.mark.asyncio
async def test_get_observation_node_returns_a_durable_interrupt_for_empty_queue() -> None:
    ports = _Ports()
    empty = _empty(4)
    ports.reads.append(empty)

    outcome = await _get_node(ports)(ConfigActivation(_request(4)))

    assert isinstance(outcome, Graph.InterruptOutcome)
    assert ObservationWait.decode(outcome.request_payload) == empty.wait
    assert ports.waits == [empty.wait]


@pytest.mark.asyncio
async def test_get_observation_node_turns_a_queue_conflict_into_a_failure() -> None:
    ports = _Ports()
    conflict = _conflict(
        _delivery(0, ToolObservation("tool"), "tool"),
        _delivery(1, AssistantObservation("assistant"), "assistant"),
    )
    ports.reads.append(conflict)

    outcome = await _get_node(ports)(ConfigActivation(_request()))

    assert isinstance(outcome, Graph.FailureOutcome)
    assert "tool,assistant" in outcome.failure
    assert ports.snapshots == []


@pytest.mark.asyncio
async def test_write_observation_node_builds_the_terminal_hook_request() -> None:
    ports = _Ports()
    delivery = _delivery(0, AssistantObservation("assistant"), "assistant")
    available = _available(delivery)
    ports.set_boundary(available.boundary)
    frame = ObserveFrame(available.batch, available.boundary, BackgroundTaskSnapshot(1, ()))
    envelope = ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, GetObservationStageValue(frame), _State())
    hook_result: HookResult[ObserveHookEnvelope, _Command] = HookResult(
        envelope,
        (_Command("after-get"),),
        GraphNodeId("get_observation"),
    )

    activation = await _write_node(ports)(ConfigActivation(hook_result))
    assert type(activation) is ConfigActivation
    request = activation.value

    assert request.node_id == GraphNodeId("write_observation")
    assert request.value.stage is ObserveHookStage.AFTER_WRITE_OBSERVATION
    assert isinstance(request.value.payload, WriteObservationStageValue)
    assert request.value.payload.result.current_state is ObservationKind.ASSISTANT
    assert request.value.hook_state is envelope.hook_state
    assert ports.contexts == [("assistant",)]
    assert ports.configs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observation", "expected_port"),
    [
        (ToolObservation("tool"), "context"),
        (UserObservation("user"), "context"),
        (AssistantObservation("assistant"), "context"),
        (ConfigObservation("config"), "config"),
    ],
)
async def test_write_observation_node_uses_exactly_one_settlement_port(
    observation: Observation,
    expected_port: str,
) -> None:
    ports = _Ports()
    available = _available(_delivery(0, observation, "delivery"))
    ports.set_boundary(available.boundary)
    frame = ObserveFrame(available.batch, available.boundary, BackgroundTaskSnapshot(1, ()))
    envelope = ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, GetObservationStageValue(frame), _State())
    hook_result = HookResult(envelope, (), GraphNodeId("get_observation"))

    await _write_node(ports)(ConfigActivation(hook_result))

    assert ("config" if ports.configs else "context") == expected_port
    assert len(ports.configs) + len(ports.contexts) == 1


@pytest.mark.asyncio
async def test_write_observation_node_preserves_config_and_context_subsets_in_one_activation() -> None:
    ports = _Ports()
    available = _available(
        _delivery(0, ConfigObservation("config"), "config"),
        _delivery(1, UserObservation("user"), "user"),
    )
    ports.set_boundary(available.boundary)
    frame = ObserveFrame(available.batch, available.boundary, BackgroundTaskSnapshot(1, ()))
    envelope = ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, GetObservationStageValue(frame), _State())

    await _write_node(ports)(ConfigActivation(HookResult(envelope, (), GraphNodeId("get_observation"))))

    assert ports.configs == [(DeliveryId("config"),)]
    assert ports.contexts == [("user",)]


@pytest.mark.asyncio
async def test_write_observation_rejects_a_context_receipt_for_another_family() -> None:
    class WrongFamilyPorts(_Ports):
        async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt:
            receipt = await super().append(batch)
            return ContextAppendReceipt(
                receipt.delivery_ids,
                receipt.read_boundary,
                receipt.settlement_boundary,
                receipt.settlement_id,
                NonConfigObservationFamily.ASSISTANT,
                receipt.background_task_snapshot,
            )

    ports = WrongFamilyPorts()
    available = _available(_delivery(0, UserObservation("user"), "user"))
    ports.set_boundary(available.boundary)
    frame = ObserveFrame(available.batch, available.boundary, BackgroundTaskSnapshot(1, ()))
    envelope = ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, GetObservationStageValue(frame), _State())

    with pytest.raises(ObserveContractError, match="family does not match"):
        await _write_node(ports)(ConfigActivation(HookResult(envelope, (), GraphNodeId("get_observation"))))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "observation", "message"),
    [
        ("config-boundary", ConfigObservation("config"), "Config settlement receipt read boundary"),
        ("config-deliveries", ConfigObservation("config"), "complete Config batch"),
        ("context-boundary", UserObservation("user"), "Context settlement receipt read boundary"),
        ("context-deliveries", UserObservation("user"), "complete Context batch"),
    ],
)
async def test_write_observation_rejects_incomplete_or_misbound_child_receipts(
    fault: str,
    observation: Observation,
    message: str,
) -> None:
    class WrongReceiptPorts(_Ports):
        async def apply(self, batch: ConfigBatch, /) -> ConfigApplyResult:
            applied = await super().apply(batch)
            receipt = applied.receipt
            if fault == "config-boundary":
                boundary = receipt.read_boundary
                wrong = ObservationBoundary(
                    boundary.stream_id,
                    boundary.cursor_before,
                    boundary.cursor_after,
                    boundary.observation_revision + 1,
                )
                return ConfigApplyResult(
                    ConfigSettlementReceipt(receipt.delivery_ids, wrong, wrong, receipt.settlement_id)
                )
            if fault == "config-deliveries":
                return ConfigApplyResult(
                    ConfigSettlementReceipt(
                        (DeliveryId("other"),),
                        receipt.read_boundary,
                        receipt.settlement_boundary,
                        receipt.settlement_id,
                    )
                )
            return applied

        async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt:
            receipt = await super().append(batch)
            if fault == "context-boundary":
                boundary = receipt.read_boundary
                wrong = ObservationBoundary(
                    boundary.stream_id,
                    boundary.cursor_before,
                    boundary.cursor_after,
                    boundary.observation_revision + 1,
                )
                return ContextAppendReceipt(
                    receipt.delivery_ids,
                    wrong,
                    wrong,
                    receipt.settlement_id,
                    receipt.family,
                )
            if fault == "context-deliveries":
                return ContextAppendReceipt(
                    (DeliveryId("other"),),
                    receipt.read_boundary,
                    receipt.settlement_boundary,
                    receipt.settlement_id,
                    receipt.family,
                )
            return receipt

    ports = WrongReceiptPorts()
    available = _available(_delivery(0, observation, "delivery"))
    ports.set_boundary(available.boundary)

    with pytest.raises(ObserveContractError, match=message):
        await _write_node(ports)(ConfigActivation(_after_get_result(available)))


@pytest.mark.asyncio
async def test_write_observation_rejects_different_config_and_context_settlement_boundaries() -> None:
    class DivergentBoundaryPorts(_Ports):
        async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt:
            receipt = await super().append(batch)
            read = receipt.read_boundary
            successor = ObservationBoundary(
                read.stream_id,
                read.cursor_after,
                read.cursor_after,
                read.observation_revision + 1,
            )
            return ContextAppendReceipt(
                receipt.delivery_ids,
                read,
                successor,
                receipt.settlement_id,
                receipt.family,
                BackgroundTaskSnapshot(successor.observation_revision, ()),
            )

    ports = DivergentBoundaryPorts()
    available = _available(
        _delivery(0, ConfigObservation("config"), "config"),
        _delivery(1, UserObservation("user"), "user"),
    )
    ports.set_boundary(available.boundary)

    with pytest.raises(ObserveContractError, match="disagree on settlement boundary"):
        await _write_node(ports)(ConfigActivation(_after_get_result(available)))


@pytest.mark.asyncio
async def test_write_observation_rejects_conflicting_successor_task_snapshots() -> None:
    class ConflictingSnapshotPorts(_Ports):
        def _successor(self) -> ObservationBoundary:
            boundary = self._last_boundary
            if boundary is None:
                raise AssertionError("missing read boundary")
            return ObservationBoundary(
                boundary.stream_id,
                boundary.cursor_after,
                boundary.cursor_after,
                boundary.observation_revision + 1,
            )

        async def apply(self, batch: ConfigBatch, /) -> ConfigApplyResult:
            successor = self._successor()
            read = cast(ObservationBoundary, self._last_boundary)
            return ConfigApplyResult(
                ConfigSettlementReceipt(
                    batch.delivery_ids,
                    read,
                    successor,
                    "config",
                    BackgroundTaskSnapshot(2, (BlockingTaskRef("config-task", 1, "wait"),)),
                )
            )

        async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt:
            successor = self._successor()
            read = cast(ObservationBoundary, self._last_boundary)
            return ContextAppendReceipt(
                batch.delivery_ids,
                read,
                successor,
                "context",
                NonConfigObservationFamily.USER,
                BackgroundTaskSnapshot(2, (BlockingTaskRef("context-task", 1, "wait"),)),
            )

    ports = ConflictingSnapshotPorts()
    available = _available(
        _delivery(0, ConfigObservation("config"), "config"),
        _delivery(1, UserObservation("user"), "user"),
    )
    ports.set_boundary(available.boundary)

    with pytest.raises(ObserveContractError, match="conflicting task snapshots"):
        await _write_node(ports)(ConfigActivation(_after_get_result(available)))


@pytest.mark.asyncio
async def test_write_observation_bounds_the_composed_settlement_identity() -> None:
    class LongSettlementIdPorts(_Ports):
        async def apply(self, batch: ConfigBatch, /) -> ConfigApplyResult:
            applied = await super().apply(batch)
            receipt = applied.receipt
            return ConfigApplyResult(
                ConfigSettlementReceipt(
                    receipt.delivery_ids,
                    receipt.read_boundary,
                    receipt.settlement_boundary,
                    "x" * 256,
                )
            )

    ports = LongSettlementIdPorts()
    available = _available(_delivery(0, ConfigObservation("config"), "config"))
    ports.set_boundary(available.boundary)

    with pytest.raises(ObserveContractError, match="settlement identity exceeds"):
        await _write_node(ports)(ConfigActivation(_after_get_result(available)))


@pytest.mark.asyncio
async def test_write_observation_node_rejects_a_non_get_hook_envelope_before_writing() -> None:
    ports = _Ports()
    available = _available(_delivery(0, UserObservation("user"), "user"))
    bad = ObserveHookEnvelope(
        ObserveHookStage.AFTER_WRITE_OBSERVATION,
        WriteObservationStageValue(
            ObserveResult(
                ObservationKind.USER,
                available.batch.delivery_ids,
                available.boundary.cursor_range,
                BackgroundTaskSnapshot(1, ()),
                ObservationBatchReceipt(
                    available.boundary,
                    available.boundary,
                    None,
                    ContextAppendReceipt(
                        available.batch.delivery_ids,
                        available.boundary,
                        available.boundary,
                        "context",
                        NonConfigObservationFamily.USER,
                    ),
                    DeliveryAckReference("stream", available.batch.delivery_ids, "ack"),
                ),
            )
        ),
        _State(),
    )

    with pytest.raises(ObserveContractError, match="after-get"):
        await _write_node(ports)(ConfigActivation(HookResult(bad, (), GraphNodeId("write_observation"))))
    assert ports.configs == []
    assert ports.contexts == []


@pytest.mark.asyncio
async def test_observe_acknowledgement_is_explicit_and_validates_provider_reference() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(_available(_delivery(0, UserObservation("user"), "user")))
    observe = _observe(ports, invocation)
    completed = await observe.run(Graph.values(request=_request()))
    assert isinstance(completed, Graph.CompletedResult)
    output = cast(HookResult[ObserveHookEnvelope, _Command], completed.outputs["result"])
    stage = cast(WriteObservationStageValue, output.value.payload)

    assert ports.acknowledgements == []
    ack = await observe.acknowledge(stage.result)
    assert ack.reference == stage.result.observation_receipt.ack_reference
    assert ports.acknowledgements == [(DeliveryId("user"),)]


@pytest.mark.asyncio
async def test_observe_rejects_an_ack_provider_response_for_a_different_reference() -> None:
    class WrongAckPorts(_Ports):
        async def acknowledge(
            self,
            delivery_ids: tuple[DeliveryId, ...],
            receipt: ObservationBatchReceipt,
            /,
        ) -> DeliveryAck:
            self.acknowledgements.append(delivery_ids)
            return DeliveryAck(
                DeliveryAckReference("stream", (DeliveryId("other"),), receipt.ack_reference.settlement_id)
            )

    ports = WrongAckPorts()
    invocation = _HookInvocation()
    ports.reads.append(_available(_delivery(0, UserObservation("user"), "user")))
    observe = _observe(ports, invocation)
    completed = await observe.run(Graph.values(request=_request()))
    assert isinstance(completed, Graph.CompletedResult)
    output = cast(HookResult[ObserveHookEnvelope, _Command], completed.outputs["result"])
    stage = cast(WriteObservationStageValue, output.value.payload)
    with pytest.raises(ObserveContractError, match="does not match observation receipt"):
        await observe.acknowledge(stage.result)


@pytest.mark.asyncio
async def test_get_observation_node_rejects_a_wrong_request_outer_type_and_does_not_read_queue() -> None:
    ports = _Ports()
    with pytest.raises(ObserveContractError, match="exact ObserveRequest"):
        await _get_node(ports)(ConfigActivation(cast(ObserveRequest[_State], _State())))
    assert ports.reads == []


@pytest.mark.asyncio
async def test_write_observation_node_rejects_a_wrong_hook_result_outer_type() -> None:
    ports = _Ports()
    with pytest.raises(ObserveContractError, match="exact HookResult"):
        await _write_node(ports)(ConfigActivation(cast(HookResult[ObserveHookEnvelope, _Command], _State())))
    assert ports.configs == []
    assert ports.contexts == []


@pytest.mark.asyncio
async def test_get_observation_node_propagates_cancellation_while_reading() -> None:
    class BlockingQueue(_Ports):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def read_after(self, cursor: ObservationCursor, /) -> ObservationRead:
            self.entered.set()
            await self.release.wait()
            return await super().read_after(cursor)

    ports = BlockingQueue()
    ports.reads.append(_available(_delivery(0, UserObservation("user"), "user")))
    task = asyncio.create_task(_get_node(ports)(ConfigActivation(_request())))
    await asyncio.wait_for(ports.entered.wait(), timeout=1)
    task.cancel("cancelled observe read")
    with pytest.raises(asyncio.CancelledError):
        await task
