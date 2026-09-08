"""Config snapshot, projection, and recovery boundary tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace
from typing import cast

import pytest
from tests.loop.support import (
    ActCommand,
    ActPorts,
    ObservationText,
    ObserveCommand,
    ObservePorts,
    PassThroughInvocation,
    Priority,
    SharedState,
    ThinkCommand,
    ThinkPayload,
    ThinkPorts,
    act_admission,
    available,
    cursor,
    delivery,
    hook_plan,
    next_state,
    observe_admission,
    valid_act_request,
)

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.authorize import AuthorizeNode
from mote_kernel.act.config import (
    ActConfig,
    AuthorizeBinding,
    ExecuteBinding,
    ResolveBinding,
    SettleBinding,
)
from mote_kernel.act.contract import (
    ActHookEnvelope,
    ActRequest,
    Allow,
    AuthorizationDecision,
    AuthorizationInput,
    AuthorizationInterruptView,
    AuthorizationRequestRef,
    AuthorizedInvocation,
    AuthorizeNodeInput,
    ExecuteNodeInput,
    OpaqueGraphFailureReason,
    ResolvedInvocation,
    ResolvePortResult,
    SettlementProjection,
    SettleNodeInput,
    ToolExchangeWriteRequest,
    ToolExchangeWriteResult,
    ToolExecutionResult,
)
from mote_kernel.act.execute import ExecuteNode
from mote_kernel.act.node import ActNode
from mote_kernel.act.settle import SettleNode
from mote_kernel.config import (
    Config,
    ConfigActivation,
    ConfigContractError,
    ConfigResolver,
    ConfigSelector,
    ConfigSlice,
    ConfigSnapshot,
    ConfigSnapshotDigest,
    ConfigSnapshotKey,
    ConfigSnapshotStore,
    load_config_snapshot,
    recover_config,
    require_config,
    resolve_config,
    revalidate_config_slice,
    save_config_snapshot,
)
from mote_kernel.execution import Graph
from mote_kernel.failover import Failover
from mote_kernel.failover.assembly import FailoverCall, FailoverResult
from mote_kernel.failover.config import (
    FailoverBinding,
    FailoverConfig,
    FailoverPlanBinding,
    FailoverPrepareBinding,
)
from mote_kernel.failover.contract import (
    AttemptPreparation,
    Completed,
    ErrorHint,
    FailureClass,
    FailureEvidence,
    PortOutcome,
    PreparationAction,
    PreparedRequest,
    Rejected,
)
from mote_kernel.failover.plan import (
    FailoverConfigRevision,
    FailoverOperationId,
    FailoverPlan,
    FailoverPortId,
    FailoverProfile,
    FailoverProfileId,
)
from mote_kernel.hooks.config import HookBinding, HookConfig, HookPriorityBinding
from mote_kernel.hooks.contract import (
    HookActivationRequest,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookPriority, HookSlotId, HookStage
from mote_kernel.hooks.node import HookNode
from mote_kernel.hooks.plan import HookPlan, HookPriorityPlan
from mote_kernel.invocation import Invocation
from mote_kernel.loop.config import (
    ActToObserveBinding,
    ObserveToActBinding,
    ObserveToThinkBinding,
    ReActConfig,
    ReActPrepareBinding,
    ReActRouteBinding,
    ThinkToObserveBinding,
)
from mote_kernel.loop.contract import (
    ActToObserveProjector,
    ObserveRoutePolicy,
    ObserveToActProjector,
    ObserveToThinkProjector,
    ReActRoute,
    ThinkToObserveProjector,
)
from mote_kernel.loop.node import ReActNode
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.config import (
    AcknowledgeBinding,
    GetObservationBinding,
    ObserveBinding,
    ObserveConfig,
    WriteObservationBinding,
)
from mote_kernel.observe.contract import (
    ConfigApplyResult,
    ConfigBatch,
    ConfigObservation,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
    UserObservation,
)
from mote_kernel.observe.node import ObserveNode
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphFrontierState,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
)
from mote_kernel.think.config import (
    CommandBinding,
    CompactBinding,
    ContextBinding,
    InferenceBinding,
    PromptBinding,
    RouterBinding,
    ThinkConfig,
)
from mote_kernel.think.contract import (
    CompactedContext,
    CompactRequest,
    ContextFrame,
    ContextRequest,
    InferenceRequest,
    InferenceResult,
    ModelBinding,
    RouterRequest,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)
from mote_kernel.think.node import ThinkNode


@dataclass(frozen=True, slots=True)
class _Slice(ConfigSlice):
    label: str


def _key(name: str = "agent.graph", version: int = 1) -> ConfigSnapshotKey:
    return ConfigSnapshotKey(GraphDefinitionId(name), GraphDefinitionVersion(version))


def _snapshot(key: ConfigSnapshotKey | None = None, payload: bytes = b'{"version":1}') -> ConfigSnapshot:
    return ConfigSnapshot.capture(_key() if key is None else key, payload)


def _config(snapshot: ConfigSnapshot) -> Config:
    def projection(label: str) -> _Slice:
        return _Slice(snapshot.key, label)

    return Config(
        snapshot,
        projection("react"),
        projection("observe"),
        projection("think"),
        projection("act"),
        (projection("hook"),),
        (projection("failover"),),
    )


def _state(key: ConfigSnapshotKey) -> GraphRunState:
    return GraphRunState(
        GraphRunId("run-1"),
        key.definition_id,
        key.definition_version,
        GraphRunStatus.RUNNING,
        0,
        GraphFrontierState(()),
    )


class _Store:
    def __init__(self, snapshot: ConfigSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.saved: list[ConfigSnapshot] = []
        self.loaded: list[ConfigSnapshotKey] = []

    async def save(self, snapshot: ConfigSnapshot, /) -> ConfigSnapshot:
        self.saved.append(snapshot)
        return snapshot

    async def load(self, key: ConfigSnapshotKey, /) -> ConfigSnapshot:
        self.loaded.append(key)
        if self.snapshot is None:
            raise LookupError(key)
        return self.snapshot


class _Resolver:
    def __init__(self, result: Config | None = None) -> None:
        self.result = result
        self.snapshots: list[ConfigSnapshot] = []

    async def resolve(self, snapshot: ConfigSnapshot, /) -> Config:
        self.snapshots.append(snapshot)
        if self.result is None:
            return _config(snapshot)
        return self.result


class _ForeignSelector:
    def __init__(self, result: ConfigSlice) -> None:
        self.result = result

    def select(self, _config: Config, /) -> ConfigSlice:
        return self.result


class _NoneSelector:
    def select(self, _config: Config, /) -> None:
        return None


class _ValueSelector:
    def select(self, _config: Config, /) -> object:
        return object()


class _BaseSliceSelector:
    def select(self, config: Config, /) -> ConfigSlice:
        return ConfigSlice(config.snapshot.key)


class _NonCallableSelector:
    select = object()


class _NonCallableStore:
    save = object()
    load = object()


class _NonCallableResolver:
    resolve = object()


class _FailoverPreparation:
    async def prepare_next(
        self,
        request: str,
        _action: PreparationAction[str],
        /,
    ) -> PreparedRequest[str]:
        return PreparedRequest(request)


class _UpdatingObservePorts(ObservePorts):
    def __init__(self) -> None:
        super().__init__(
            (
                available(
                    delivery(0, ConfigObservation(ObservationText("next")), "config-1"),
                    delivery(1, UserObservation(ObservationText("question")), "user-1"),
                ),
            )
        )
        self.successor: Config | None = None

    async def apply(self, batch: ConfigBatch, /) -> ConfigApplyResult:
        result = await super().apply(batch)
        if self.successor is None:
            raise AssertionError("successor Config was not installed")
        return replace(result, successor_config=self.successor)


class _RecordingObserveInvocation:
    def __init__(self) -> None:
        self.requests: list[HookInvocationRequest[Priority, ObserveHookEnvelope]] = []

    async def invoke(
        self,
        request: HookInvocationRequest[Priority, ObserveHookEnvelope],
        /,
    ) -> HookStageResult[ObserveHookEnvelope, ObserveCommand]:
        self.requests.append(request)
        return HookStageResult(request.payload)


class _RecordingThinkInvocation:
    def __init__(self) -> None:
        self.requests: list[HookInvocationRequest[Priority, ThinkFrame[ThinkStep, SharedState]]] = []

    async def invoke(
        self,
        request: HookInvocationRequest[Priority, ThinkFrame[ThinkStep, SharedState]],
        /,
    ) -> HookStageResult[ThinkFrame[ThinkStep, SharedState], ThinkCommand]:
        self.requests.append(request)
        return HookStageResult(request.payload)


class _RecordingActInvocation:
    def __init__(self) -> None:
        self.requests: list[HookInvocationRequest[Priority, ActHookEnvelope]] = []

    async def invoke(
        self,
        request: HookInvocationRequest[Priority, ActHookEnvelope],
        /,
    ) -> HookStageResult[ActHookEnvelope, ActCommand]:
        self.requests.append(request)
        return HookStageResult(request.payload)


class _RecordingThinkPorts(ThinkPorts):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    async def load_system_prompt(self, payload: ThinkPayload, /) -> str:
        self.events.append("prompt")
        return await super().load_system_prompt(payload)

    async def load_context(
        self,
        request: ContextRequest[ThinkPayload, SharedState, str, str, str],
        /,
    ) -> ContextFrame[tuple[str, ...]]:
        self.events.append("context")
        return await super().load_context(request)

    async def compact(
        self,
        request: CompactRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> CompactedContext[tuple[str, ...]]:
        self.events.append("compact")
        return await super().compact(request)

    async def route_model(
        self,
        request: RouterRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> ModelBinding:
        self.events.append("router")
        return await super().route_model(request)

    async def infer(
        self,
        request: InferenceRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> InferenceResult[str]:
        self.events.append("inference")
        return await super().infer(request)

    async def build_command(self, request: InferenceResult[str], /) -> ThinkCoreResult[str]:
        self.events.append("command")
        return await super().build_command(request)


class _RecordingActPorts(ActPorts):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    async def resolve(self, request: ActRequest, /) -> ResolvePortResult:
        self.events.append("resolve")
        return await super().resolve(request)

    async def request_authorization(self, invocation: ResolvedInvocation, /) -> AuthorizationRequestRef:
        self.events.append("authorize")
        return await super().request_authorization(invocation)

    def encode_interrupt(self, request_ref: AuthorizationRequestRef, /) -> bytes:
        self.events.append("encode_interrupt")
        return super().encode_interrupt(request_ref)

    def build_resume_input(
        self,
        interrupt: AuthorizationInterruptView,
        decision: AuthorizationDecision,
        /,
    ) -> AuthorizationInput:
        self.events.append("resume_authorization")
        return super().build_resume_input(interrupt, decision)

    async def execute(self, invocation: AuthorizedInvocation, /) -> ToolExecutionResult:
        self.events.append("execute")
        return await super().execute(invocation)

    async def project(self, result: ToolExecutionResult, /) -> SettlementProjection:
        self.events.append("settle_project")
        return await super().project(result)

    async def write(self, request: ToolExchangeWriteRequest, /) -> ToolExchangeWriteResult:
        self.events.append("settle_write")
        return await super().write(request)


class _ScriptedFailoverAttempt:
    def __init__(self, outcomes: list[PortOutcome[str, str, str]]) -> None:
        self.outcomes = outcomes
        self.requests: list[str] = []

    async def invoke_once(self, request: str, /) -> PortOutcome[str, str, str]:
        self.requests.append(request)
        return self.outcomes.pop(0)


class _RecordingFailoverPreparation:
    def __init__(self) -> None:
        self.calls: list[tuple[str, PreparationAction[str]]] = []

    async def prepare_next(
        self,
        request: str,
        action: PreparationAction[str],
        /,
    ) -> PreparedRequest[str]:
        self.calls.append((request, action))
        return PreparedRequest(f"{request}:runtime")


@dataclass(frozen=True, slots=True)
class _ReActConfigFixture:
    config: Config
    observe: ObserveConfig[Priority, SharedState, ObserveCommand]
    think: ThinkConfig[
        Priority,
        ThinkPayload,
        SharedState,
        ThinkCommand,
        str,
        str,
        str,
        tuple[str, ...],
        tuple[str, ...],
        str,
        str,
    ]
    act: ActConfig[SharedState, ActCommand]
    observe_hook: HookConfig[Priority, ObserveHookEnvelope, SharedState, ObserveCommand]
    think_hook: HookConfig[Priority, ThinkFrame[ThinkStep, SharedState], SharedState, ThinkCommand]
    act_hook: HookConfig[Priority, ActHookEnvelope, SharedState, ActCommand]
    failover: FailoverConfig[str, str]


def _slot(definition_id: str) -> HookSlotId:
    return HookSlotId(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(1),
        GraphNodeId("hook"),
        HookStage.AFTER_NODE,
    )


def _route_config(_result: ObserveResult, /) -> ReActRoute:
    return ReActRoute.CONFIG


def _observe_to_act(
    value: HookResult[ObserveHookEnvelope, ObserveCommand],
    /,
) -> ActRequest:
    return valid_act_request(next_state(value))


def _observe_to_think(
    value: HookResult[ObserveHookEnvelope, ObserveCommand],
    /,
) -> ThinkRequest[ThinkPayload, SharedState]:
    return ThinkRequest(ThinkPayload("think"), next_state(value))


def _think_to_observe(
    value: HookResult[ThinkFrame[ThinkStep, SharedState], ThinkCommand],
    /,
) -> ObserveRequest[SharedState]:
    state = value.value.hook_state
    return ObserveRequest(state.cursor, state)


def _act_to_observe(
    value: HookResult[ActHookEnvelope, ActCommand],
    /,
) -> ObserveRequest[SharedState]:
    state = cast(SharedState, value.value.hook_state)
    return ObserveRequest(state.cursor, state)


def _react_config_fixture(observe_ports: ObservePorts | None = None) -> _ReActConfigFixture:
    snapshot = _snapshot(_key("loop"), b'{"agent":"complete"}')
    key = snapshot.key
    observe_ports = ObservePorts() if observe_ports is None else observe_ports
    think_ports = ThinkPorts()
    act_ports = ActPorts()
    observe_contract = observe_admission()
    act_contract = act_admission()
    observe_slot = _slot("loop.observe")
    think_slot = _slot("loop.think")
    act_slot = _slot("loop.act")

    observe = ObserveConfig[Priority, SharedState, ObserveCommand](
        key,
        GraphDefinitionId("loop.observe"),
        GraphDefinitionVersion(1),
        observe_ports,
        observe_ports,
        observe_ports,
        observe_ports,
        observe_ports,
        observe_ports,
        observe_contract,
        observe_slot,
    )
    think = ThinkConfig[
        Priority,
        ThinkPayload,
        SharedState,
        ThinkCommand,
        str,
        str,
        str,
        tuple[str, ...],
        tuple[str, ...],
        str,
        str,
    ](
        key,
        GraphDefinitionId("loop.think"),
        GraphDefinitionVersion(1),
        think_ports,
        think_ports,
        think_ports,
        think_ports,
        think_ports,
        think_ports,
        SharedState,
        think_slot,
    )
    act = ActConfig[SharedState, ActCommand](
        key,
        GraphDefinitionId("loop.act"),
        GraphDefinitionVersion(1),
        act_ports,
        act_ports,
        act_ports,
        act_ports,
        act_ports,
        OpaqueGraphFailureReason("denied"),
        act_contract,
        act_slot,
    )
    observe_hook = HookConfig[Priority, ObserveHookEnvelope, SharedState, ObserveCommand](
        key,
        observe_slot,
        hook_plan(),
        PassThroughInvocation[ObserveHookEnvelope, SharedState, ObserveCommand](),
        HookPayloadAdmission(Priority, ObserveHookEnvelope, SharedState, ObserveCommand, observe_contract),
    )
    think_hook = HookConfig[Priority, ThinkFrame[ThinkStep, SharedState], SharedState, ThinkCommand](
        key,
        think_slot,
        hook_plan(),
        PassThroughInvocation[ThinkFrame[ThinkStep, SharedState], SharedState, ThinkCommand](),
        HookPayloadAdmission(
            Priority,
            cast(type[ThinkFrame[ThinkStep, SharedState]], ThinkFrame),
            SharedState,
            ThinkCommand,
        ),
    )
    act_hook = HookConfig[Priority, ActHookEnvelope, SharedState, ActCommand](
        key,
        act_slot,
        hook_plan(),
        PassThroughInvocation[ActHookEnvelope, SharedState, ActCommand](),
        HookPayloadAdmission(Priority, ActHookEnvelope, SharedState, ActCommand, act_contract),
    )
    port_id = FailoverPortId("model")
    failover_plan: FailoverPlan[str] = FailoverPlan(
        FailoverConfigRevision(7),
        port_id,
        FailoverProfile[str](FailoverProfileId("default")),
    )
    failover = FailoverConfig[str, str](key, port_id, failover_plan, _FailoverPreparation())
    react = ReActConfig[
        SharedState,
        ObserveCommand,
        ThinkPayload,
        SharedState,
        ThinkCommand,
        SharedState,
        ActCommand,
    ](
        key,
        GraphDefinitionId("loop"),
        GraphDefinitionVersion(1),
        _route_config,
        _observe_to_act,
        _observe_to_think,
        _think_to_observe,
        _act_to_observe,
    )
    config = Config(
        snapshot,
        react,
        observe,
        think,
        act,
        (observe_hook, think_hook, act_hook),
        (failover,),
    )
    return _ReActConfigFixture(
        config,
        observe,
        think,
        act,
        observe_hook,
        think_hook,
        act_hook,
        failover,
    )


def _config_with_think_runtime(
    fixture: _ReActConfigFixture,
    ports: _RecordingThinkPorts,
    invocation: _RecordingThinkInvocation,
    /,
) -> Config:
    key = ConfigSnapshotKey(
        fixture.config.snapshot.key.definition_id,
        fixture.config.snapshot.key.definition_version,
        2,
    )
    snapshot = ConfigSnapshot.capture(key, b'{"agent":"think-runtime"}')
    react = replace(
        cast(
            ReActConfig[
                SharedState,
                ObserveCommand,
                ThinkPayload,
                SharedState,
                ThinkCommand,
                SharedState,
                ActCommand,
            ],
            fixture.config.react,
        ),
        snapshot_key=key,
    )
    think = replace(
        fixture.think,
        snapshot_key=key,
        prompt_port=ports,
        context_port=ports,
        compact_port=ports,
        router_port=ports,
        inference_port=ports,
        command_port=ports,
    )
    return Config(
        snapshot,
        react,
        replace(fixture.observe, snapshot_key=key),
        think,
        replace(fixture.act, snapshot_key=key),
        (
            replace(fixture.observe_hook, snapshot_key=key),
            replace(fixture.think_hook, snapshot_key=key, invocation=invocation),
            replace(fixture.act_hook, snapshot_key=key),
        ),
        (replace(fixture.failover, snapshot_key=key),),
    )


def _config_with_act_runtime(
    fixture: _ReActConfigFixture,
    ports: _RecordingActPorts,
    invocation: _RecordingActInvocation,
    /,
) -> Config:
    key = ConfigSnapshotKey(
        fixture.config.snapshot.key.definition_id,
        fixture.config.snapshot.key.definition_version,
        2,
    )
    snapshot = ConfigSnapshot.capture(key, b'{"agent":"act-runtime"}')
    react = replace(
        cast(
            ReActConfig[
                SharedState,
                ObserveCommand,
                ThinkPayload,
                SharedState,
                ThinkCommand,
                SharedState,
                ActCommand,
            ],
            fixture.config.react,
        ),
        snapshot_key=key,
    )
    act = replace(
        fixture.act,
        snapshot_key=key,
        resolve_port=ports,
        authorize_port=ports,
        execute_port=ports,
        settlement_port=ports,
        exchange_writer=ports,
    )
    return Config(
        snapshot,
        react,
        replace(fixture.observe, snapshot_key=key),
        replace(fixture.think, snapshot_key=key),
        act,
        (
            replace(fixture.observe_hook, snapshot_key=key),
            replace(fixture.think_hook, snapshot_key=key),
            replace(fixture.act_hook, snapshot_key=key, invocation=invocation),
        ),
        (replace(fixture.failover, snapshot_key=key),),
    )


def test_snapshot_capture_is_content_checked_and_frozen() -> None:
    snapshot = _snapshot()
    assert snapshot.digest == ConfigSnapshot.capture(snapshot.key, snapshot.payload).digest
    assert len(snapshot.digest) == 64
    with pytest.raises(ConfigContractError, match="non-empty"):
        ConfigSnapshot.capture(snapshot.key, b"")
    with pytest.raises(ConfigContractError, match="digest"):
        ConfigSnapshot(snapshot.key, snapshot.payload, cast(ConfigSnapshotDigest, "bad-digest"))
    with pytest.raises(FrozenInstanceError):
        snapshot.payload = b"changed"  # type: ignore[misc]


def test_snapshot_identity_and_envelope_admission_fail_closed() -> None:
    snapshot = _snapshot()
    with pytest.raises(ConfigContractError, match="definition_id"):
        _key("")
    with pytest.raises(ConfigContractError, match="definition_version"):
        _key(version=0)
    with pytest.raises(ConfigContractError, match="ConfigSnapshotKey"):
        ConfigSnapshot.capture(cast(ConfigSnapshotKey, object()), snapshot.payload)
    with pytest.raises(ConfigContractError, match="ConfigSnapshotKey"):
        ConfigSnapshot(
            cast(ConfigSnapshotKey, object()),
            snapshot.payload,
            snapshot.digest,
        )
    with pytest.raises(ConfigContractError, match="non-empty"):
        ConfigSnapshot(snapshot.key, b"", snapshot.digest)
    with pytest.raises(ConfigContractError, match="ConfigSnapshotKey"):
        ConfigSlice(cast(ConfigSnapshotKey, object()))

    invalid_state = replace(
        _state(snapshot.key),
        definition_id=GraphDefinitionId(""),
    )
    with pytest.raises(ConfigContractError, match="definition_id"):
        ConfigSnapshotKey.from_state(invalid_state)


@pytest.mark.asyncio
async def test_persistence_revalidates_values_reconstructed_without_dataclass_hooks() -> None:
    snapshot = _snapshot()
    forged_key = object.__new__(ConfigSnapshotKey)
    forged_snapshot = object.__new__(ConfigSnapshot)
    object.__setattr__(forged_snapshot, "key", forged_key)
    object.__setattr__(forged_snapshot, "payload", snapshot.payload)
    object.__setattr__(forged_snapshot, "digest", snapshot.digest)

    with pytest.raises(ConfigContractError, match="malformed"):
        ConfigSnapshot(forged_key, snapshot.payload, snapshot.digest)

    class CorruptLoad(_Store):
        async def load(self, _key: ConfigSnapshotKey, /) -> ConfigSnapshot:
            return forged_snapshot

    with pytest.raises(ConfigContractError, match="malformed"):
        await load_config_snapshot(CorruptLoad(snapshot), snapshot.key)

    missing_fields = object.__new__(ConfigSnapshot)

    class MissingFieldsLoad(_Store):
        async def load(self, _key: ConfigSnapshotKey, /) -> ConfigSnapshot:
            return missing_fields

    with pytest.raises(ConfigContractError, match="malformed"):
        await load_config_snapshot(MissingFieldsLoad(snapshot), snapshot.key)


def test_projection_revalidation_rejects_base_forged_and_broken_slices() -> None:
    key = _key()
    with pytest.raises(ConfigContractError, match="concrete"):
        revalidate_config_slice(ConfigSlice(key), "projection")

    forged = object.__new__(_Slice)
    object.__setattr__(forged, "snapshot_key", object())
    object.__setattr__(forged, "label", "forged")
    with pytest.raises(ConfigContractError, match="ConfigSnapshotKey"):
        revalidate_config_slice(forged, "projection")

    class BrokenSlice(ConfigSlice):
        def __post_init__(self) -> None:
            raise TypeError("broken")

    broken = object.__new__(BrokenSlice)
    object.__setattr__(broken, "snapshot_key", key)
    with pytest.raises(ConfigContractError, match="malformed"):
        revalidate_config_slice(broken, "projection")


@pytest.mark.asyncio
async def test_resolver_revalidates_a_forged_config_before_returning_it() -> None:
    snapshot = _snapshot()
    forged = object.__new__(Config)
    # Leave every slot absent.  A resolver response can be reconstructed by a
    # deserializer without invoking Config.__post_init__, so the boundary must
    # turn the resulting AttributeError into ConfigContractError.
    with pytest.raises(ConfigContractError, match="malformed"):
        await resolve_config(_Resolver(forged), snapshot)


def test_config_requires_every_projection_to_reference_the_same_snapshot() -> None:
    snapshot = _snapshot()
    foreign = _snapshot(_key("other.graph"))
    with pytest.raises(ConfigContractError, match="ConfigSnapshot"):
        Config(
            cast(ConfigSnapshot, object()),
            _Slice(snapshot.key, "react"),
            _Slice(snapshot.key, "observe"),
            _Slice(snapshot.key, "think"),
            _Slice(snapshot.key, "act"),
            (),
        )
    with pytest.raises(ConfigContractError, match="complete snapshot"):
        Config(
            snapshot,
            _Slice(foreign.key, "react"),
            _Slice(snapshot.key, "observe"),
            _Slice(snapshot.key, "think"),
            _Slice(snapshot.key, "act"),
            (),
        )
    with pytest.raises(ConfigContractError, match="concrete"):
        Config(
            snapshot,
            ConfigSlice(snapshot.key),
            _Slice(snapshot.key, "observe"),
            _Slice(snapshot.key, "think"),
            _Slice(snapshot.key, "act"),
            (),
        )
    with pytest.raises(ConfigContractError, match="hooks"):
        Config(
            snapshot,
            _Slice(snapshot.key, "react"),
            _Slice(snapshot.key, "observe"),
            _Slice(snapshot.key, "think"),
            _Slice(snapshot.key, "act"),
            cast(tuple[ConfigSlice, ...], [_Slice(snapshot.key, "hook")]),
        )
    with pytest.raises(ConfigContractError, match="failovers must be a tuple"):
        Config(
            snapshot,
            _Slice(snapshot.key, "react"),
            _Slice(snapshot.key, "observe"),
            _Slice(snapshot.key, "think"),
            _Slice(snapshot.key, "act"),
            (),
            cast(tuple[ConfigSlice, ...], [_Slice(snapshot.key, "failover")]),
        )
    with pytest.raises(ConfigContractError, match="concrete"):
        Config(
            snapshot,
            cast(ConfigSlice, object()),
            _Slice(snapshot.key, "observe"),
            _Slice(snapshot.key, "think"),
            _Slice(snapshot.key, "act"),
            (),
        )


def test_config_bind_rejects_foreign_or_non_projection_selector_results() -> None:
    snapshot = _snapshot()
    config = _config(snapshot)
    with pytest.raises(ConfigContractError, match="different snapshot"):
        config.bind(_ForeignSelector(_Slice(_key("foreign"), "foreign")))
    assert config.bind(cast(ConfigSelector[ConfigSlice | None], _NoneSelector())) is None
    with pytest.raises(ConfigContractError, match="ConfigSlice"):
        config.bind(cast(ConfigSelector[object], _ValueSelector()))
    with pytest.raises(ConfigContractError, match="concrete config projection"):
        config.bind(_BaseSliceSelector())
    with pytest.raises(ConfigContractError, match="ConfigSelector"):
        config.bind(cast(ConfigSelector[ConfigSlice], object()))
    with pytest.raises(ConfigContractError, match="ConfigSelector"):
        config.bind(cast(ConfigSelector[ConfigSlice], _NonCallableSelector()))


def test_config_bind_revalidates_a_forged_complete_aggregate() -> None:
    snapshot = _snapshot()
    forged = object.__new__(Config)
    object.__setattr__(forged, "snapshot", snapshot)
    with pytest.raises(ConfigContractError, match="malformed"):
        forged.bind(cast(ConfigSelector[ConfigSlice], _NoneSelector()))

    with pytest.raises(ConfigContractError, match="exact Config"):
        require_config(cast(Config, object()))


@pytest.mark.asyncio
async def test_persistence_helpers_reject_missing_store_or_resolver_capabilities() -> None:
    snapshot = _snapshot()
    with pytest.raises(ConfigContractError, match="ConfigSnapshotStore"):
        await save_config_snapshot(cast(ConfigSnapshotStore, object()), snapshot)
    with pytest.raises(ConfigContractError, match="ConfigSnapshotStore"):
        await load_config_snapshot(cast(ConfigSnapshotStore, object()), snapshot.key)
    with pytest.raises(ConfigContractError, match="ConfigResolver"):
        await resolve_config(cast(ConfigResolver, object()), snapshot)

    with pytest.raises(ConfigContractError, match="callable"):
        await save_config_snapshot(cast(ConfigSnapshotStore, _NonCallableStore()), snapshot)
    with pytest.raises(ConfigContractError, match="callable"):
        await resolve_config(cast(ConfigResolver, _NonCallableResolver()), snapshot)


@pytest.mark.asyncio
async def test_persistence_boundaries_reject_wrong_types_and_revisions() -> None:
    snapshot = _snapshot()
    key = snapshot.key

    class WrongSaveType(_Store):
        async def save(self, _snapshot: ConfigSnapshot, /) -> ConfigSnapshot:
            return cast(ConfigSnapshot, object())

    with pytest.raises(ConfigContractError, match="exact immutable"):
        await save_config_snapshot(WrongSaveType(), snapshot)
    with pytest.raises(ConfigContractError, match="ConfigSnapshot"):
        await save_config_snapshot(_Store(), cast(ConfigSnapshot, object()))

    class WrongSaveRevision(_Store):
        async def save(self, input_snapshot: ConfigSnapshot, /) -> ConfigSnapshot:
            return ConfigSnapshot.capture(_key("other"), input_snapshot.payload)

    with pytest.raises(ConfigContractError, match="exact immutable"):
        await save_config_snapshot(WrongSaveRevision(), snapshot)

    with pytest.raises(ConfigContractError, match="ConfigSnapshotKey"):
        await load_config_snapshot(_Store(snapshot), cast(ConfigSnapshotKey, object()))

    class WrongLoadType(_Store):
        async def load(self, _key: ConfigSnapshotKey, /) -> ConfigSnapshot:
            return cast(ConfigSnapshot, object())

    with pytest.raises(ConfigContractError, match="ConfigSnapshot"):
        await load_config_snapshot(WrongLoadType(snapshot), key)

    class WrongLoadRevision(_Store):
        async def load(self, _key_value: ConfigSnapshotKey, /) -> ConfigSnapshot:
            return _snapshot(_key("other"))

    with pytest.raises(ConfigContractError, match="different snapshot"):
        await load_config_snapshot(WrongLoadRevision(snapshot), key)

    with pytest.raises(ConfigContractError, match="ConfigSnapshot"):
        await resolve_config(_Resolver(), cast(ConfigSnapshot, object()))

    class WrongResolveType(_Resolver):
        async def resolve(self, _snapshot: ConfigSnapshot, /) -> Config:
            return cast(Config, object())

    with pytest.raises(ConfigContractError, match="preserve"):
        await resolve_config(WrongResolveType(), snapshot)


@pytest.mark.asyncio
async def test_save_and_load_use_the_exact_immutable_key() -> None:
    snapshot = _snapshot()
    store = _Store(snapshot)
    assert await save_config_snapshot(store, snapshot) == snapshot
    assert store.saved == [snapshot]
    assert await load_config_snapshot(store, snapshot.key) == snapshot
    assert store.loaded == [snapshot.key]

    class WrongSave(_Store):
        async def save(self, input_snapshot: ConfigSnapshot, /) -> ConfigSnapshot:
            return ConfigSnapshot.capture(_key("wrong"), input_snapshot.payload)

    with pytest.raises(ConfigContractError, match="exact immutable"):
        await save_config_snapshot(WrongSave(), snapshot)

    class WrongLoad(_Store):
        async def load(self, _key_value: ConfigSnapshotKey, /) -> ConfigSnapshot:
            return _snapshot(_key("wrong"))

    with pytest.raises(ConfigContractError, match="different snapshot"):
        await load_config_snapshot(WrongLoad(snapshot), snapshot.key)


@pytest.mark.asyncio
async def test_resolve_and_recover_pass_only_the_authoritative_snapshot() -> None:
    snapshot = _snapshot()
    state = _state(snapshot.key)
    store = _Store(snapshot)
    resolver = _Resolver()
    recovered = await recover_config(store, resolver, state)
    assert recovered.snapshot == snapshot
    assert store.loaded == [snapshot.key]
    assert resolver.snapshots == [snapshot]

    foreign = _snapshot(_key("foreign"))
    with pytest.raises(ConfigContractError, match="preserve"):
        await resolve_config(_Resolver(_config(foreign)), snapshot)
    with pytest.raises(ConfigContractError, match="different config snapshot"):
        _config(snapshot).admit_state(_state(foreign.key))
    with pytest.raises(ConfigContractError, match="exact GraphRunState"):
        ConfigSnapshotKey.from_state(cast(GraphRunState, object()))
    forged_state = object.__new__(GraphRunState)
    with pytest.raises(ConfigContractError, match="identity is malformed"):
        ConfigSnapshotKey.from_state(forged_state)


class _TypedStore:
    """Structural check that the persistence protocols stay narrow."""

    async def save(self, snapshot: ConfigSnapshot, /) -> ConfigSnapshot:
        return snapshot

    async def load(self, key: ConfigSnapshotKey, /) -> ConfigSnapshot:
        return _snapshot(key)


class _TypedResolver:
    async def resolve(self, snapshot: ConfigSnapshot, /) -> Config:
        return _config(snapshot)


def test_store_and_resolver_protocols_are_runtime_structural() -> None:
    assert isinstance(_TypedStore(), ConfigSnapshotStore)
    assert isinstance(_TypedResolver(), ConfigResolver)


def test_domain_nodes_bind_their_own_projection_from_one_complete_config() -> None:
    fixture = _react_config_fixture()
    config = fixture.config

    assert config.bind(ObserveBinding[Priority, SharedState, ObserveCommand]()) is fixture.observe
    assert (
        config.bind(HookBinding[Priority, ObserveHookEnvelope, SharedState, ObserveCommand](fixture.observe_hook.slot))
        is fixture.observe_hook
    )
    assert config.bind(FailoverBinding[str, str](fixture.failover.port_id)) is fixture.failover

    observe = ObserveNode[Priority, SharedState, ObserveCommand].from_config(config)
    assert observe.hook.slot == fixture.observe_hook.slot

    think = ThinkNode[Priority, SharedState, ThinkCommand].from_config(config)
    assert think.hook.slot == fixture.think_hook.slot

    act = ActNode[Priority, SharedState, ActCommand].from_config(config)
    assert act.hook.slot == fixture.act_hook.slot

    react = ReActNode[SharedState, ThinkPayload, SharedState, SharedState].from_config(config)
    assert isinstance(react, ReActNode)

    failover = Failover[str, str, str, str, str].bind(config, fixture.failover.port_id)
    assert failover is not None
    assert failover.config == fixture.failover.plan
    assert failover.preparation is fixture.failover.preparation


def test_each_activation_binding_exposes_only_its_declared_config_slice() -> None:
    fixture = _react_config_fixture()
    config = fixture.config

    resolve = config.bind(ResolveBinding[SharedState, ActCommand]())
    authorize = config.bind(AuthorizeBinding[SharedState, ActCommand]())
    execute = config.bind(ExecuteBinding[SharedState, ActCommand]())
    settle = config.bind(SettleBinding[SharedState, ActCommand]())
    assert resolve.capability is fixture.act.resolve_port
    assert authorize.capability.port is fixture.act.authorize_port
    assert authorize.capability.failure_reason == fixture.act.failure_reason
    assert execute.capability is fixture.act.execute_port
    assert settle.capability.settlement_port is fixture.act.settlement_port
    assert settle.capability.exchange_writer is fixture.act.exchange_writer
    assert not hasattr(resolve, "authorize_port")
    assert not hasattr(execute, "settlement_port")

    get_observation = config.bind(GetObservationBinding())
    write_observation = config.bind(WriteObservationBinding())
    acknowledge = config.bind(AcknowledgeBinding())
    assert get_observation.capability.queue_port is fixture.observe.queue_port
    assert get_observation.capability.background_task_port is fixture.observe.background_task_port
    assert write_observation.capability.config_port is fixture.observe.config_port
    assert write_observation.capability.context_port is fixture.observe.context_port
    assert acknowledge.capability is fixture.observe.ack_port
    assert not hasattr(get_observation, "config_port")
    assert not hasattr(write_observation, "ack_port")

    p1 = config.bind(
        HookPriorityBinding[Priority, ObserveHookEnvelope, SharedState, ObserveCommand](
            fixture.observe_hook.slot,
            HookPriority.P1,
        )
    )
    p2 = config.bind(
        HookPriorityBinding[Priority, ObserveHookEnvelope, SharedState, ObserveCommand](
            fixture.observe_hook.slot,
            HookPriority.P2,
        )
    )
    assert p1.priority_plan is fixture.observe_hook.plan.p1
    assert p2.priority_plan is fixture.observe_hook.plan.p2
    assert not hasattr(p1, "plan")

    failover_plan = config.bind(FailoverPlanBinding[str](fixture.failover.port_id))
    failover_prepare = config.bind(FailoverPrepareBinding[str, str](fixture.failover.port_id))
    assert failover_plan is not None
    assert failover_prepare is not None
    assert failover_plan.plan is fixture.failover.plan
    assert not hasattr(failover_plan, "preparation")
    assert failover_prepare.preparation is fixture.failover.preparation

    prepare = config.bind(ReActPrepareBinding())
    route = config.bind(ReActRouteBinding())
    observe_to_act = config.bind(ObserveToActBinding[ObserveCommand]())
    observe_to_think = config.bind(ObserveToThinkBinding[ObserveCommand, ThinkPayload, SharedState]())
    think_to_observe = config.bind(ThinkToObserveBinding[SharedState, ThinkCommand, SharedState]())
    act_to_observe = config.bind(ActToObserveBinding[ActCommand, SharedState]())
    react_config = cast(
        ReActConfig[
            SharedState,
            ObserveCommand,
            ThinkPayload,
            SharedState,
            ThinkCommand,
            SharedState,
            ActCommand,
        ],
        fixture.config.react,
    )
    assert prepare.definition_id == fixture.config.snapshot.key.definition_id
    assert route.capability is react_config.route_policy
    assert observe_to_act.capability is react_config.observe_to_act
    assert observe_to_think.capability is react_config.observe_to_think
    assert think_to_observe.capability is react_config.think_to_observe
    assert act_to_observe.capability is react_config.act_to_observe
    assert not hasattr(route, "observe_to_act")

    assert config.bind(PromptBinding[ThinkPayload, str, str, str]()).port is fixture.think.prompt_port
    assert (
        config.bind(ContextBinding[ThinkPayload, SharedState, str, str, str, tuple[str, ...]]()).port
        is fixture.think.context_port
    )
    assert (
        config.bind(CompactBinding[str, str, str, tuple[str, ...], tuple[str, ...]]()).port
        is fixture.think.compact_port
    )
    assert config.bind(RouterBinding[str, str, str, tuple[str, ...]]()).port is fixture.think.router_port
    assert config.bind(InferenceBinding[str, str, str, tuple[str, ...], str]()).port is fixture.think.inference_port
    assert config.bind(CommandBinding[str, str]()).port is fixture.think.command_port


@pytest.mark.asyncio
async def test_think_stages_bind_the_activation_config_without_reassembly() -> None:
    fixture = _react_config_fixture()
    runtime_ports = _RecordingThinkPorts()
    runtime_invocation = _RecordingThinkInvocation()
    runtime_config = _config_with_think_runtime(
        fixture,
        runtime_ports,
        runtime_invocation,
    )
    think = ThinkNode[Priority, SharedState, ThinkCommand].from_config(fixture.config)

    result = await think.run(
        Graph.values(
            request=ThinkRequest(
                ThinkPayload("current"),
                SharedState(),
            )
        ),
        activation_config=runtime_config,
    )

    assert isinstance(result, Graph.CompletedResult)
    assert runtime_ports.events == [
        "prompt",
        "context",
        "compact",
        "router",
        "inference",
        "command",
    ]
    assembly_ports = cast(ThinkPorts, fixture.think.prompt_port)
    assert assembly_ports.payloads == []
    assert len(runtime_invocation.requests) == 12
    assert all(request.config_cursor == runtime_config.config_cursor for request in runtime_invocation.requests)


@pytest.mark.asyncio
async def test_act_nodes_bind_the_activation_config_through_authorization_resume() -> None:
    fixture = _react_config_fixture()
    runtime_ports = _RecordingActPorts()
    runtime_invocation = _RecordingActInvocation()
    runtime_config = _config_with_act_runtime(
        fixture,
        runtime_ports,
        runtime_invocation,
    )
    act = ActNode[Priority, SharedState, ActCommand].from_config(fixture.config)

    awaiting = await act.run(
        Graph.values(request=valid_act_request()),
        activation_config=runtime_config,
    )

    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    assert awaiting.state.config_revision == 2
    action = act.resume_authorization(
        awaiting=awaiting,
        interrupt_id=str(awaiting.interrupts[0].interrupt_id),
        decision=Allow(),
        activation_config=runtime_config,
    )
    resumed_hook_result = cast(
        HookResult[ActHookEnvelope, ActCommand],
        action.input.values["hook_result"],
    )
    authorize_request = await AuthorizeNode[SharedState, ActCommand](
        fixture.act.authorize_port,
        fixture.act.failure_reason,
        fixture.act.admission,
    )(
        ConfigActivation(
            AuthorizeNodeInput(resumed_hook_result),
            runtime_config,
        )
    )
    assert type(authorize_request) is HookActivationRequest
    authorize_hook = await act.hook.run(
        Graph.values(request=authorize_request),
        activation_config=runtime_config,
    )
    assert isinstance(authorize_hook, Graph.CompletedResult)
    authorize_result = cast(
        HookResult[ActHookEnvelope, ActCommand],
        authorize_hook.outputs["result"],
    )
    execute_request = await ExecuteNode[SharedState, ActCommand](
        fixture.act.execute_port,
        fixture.act.admission,
    )(
        ConfigActivation(
            ExecuteNodeInput(authorize_result),
            runtime_config,
        )
    )
    assert type(execute_request) is HookActivationRequest
    execute_hook = await act.hook.run(
        Graph.values(request=execute_request),
        activation_config=runtime_config,
    )
    assert isinstance(execute_hook, Graph.CompletedResult)
    execute_result = cast(
        HookResult[ActHookEnvelope, ActCommand],
        execute_hook.outputs["result"],
    )
    settle_request = await SettleNode[SharedState, ActCommand](
        fixture.act.settlement_port,
        fixture.act.exchange_writer,
        fixture.act.admission,
    )(
        ConfigActivation(
            SettleNodeInput(execute_result),
            runtime_config,
        )
    )
    settle_hook = await act.hook.run(
        Graph.values(request=settle_request),
        activation_config=runtime_config,
    )

    assert isinstance(settle_hook, Graph.CompletedResult)
    assert settle_hook.state.config_revision == 2
    assert runtime_ports.events == [
        "resolve",
        "authorize",
        "encode_interrupt",
        "resume_authorization",
        "execute",
        "settle_project",
        "settle_write",
    ]
    assembly_ports = cast(ActPorts, fixture.act.resolve_port)
    assert assembly_ports.requests == []
    assert len(runtime_invocation.requests) == 8
    assert all(request.config_cursor == runtime_config.config_cursor for request in runtime_invocation.requests)


@pytest.mark.asyncio
async def test_failover_nodes_bind_the_activation_config_without_reassembly() -> None:
    fixture = _react_config_fixture()
    assembly_preparation = _RecordingFailoverPreparation()
    runtime_preparation = _RecordingFailoverPreparation()
    assembly_failover = replace(fixture.failover, preparation=assembly_preparation)
    assembly_config = replace(fixture.config, failovers=(assembly_failover,))
    runtime_key = ConfigSnapshotKey(
        fixture.config.snapshot.key.definition_id,
        fixture.config.snapshot.key.definition_version,
        2,
    )
    runtime_snapshot = ConfigSnapshot.capture(runtime_key, b'{"agent":"failover-runtime"}')
    runtime_plan = replace(
        fixture.failover.plan,
        plan_revision=FailoverConfigRevision(8),
    )
    runtime_failover = replace(
        assembly_failover,
        snapshot_key=runtime_key,
        plan=runtime_plan,
        preparation=runtime_preparation,
    )
    runtime_config = Config(
        runtime_snapshot,
        replace(fixture.config.react, snapshot_key=runtime_key),
        replace(fixture.observe, snapshot_key=runtime_key),
        replace(fixture.think, snapshot_key=runtime_key),
        replace(fixture.act, snapshot_key=runtime_key),
        tuple(replace(hook, snapshot_key=runtime_key) for hook in fixture.config.hooks),
        (runtime_failover,),
    )
    attempt = _ScriptedFailoverAttempt(
        [
            Rejected(FailureEvidence(FailureClass.RATE_LIMITED, 429, ErrorHint("rate_limited"))),
            Completed("done"),
        ]
    )
    decorator = Failover[str, str, str, str, str].bind(
        assembly_config,
        fixture.failover.port_id,
    )
    assert decorator is not None
    graph = decorator(attempt)

    result = await graph.run(
        Graph.values(request=FailoverCall(FailoverOperationId("operation-1"), "request")),
        activation_config=runtime_config,
    )

    assert isinstance(result, Graph.CompletedResult)
    terminal = cast(FailoverResult[str, str, str, str, str], result.outputs["result"])
    assert terminal.context.plan_revision == FailoverConfigRevision(8)
    assert result.state.config_revision == 2
    assert attempt.requests == ["request", "request:runtime"]
    assert len(runtime_preparation.calls) == 1
    assert assembly_preparation.calls == []


@pytest.mark.asyncio
async def test_observe_successor_config_is_bound_without_reassembling_react() -> None:
    ports = _UpdatingObservePorts()
    fixture = _react_config_fixture(ports)
    c17_invocation = _RecordingObserveInvocation()
    c18_invocation = _RecordingObserveInvocation()
    route_revisions: list[int] = []

    def c17_route(_result: ObserveResult, /) -> ReActRoute:
        route_revisions.append(1)
        return ReActRoute.CONFIG

    def c18_route(_result: ObserveResult, /) -> ReActRoute:
        route_revisions.append(2)
        return ReActRoute.CONFIG

    c17_react = replace(
        cast(
            ReActConfig[
                SharedState,
                ObserveCommand,
                ThinkPayload,
                SharedState,
                ThinkCommand,
                SharedState,
                ActCommand,
            ],
            fixture.config.react,
        ),
        route_policy=c17_route,
    )
    c17_observe_hook = replace(fixture.observe_hook, invocation=c17_invocation)
    c17 = Config(
        fixture.config.snapshot,
        c17_react,
        fixture.observe,
        fixture.think,
        fixture.act,
        (c17_observe_hook, fixture.think_hook, fixture.act_hook),
        (fixture.failover,),
    )

    c18_key = ConfigSnapshotKey(
        fixture.config.snapshot.key.definition_id,
        fixture.config.snapshot.key.definition_version,
        2,
    )
    c18_snapshot = ConfigSnapshot.capture(c18_key, b'{"agent":"next"}')
    c18_react = replace(c17_react, snapshot_key=c18_key, route_policy=c18_route)
    c18_observe = replace(fixture.observe, snapshot_key=c18_key)
    c18_think = replace(fixture.think, snapshot_key=c18_key)
    c18_act = replace(fixture.act, snapshot_key=c18_key)
    c18_observe_hook = replace(
        fixture.observe_hook,
        snapshot_key=c18_key,
        invocation=c18_invocation,
    )
    c18_think_hook = replace(fixture.think_hook, snapshot_key=c18_key)
    c18_act_hook = replace(fixture.act_hook, snapshot_key=c18_key)
    c18_failover = replace(fixture.failover, snapshot_key=c18_key)
    c18 = Config(
        c18_snapshot,
        c18_react,
        c18_observe,
        c18_think,
        c18_act,
        (c18_observe_hook, c18_think_hook, c18_act_hook),
        (c18_failover,),
    )
    ports.successor = c18

    react = ReActNode[SharedState, ThinkPayload, SharedState, SharedState].from_config(c17)
    request = ObserveRequest(cursor(0), SharedState(cursor=cursor(0)))
    result = await react.run(
        Graph.values(request=request),
        activation_config=c17,
    )

    assert isinstance(result, Graph.CompletedResult)
    output = cast(HookResult[ObserveHookEnvelope, ObserveCommand], result.outputs["result"])
    assert not hasattr(output, "activation_config")
    assert result.state.config_revision == 2
    assert result.state.config_digest == str(c18.snapshot.digest)
    assert route_revisions == [2]
    assert len(c17_invocation.requests) == 2
    assert len(c18_invocation.requests) == 2
    assert all(request.config_cursor == c17.config_cursor for request in c17_invocation.requests)
    assert all(request.config_cursor == c18.config_cursor for request in c18_invocation.requests)
    assert all(not hasattr(request, "config") for request in c18_invocation.requests)
    assert all(not hasattr(request, "activation_config") for request in c18_invocation.requests)


def test_config_module_exports_one_public_api() -> None:
    import mote_kernel.config as config_module

    assert config_module.__all__ == ["Config"]


def test_domain_projection_selection_is_exact_and_optional_failover_is_omitted() -> None:
    fixture = _react_config_fixture()
    foreign_observe = replace(fixture.config, observe=fixture.think)
    with pytest.raises(ConfigContractError, match=r"Config\.observe"):
        foreign_observe.bind(ObserveBinding[Priority, SharedState, ObserveCommand]())

    missing = replace(fixture.config, failovers=())
    assert Failover[str, str, str, str, str].bind(missing, fixture.failover.port_id, required=False) is None
    with pytest.raises(ConfigContractError, match="requires a FailoverConfig"):
        Failover[str, str, str, str, str].bind(missing, fixture.failover.port_id)

    duplicate_hooks = replace(
        fixture.config,
        hooks=(fixture.observe_hook, fixture.observe_hook, fixture.think_hook, fixture.act_hook),
    )
    with pytest.raises(ConfigContractError, match=r"duplicate|exactly one"):
        HookNode[Priority, ObserveHookEnvelope, SharedState, ObserveCommand].from_config(
            duplicate_hooks,
            fixture.observe_hook.slot,
        )

    duplicate_failover = replace(fixture.config, failovers=(fixture.failover, fixture.failover))
    with pytest.raises(ConfigContractError, match="duplicate"):
        Failover[str, str, str, str, str].bind(duplicate_failover, fixture.failover.port_id)


def test_projection_identity_and_slot_contracts_fail_closed() -> None:
    fixture = _react_config_fixture()

    with pytest.raises(ConfigContractError, match=r"ObserveConfig\.definition_id"):
        replace(fixture.observe, definition_id=GraphDefinitionId(""))
    with pytest.raises(ConfigContractError, match=r"ThinkConfig\.definition_version"):
        replace(fixture.think, definition_version=GraphDefinitionVersion(0))
    with pytest.raises(ConfigContractError, match=r"ActConfig\.hook_slot"):
        replace(fixture.act, hook_slot=_slot("foreign.act"))

    foreign_react = replace(fixture.config.react, definition_id=GraphDefinitionId("foreign.loop"))
    with pytest.raises(ConfigContractError, match="complete config snapshot"):
        ReActNode[SharedState, ThinkPayload, SharedState, SharedState].from_config(
            replace(fixture.config, react=foreign_react)
        )


def test_observe_projection_contract_is_revalidated_at_assembly() -> None:
    fixture = _react_config_fixture()
    with pytest.raises(ConfigContractError, match="definition_version"):
        replace(fixture.observe, definition_version=GraphDefinitionVersion(0))
    with pytest.raises(ConfigContractError, match="admission"):
        replace(fixture.observe, admission=cast(ObservePayloadAdmission, object()))
    with pytest.raises(ConfigContractError, match="HookSlotId"):
        replace(fixture.observe, hook_slot=cast(HookSlotId, object()))
    with pytest.raises(ConfigContractError, match="does not match"):
        replace(fixture.observe, hook_slot=_slot("foreign.observe"))


def test_think_projection_contract_is_revalidated_at_assembly() -> None:
    fixture = _react_config_fixture()
    with pytest.raises(ConfigContractError, match="definition_id"):
        replace(fixture.think, definition_id=GraphDefinitionId(""))
    with pytest.raises(ConfigContractError, match="HookSlotId"):
        replace(fixture.think, hook_slot=cast(HookSlotId, object()))
    with pytest.raises(ConfigContractError, match="does not match"):
        replace(fixture.think, hook_slot=_slot("foreign.think"))


def test_act_projection_contract_is_revalidated_at_assembly() -> None:
    fixture = _react_config_fixture()
    with pytest.raises(ConfigContractError, match="definition_id"):
        replace(fixture.act, definition_id=GraphDefinitionId(""))
    with pytest.raises(ConfigContractError, match="definition_version"):
        replace(fixture.act, definition_version=GraphDefinitionVersion(0))
    with pytest.raises(ConfigContractError, match="admission"):
        replace(fixture.act, admission=cast(ActPayloadAdmission[SharedState, ActCommand], object()))
    with pytest.raises(ConfigContractError, match="HookSlotId"):
        replace(fixture.act, hook_slot=cast(HookSlotId, object()))


def test_react_projection_callable_contract_is_revalidated_at_assembly() -> None:
    fixture = _react_config_fixture()
    with pytest.raises(ConfigContractError, match="definition_id"):
        replace(fixture.config.react, definition_id=GraphDefinitionId(""))
    with pytest.raises(ConfigContractError, match="definition_version"):
        replace(fixture.config.react, definition_version=GraphDefinitionVersion(0))
    with pytest.raises(ConfigContractError, match="route_policy"):
        replace(fixture.config.react, route_policy=cast(ObserveRoutePolicy, object()))
    with pytest.raises(ConfigContractError, match="observe_to_act"):
        replace(fixture.config.react, observe_to_act=cast(ObserveToActProjector[ObserveCommand], object()))
    with pytest.raises(ConfigContractError, match="observe_to_think"):
        replace(
            fixture.config.react,
            observe_to_think=cast(ObserveToThinkProjector[ObserveCommand, ThinkPayload, SharedState], object()),
        )
    with pytest.raises(ConfigContractError, match="think_to_observe"):
        replace(
            fixture.config.react,
            think_to_observe=cast(ThinkToObserveProjector[SharedState, ThinkCommand, SharedState], object()),
        )
    with pytest.raises(ConfigContractError, match="act_to_observe"):
        replace(
            fixture.config.react,
            act_to_observe=cast(ActToObserveProjector[ActCommand, SharedState], object()),
        )


def test_hook_and_failover_projection_capabilities_are_checked_at_their_bindings() -> None:
    fixture = _react_config_fixture()

    with pytest.raises(ConfigContractError, match="invocation"):
        replace(
            fixture.observe_hook,
            invocation=cast(
                Invocation[
                    HookInvocationRequest[Priority, ObserveHookEnvelope],
                    HookStageResult[ObserveHookEnvelope, ObserveCommand],
                ],
                object(),
            ),
        )
    with pytest.raises(ConfigContractError, match="preparation"):
        replace(fixture.failover, preparation=cast(AttemptPreparation[str, str], object()))


def test_hook_projection_contract_is_revalidated_at_assembly() -> None:
    fixture = _react_config_fixture()
    with pytest.raises(ConfigContractError, match="HookSlotId"):
        replace(fixture.observe_hook, slot=cast(HookSlotId, object()))
    with pytest.raises(ConfigContractError, match="HookPlan"):
        replace(fixture.observe_hook, plan=cast(HookPlan[Priority], object()))

    malformed_plan = cast(HookPlan[Priority], object.__new__(HookPlan))
    with pytest.raises(ConfigContractError, match="malformed"):
        replace(fixture.observe_hook, plan=malformed_plan)
    with pytest.raises(ConfigContractError, match="payload_admission"):
        replace(
            fixture.observe_hook,
            payload_admission=cast(
                HookPayloadAdmission[Priority, ObserveHookEnvelope, SharedState, ObserveCommand],
                object(),
            ),
        )
    invalid_plan = HookPlan(HookPriorityPlan(object()), HookPriorityPlan(Priority(2)))
    with pytest.raises(ConfigContractError, match="does not satisfy"):
        replace(fixture.observe_hook, plan=invalid_plan)

    malformed_slot = object.__new__(HookSlotId)
    with pytest.raises(ConfigContractError, match="malformed"):
        HookBinding[Priority, ObserveHookEnvelope, SharedState, ObserveCommand](malformed_slot)
    with pytest.raises(ConfigContractError, match="HookSlotId"):
        HookBinding[Priority, ObserveHookEnvelope, SharedState, ObserveCommand](cast(HookSlotId, object()))
    invalid_hooks = replace(fixture.config, hooks=(_Slice(fixture.config.snapshot.key, "hook"),))
    with pytest.raises(ConfigContractError, match="non-HookConfig"):
        invalid_hooks.bind(
            HookBinding[Priority, ObserveHookEnvelope, SharedState, ObserveCommand](fixture.observe_hook.slot)
        )


def test_failover_projection_contract_is_revalidated_at_assembly() -> None:
    fixture = _react_config_fixture()
    with pytest.raises(ConfigContractError, match="port_id"):
        replace(fixture.failover, port_id=cast(FailoverPortId, ""))
    with pytest.raises(ConfigContractError, match="FailoverPlan"):
        replace(fixture.failover, plan=cast(FailoverPlan[str], object()))

    malformed_plan = cast(FailoverPlan[str], object.__new__(FailoverPlan))
    with pytest.raises(ConfigContractError, match="malformed"):
        replace(fixture.failover, plan=malformed_plan)
    other_port = FailoverPortId("other")
    with pytest.raises(ConfigContractError, match="does not match"):
        replace(fixture.failover, port_id=other_port)

    class NonCallablePreparation:
        prepare_next = object()

    with pytest.raises(ConfigContractError, match="preparation"):
        replace(fixture.failover, preparation=cast(AttemptPreparation[str, str], NonCallablePreparation()))
    with pytest.raises(ConfigContractError, match="port_id"):
        FailoverBinding[str, str](cast(FailoverPortId, ""))
    with pytest.raises(ConfigContractError, match="required"):
        FailoverBinding[str, str](fixture.failover.port_id, cast(bool, 1))

    invalid_failovers = replace(fixture.config, failovers=(_Slice(fixture.config.snapshot.key, "failover"),))
    with pytest.raises(ConfigContractError, match="non-FailoverConfig"):
        invalid_failovers.bind(FailoverBinding[str, str](fixture.failover.port_id))
    other_plan = replace(fixture.failover.plan, port_id=other_port)
    other_failover = replace(fixture.failover, port_id=other_port, plan=other_plan)
    with pytest.raises(ConfigContractError, match="requires a FailoverConfig"):
        replace(fixture.config, failovers=(other_failover,)).bind(FailoverBinding[str, str](fixture.failover.port_id))
