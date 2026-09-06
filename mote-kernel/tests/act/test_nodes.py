"""Deterministic tests for the flat Act stage modules and shared assembly."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Generic, Never, TypeVar, cast

import pytest

import mote_kernel.act as act_package
import mote_kernel.act.authorize as authorize_module
import mote_kernel.act.execute as execute_module
import mote_kernel.act.resolve as resolve_module
import mote_kernel.act.settle as settle_module
from mote_kernel.act import ActNode
from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.authorize import AuthorizeNode
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
    Allow,
    ArgumentsDigest,
    AuthorizationDecision,
    AuthorizationInput,
    AuthorizationInterruptView,
    AuthorizationRequestRef,
    AuthorizedInvocation,
    AuthorizeNodeInput,
    AuthorizeStageValue,
    CallerIdentityRef,
    CanonicalArguments,
    Deny,
    ExecuteNodeInput,
    ExecutePortResult,
    ExecuteStageValue,
    ExecutionStopped,
    HookStateProjection,
    InitialAuthorization,
    OpaqueArguments,
    OpaqueAuthorizationHandle,
    OpaqueDefinitionReference,
    OpaqueExecutionOutcome,
    OpaqueGraphFailureReason,
    OpaqueProtocolPayload,
    OpaqueToolExchangeReceipt,
    ResolutionStopped,
    ResolvedInvocation,
    ResolvePortResult,
    ResolveStageValue,
    SettledActResult,
    SettlementProjection,
    SettleNodeInput,
    SettleStageValue,
    ToolBindingRef,
    ToolCallId,
    ToolExchangeScopeId,
    ToolExchangeWriteRequest,
    ToolExchangeWriteResult,
    ToolExecutionIdentity,
    ToolExecutionResult,
    ToolPairingIdentity,
    ToolSelector,
)
from mote_kernel.act.execute import ExecuteNode
from mote_kernel.act.identity import ActHookStage, ActInvocationKey
from mote_kernel.act.resolve import ResolveNode
from mote_kernel.act.settle import SettleNode
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import GraphInputRef, NodeOutputRef
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import (
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookRequest,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.hooks.plan import HookConfigSnapshot, HookPlan, HookPriorityPlan
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId

HookValueT = TypeVar("HookValueT")
HookRuntimeStateT = TypeVar("HookRuntimeStateT")
HookRuntimeCommandT = TypeVar("HookRuntimeCommandT")

_ResolveCall = Callable[[ActRequest], Awaitable[ResolvePortResult]]
_AuthorizeCall = Callable[[ResolvedInvocation], Awaitable[AuthorizationRequestRef]]
_ExecuteCall = Callable[[AuthorizedInvocation], Awaitable[ExecutePortResult]]
_ProjectCall = Callable[[ToolExecutionResult], Awaitable[SettlementProjection]]
_WriteCall = Callable[[ToolExchangeWriteRequest], Awaitable[ToolExchangeWriteResult]]
_BuildResumeCall = Callable[
    [AuthorizationInterruptView, AuthorizationDecision],
    AuthorizationInput,
]


@dataclass(frozen=True, slots=True)
class _State(HookStateProjection):
    turn: int = 1


@dataclass(frozen=True, slots=True)
class _Command(ActHookCommand):
    stage: str


@dataclass(frozen=True, slots=True)
class _OtherState(HookStateProjection):
    turn: int = 2


@dataclass(frozen=True, slots=True)
class _OtherCommand(ActHookCommand):
    stage: str


@dataclass(frozen=True, slots=True)
class _OtherHookValue(HookGraphValue):
    marker: str = "other"


@dataclass(frozen=True, slots=True)
class _Config:
    marker: str = "hook"


@dataclass(frozen=True, slots=True)
class _PriorityConfig:
    ordinal: int


class _ConfigSource:
    def snapshot(self) -> HookConfigSnapshot[_Config]:
        return HookConfigSnapshot(_Config())


class _PlanLoader:
    def load(self, snapshot: HookConfigSnapshot[_Config], /) -> HookPlan[_PriorityConfig]:
        assert snapshot.config.marker == "hook"
        return HookPlan(
            HookPriorityPlan(_PriorityConfig(1)),
            HookPriorityPlan(_PriorityConfig(2)),
            HookPriorityPlan(_PriorityConfig(3)),
        )


class _HookRuntime:
    def __init__(self) -> None:
        self.calls: list[HookInvocationRequest[_PriorityConfig, ActHookEnvelope, _State]] = []

    async def invoke(
        self,
        request: HookInvocationRequest[_PriorityConfig, ActHookEnvelope, _State],
        /,
    ) -> HookStageResult[ActHookEnvelope, _Command]:
        self.calls.append(request)
        return HookStageResult(request.request.value, (_Command(request.request.value.stage.value),))


class _MutatingHookRuntime(_HookRuntime):
    def __init__(self, target: ActHookStage, mutation: str) -> None:
        super().__init__()
        self.target = target
        self.mutation = mutation

    async def invoke(
        self,
        request: HookInvocationRequest[_PriorityConfig, ActHookEnvelope, _State],
        /,
    ) -> HookStageResult[ActHookEnvelope, _Command]:
        self.calls.append(request)
        envelope = request.request.value
        if envelope.stage is self.target:
            envelope = self._mutate(envelope)
        return HookStageResult(envelope, (_Command(request.request.value.stage.value),))

    def _mutate(self, envelope: ActHookEnvelope) -> ActHookEnvelope:
        if self.mutation == "phase":
            payload = cast(ResolveStageValue, envelope.payload)
            resolved = cast(InitialAuthorization, payload.authorization.phase).resolved
            request_ref = AuthorizationRequestRef(
                resolved.request.pairing,
                OpaqueAuthorizationHandle(b"forged"),
            )
            return replace(
                envelope,
                payload=ResolveStageValue(AuthorizationInput.resumed(resolved, request_ref, Allow())),
            )
        if self.mutation == "stage":
            payload = cast(ResolveStageValue, envelope.payload)
            resolved = cast(InitialAuthorization, payload.authorization.phase).resolved
            return ActHookEnvelope(
                ActHookStage.AUTHORIZE,
                AuthorizeStageValue(AuthorizedInvocation(resolved)),
                envelope.hook_state,
            )
        if self.mutation == "hook_state":
            return replace(envelope, hook_state=_State(99))
        if self.mutation in {"pairing", "binding", "digest"}:
            payload = cast(ResolveStageValue, envelope.payload)
            resolved = cast(InitialAuthorization, payload.authorization.phase).resolved
            request = resolved.request
            binding = resolved.binding
            arguments = resolved.arguments
            if self.mutation == "pairing":
                request = replace(
                    request,
                    pairing=ToolPairingIdentity(
                        ToolExchangeScopeId("forged-scope"),
                        request.pairing.invocation,
                        request.pairing.tool_call_id,
                    ),
                )
            elif self.mutation == "binding":
                binding = ToolBindingRef("forged-binding")
            else:
                arguments = replace(arguments, digest=ArgumentsDigest(b"forged-digest"))
            return replace(
                envelope,
                payload=ResolveStageValue(
                    AuthorizationInput.initial(
                        ResolvedInvocation(
                            request,
                            resolved.definition,
                            binding,
                            arguments,
                        )
                    )
                ),
            )
        if self.mutation == "authorized_binding":
            payload = cast(AuthorizeStageValue, envelope.payload)
            resolved = replace(
                payload.invocation.resolved,
                binding=ToolBindingRef("forged-authorized-binding"),
            )
            return replace(
                envelope,
                payload=AuthorizeStageValue(AuthorizedInvocation(resolved)),
            )
        if self.mutation == "execution_result":
            payload = cast(ExecuteStageValue, envelope.payload)
            return replace(
                envelope,
                payload=ExecuteStageValue(replace(payload.result, outcome=OpaqueExecutionOutcome(b"forged-outcome"))),
            )
        payload = cast(SettleStageValue, envelope.payload)
        return replace(
            envelope,
            payload=SettleStageValue(
                SettledActResult(
                    payload.result.projection,
                    OpaqueToolExchangeReceipt(b"forged-receipt"),
                )
            ),
        )


class _FailingHookRuntime(_HookRuntime):
    def __init__(self, target: ActHookStage) -> None:
        super().__init__()
        self.target = target
        self.failure = RuntimeError(f"{target.value} hook failure")

    async def invoke(
        self,
        request: HookInvocationRequest[_PriorityConfig, ActHookEnvelope, _State],
        /,
    ) -> HookStageResult[ActHookEnvelope, _Command]:
        self.calls.append(request)
        if request.request.value.stage is self.target:
            raise self.failure
        return HookStageResult(request.request.value, (_Command(request.request.value.stage.value),))


class _BlockingHookRuntime(_HookRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(
        self,
        request: HookInvocationRequest[_PriorityConfig, ActHookEnvelope, _State],
        /,
    ) -> HookStageResult[ActHookEnvelope, _Command]:
        self.calls.append(request)
        self.entered.set()
        await self.release.wait()
        return HookStageResult(request.request.value, (_Command(request.request.value.stage.value),))


class _BlockingExecuteCall:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, invocation: AuthorizedInvocation, /) -> ExecutePortResult:
        self.entered.set()
        await self.release.wait()
        return _execution(invocation.resolved)


class _PassThroughHookRuntime(Generic[HookValueT, HookRuntimeStateT, HookRuntimeCommandT]):
    async def invoke(
        self,
        request: HookInvocationRequest[_PriorityConfig, HookValueT, HookRuntimeStateT],
        /,
    ) -> HookStageResult[HookValueT, HookRuntimeCommandT]:
        return HookStageResult(request.request.value)


class _ActHookSubclass(HookNode[_Config, _PriorityConfig, ActHookEnvelope, _State, _Command]):
    pass


class _RaisingAsyncCall:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    async def __call__(self, _value: object, /) -> Never:
        raise self.failure


class _Ports:
    resolve_calls: int
    request_authorization_calls: int
    execute_calls: int
    project_calls: int
    write_calls: int

    def __init__(self) -> None:
        self.resolve_calls = 0
        self.request_authorization_calls = 0
        self.execute_calls = 0
        self.project_calls = 0
        self.write_calls = 0
        self.resolve_requests: list[ActRequest] = []
        self.authorization_requests: list[ResolvedInvocation] = []
        self.interrupt_requests: list[AuthorizationRequestRef] = []
        self.resume_requests: list[tuple[AuthorizationInterruptView, AuthorizationDecision]] = []
        self.execute_requests: list[AuthorizedInvocation] = []
        self.project_requests: list[ToolExecutionResult] = []
        self.write_requests: list[ToolExchangeWriteRequest] = []
        self._resolved: ResolvedInvocation | None = None
        self._resume_values: dict[bytes, Graph.Values[HookGraphValue]] = {}
        self.resolve_override: _ResolveCall | None = None
        self.authorize_override: _AuthorizeCall | None = None
        self.execute_override: _ExecuteCall | None = None
        self.project_override: _ProjectCall | None = None
        self.write_override: _WriteCall | None = None
        self.build_resume_override: _BuildResumeCall | None = None

    @property
    def resolved(self) -> ResolvedInvocation | None:
        return self._resolved

    async def resolve(self, request: ActRequest) -> ResolvePortResult:
        self.resolve_calls += 1
        self.resolve_requests.append(request)
        override = self.resolve_override
        if override is not None:
            return await override(request)
        resolved = ResolvedInvocation(
            request,
            OpaqueDefinitionReference("definition"),
            ToolBindingRef("binding"),
            CanonicalArguments(request.arguments, ArgumentsDigest(b"digest")),
        )
        self._resolved = resolved
        return resolved

    async def request_authorization(self, invocation: ResolvedInvocation) -> AuthorizationRequestRef:
        self.request_authorization_calls += 1
        self.authorization_requests.append(invocation)
        override = self.authorize_override
        if override is not None:
            return await override(invocation)
        return AuthorizationRequestRef(invocation.request.pairing, OpaqueAuthorizationHandle(b"handle"))

    def encode_interrupt(self, request_ref: AuthorizationRequestRef) -> bytes:
        self.interrupt_requests.append(request_ref)
        return request_ref.handle.value

    def build_resume_input(
        self,
        interrupt: AuthorizationInterruptView,
        decision: AuthorizationDecision,
    ) -> AuthorizationInput:
        self.resume_requests.append((interrupt, decision))
        override = self.build_resume_override
        if override is not None:
            return override(interrupt, decision)
        assert interrupt.request_payload == b"handle"
        assert self._resolved is not None
        return AuthorizationInput.resumed(
            self._resolved,
            AuthorizationRequestRef(self._resolved.request.pairing, OpaqueAuthorizationHandle(b"handle")),
            decision,
        )

    def encode_graph_input(self, values: Graph.Values[HookGraphValue]) -> bytes:
        payload = str(len(self._resume_values)).encode()
        self._resume_values[payload] = values
        return payload

    def decode_graph_input(self, payload: bytes) -> Graph.Values[HookGraphValue]:
        return self._resume_values[payload]

    @property
    def codec_id(self) -> str:
        return "act-test.codec"

    @property
    def codec_version(self) -> int:
        return 1

    async def execute(self, invocation: AuthorizedInvocation) -> ExecutePortResult:
        self.execute_calls += 1
        self.execute_requests.append(invocation)
        override = self.execute_override
        if override is not None:
            return await override(invocation)
        identity = ToolExecutionIdentity(
            invocation.resolved.request.pairing,
            invocation.resolved.binding,
            invocation.resolved.arguments.digest,
        )
        return ToolExecutionResult(identity, OpaqueExecutionOutcome(b"outcome"))

    async def project(self, result: ToolExecutionResult) -> SettlementProjection:
        self.project_calls += 1
        self.project_requests.append(result)
        override = self.project_override
        if override is not None:
            return await override(result)
        return SettlementProjection(result.identity, OpaqueProtocolPayload(b"projection"))

    async def write(self, request: ToolExchangeWriteRequest) -> ToolExchangeWriteResult:
        self.write_calls += 1
        self.write_requests.append(request)
        override = self.write_override
        if override is not None:
            return await override(request)
        return ToolExchangeWriteResult(OpaqueToolExchangeReceipt(b"receipt"))


class _ConcurrentPorts(_Ports):
    def __init__(self) -> None:
        super().__init__()
        self.resolved_by_handle: dict[bytes, ResolvedInvocation] = {}

    async def resolve(self, request: ActRequest) -> ResolvePortResult:
        await asyncio.sleep(0)
        return await super().resolve(request)

    async def request_authorization(self, invocation: ResolvedInvocation) -> AuthorizationRequestRef:
        self.request_authorization_calls += 1
        self.authorization_requests.append(invocation)
        await asyncio.sleep(0)
        handle = invocation.request.pairing.tool_call_id.value.encode()
        self.resolved_by_handle[handle] = invocation
        return AuthorizationRequestRef(
            invocation.request.pairing,
            OpaqueAuthorizationHandle(handle),
        )

    def build_resume_input(
        self,
        interrupt: AuthorizationInterruptView,
        decision: AuthorizationDecision,
    ) -> AuthorizationInput:
        self.resume_requests.append((interrupt, decision))
        resolved = self.resolved_by_handle[interrupt.request_payload]
        return AuthorizationInput.resumed(
            resolved,
            AuthorizationRequestRef(
                resolved.request.pairing,
                OpaqueAuthorizationHandle(interrupt.request_payload),
            ),
            decision,
        )

    async def execute(self, invocation: AuthorizedInvocation) -> ExecutePortResult:
        await asyncio.sleep(0)
        return await super().execute(invocation)

    async def project(self, result: ToolExecutionResult) -> SettlementProjection:
        await asyncio.sleep(0)
        return await super().project(result)

    async def write(self, request: ToolExchangeWriteRequest) -> ToolExchangeWriteResult:
        await asyncio.sleep(0)
        return await super().write(request)


def _request() -> ActRequest:
    pairing = ToolPairingIdentity(
        ToolExchangeScopeId("scope"),
        ActInvocationKey("invocation"),
        ToolCallId("call"),
    )
    return ActRequest(pairing, ToolSelector("tool"), OpaqueArguments(b"{}"), CallerIdentityRef(b"caller"), _State())


def _named_request(name: str, turn: int) -> ActRequest:
    pairing = ToolPairingIdentity(
        ToolExchangeScopeId(f"scope-{name}"),
        ActInvocationKey(f"invocation-{name}"),
        ToolCallId(f"call-{name}"),
    )
    return ActRequest(
        pairing,
        ToolSelector(f"tool-{name}"),
        OpaqueArguments(name.encode()),
        CallerIdentityRef(name.encode()),
        _State(turn),
    )


def _resolved(request: ActRequest | None = None) -> ResolvedInvocation:
    source = _request() if request is None else request
    return ResolvedInvocation(
        source,
        OpaqueDefinitionReference("definition"),
        ToolBindingRef("binding"),
        CanonicalArguments(source.arguments, ArgumentsDigest(b"digest")),
    )


def _execution(resolved: ResolvedInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(
        ToolExecutionIdentity(
            resolved.request.pairing,
            resolved.binding,
            resolved.arguments.digest,
        ),
        OpaqueExecutionOutcome(b"outcome"),
    )


def _stage_envelope(stage: ActHookStage, resolved: ResolvedInvocation) -> ActHookEnvelope:
    if stage is ActHookStage.RESOLVE:
        payload = ResolveStageValue(AuthorizationInput.initial(resolved))
    elif stage is ActHookStage.AUTHORIZE:
        payload = AuthorizeStageValue(AuthorizedInvocation(resolved))
    elif stage is ActHookStage.EXECUTE:
        payload = ExecuteStageValue(_execution(resolved))
    else:
        execution = _execution(resolved)
        projection = SettlementProjection(
            execution.identity,
            OpaqueProtocolPayload(b"projection"),
        )
        payload = SettleStageValue(
            SettledActResult(
                projection,
                OpaqueToolExchangeReceipt(b"receipt"),
            )
        )
    return ActHookEnvelope(stage, payload, resolved.request.hook_state)


def _hook_result(
    envelope: ActHookEnvelope,
    node_id: GraphNodeId | None = None,
) -> HookResult[ActHookEnvelope, _Command]:
    return HookResult(
        envelope,
        (),
        GraphNodeId(envelope.stage.value) if node_id is None else node_id,
    )


def _authorize_node_input(envelope: ActHookEnvelope) -> AuthorizeNodeInput[_Command]:
    return AuthorizeNodeInput(_hook_result(envelope))


def _execute_node_input(envelope: ActHookEnvelope) -> ExecuteNodeInput[_Command]:
    return ExecuteNodeInput(_hook_result(envelope))


def _settle_node_input(envelope: ActHookEnvelope) -> SettleNodeInput[_Command]:
    return SettleNodeInput(_hook_result(envelope))


async def _invoke_external_stage(stage: str, ports: _Ports) -> None:
    admission = ActPayloadAdmission(_State, _Command)
    resolved = _resolved()
    if stage == "resolve":
        await ResolveNode(ports, admission)(resolved.request)
        return
    if stage == "authorize":
        envelope = ActHookEnvelope(
            ActHookStage.RESOLVE,
            ResolveStageValue(AuthorizationInput.initial(resolved)),
            _State(),
        )
        await AuthorizeNode(
            ports,
            OpaqueGraphFailureReason("denied"),
            admission,
        )(_authorize_node_input(envelope))
        return
    if stage == "execute":
        envelope = ActHookEnvelope(
            ActHookStage.AUTHORIZE,
            AuthorizeStageValue(AuthorizedInvocation(resolved)),
            _State(),
        )
        await ExecuteNode(ports, admission)(_execute_node_input(envelope))
        return
    envelope = ActHookEnvelope(
        ActHookStage.EXECUTE,
        ExecuteStageValue(_execution(resolved)),
        _State(),
    )
    await SettleNode(ports, ports, admission)(_settle_node_input(envelope))


def _hook(
    definition_id: str,
    runtime: _HookRuntime,
    act_admission: ActPayloadAdmission[_State, _Command],
    *,
    version: int = 1,
    node_id: str = "hook",
    stage: HookStage = HookStage.AFTER_NODE,
    payload_admission: HookPayloadAdmission[
        _Config,
        _PriorityConfig,
        ActHookEnvelope,
        _State,
        _Command,
    ]
    | None = None,
) -> HookNode[_Config, _PriorityConfig, ActHookEnvelope, _State, _Command]:
    admission = (
        HookPayloadAdmission(
            _Config,
            _PriorityConfig,
            ActHookEnvelope,
            _State,
            _Command,
            act_admission,
        )
        if payload_admission is None
        else payload_admission
    )
    slot = HookSlotId(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(version),
        GraphNodeId(node_id),
        stage,
    )
    return HookNode(slot, _ConfigSource(), _PlanLoader(), runtime, admission)


def _act(ports: _Ports, runtime: _HookRuntime) -> ActNode[_Config, _PriorityConfig, _State, _Command]:
    admission = ActPayloadAdmission(_State, _Command)
    return ActNode(
        "act.test",
        resolve_port=ports,
        authorize_port=ports,
        execute_port=ports,
        settlement_port=ports,
        exchange_writer=ports,
        hook=_hook("act.test", runtime, admission),
        failure_reason=OpaqueGraphFailureReason("authorization denied"),
        admission=admission,
    )


def _assemble(
    ports: _Ports,
    hook: HookNode[_Config, _PriorityConfig, ActHookEnvelope, _State, _Command],
    admission: ActPayloadAdmission[_State, _Command],
    *,
    definition_id: str = "act.test",
    version: int = 1,
) -> ActNode[_Config, _PriorityConfig, _State, _Command]:
    return ActNode(
        definition_id,
        version=version,
        resolve_port=ports,
        authorize_port=ports,
        execute_port=ports,
        settlement_port=ports,
        exchange_writer=ports,
        hook=hook,
        failure_reason=OpaqueGraphFailureReason("authorization denied"),
        admission=admission,
    )


def _builder_node_ids(act: ActNode[_Config, _PriorityConfig, _State, _Command]) -> tuple[str, ...]:
    # Lock the fixed composition without adding production introspection only
    # for tests.
    return tuple(str(candidate.node_id) for candidate in act._builder_state.nodes)  # pyright: ignore[reportPrivateUsage]


def test_each_stage_module_exposes_only_its_node() -> None:
    assert resolve_module.__all__ == ["ResolveNode"]
    assert authorize_module.__all__ == ["AuthorizeNode"]
    assert execute_module.__all__ == ["ExecuteNode"]
    assert settle_module.__all__ == ["SettleNode"]
    assert act_package.__all__ == ["ActNode"]
    assert not hasattr(act_package, "ActRequest")


def test_act_builder_contains_exactly_four_business_nodes_and_one_shared_hook() -> None:
    act = _act(_Ports(), _HookRuntime())

    assert _builder_node_ids(act) == (
        "resolve",
        "hook",
        "authorize",
        "execute",
        "settle",
    )
    hook_candidate = act._builder_state.nodes[1]  # pyright: ignore[reportPrivateUsage]
    assert hook_candidate.graph is act.hook  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]


def test_all_act_business_nodes_use_typed_graph_contracts() -> None:
    act = _act(_Ports(), _HookRuntime())
    candidates = tuple(
        candidate
        for candidate in act._builder_state.nodes  # pyright: ignore[reportPrivateUsage]
        if isinstance(candidate, CallableNodeDefinition)
    )

    assert len(candidates) == 4
    for candidate in candidates:
        assert isinstance(candidate, CallableNodeDefinition)
        assert callable(candidate.invoker)
        assert tuple(output.name for output in candidate.outputs.entries) == ("hook_request",)
        assert candidate.outputs.entries[0].descriptor.value_type is HookRequest

    resolve_source = candidates[0].inputs.entries[0].source
    assert type(resolve_source) is GraphInputRef
    assert resolve_source.descriptor.value_type is ActRequest

    for candidate in candidates[1:]:
        source = candidate.inputs.entries[0].source
        # Authorize must be absolute so a resume override is legal.  Execute
        # and Settle deliberately use the same latest-Hook publication rule.
        assert type(source) is NodeOutputRef
        assert source.node_id == GraphNodeId("hook")
        assert source.output_name == "result"
        assert source.descriptor is not None
        assert source.descriptor.value_type is HookResult


def test_stage_nodes_are_distinct_graph_callables() -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    assert isinstance(ResolveNode(ports, admission), ResolveNode)
    assert isinstance(AuthorizeNode(ports, OpaqueGraphFailureReason("denied"), admission), AuthorizeNode)
    assert isinstance(ExecuteNode(ports, admission), ExecuteNode)
    assert isinstance(SettleNode(ports, ports, admission), SettleNode)


@pytest.mark.asyncio
async def test_business_nodes_accept_and_return_only_their_typed_stage_dtos() -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    request = _request()

    resolve_output = await ResolveNode(ports, admission)(request)
    assert type(resolve_output) is HookRequest
    resolve_request = resolve_output
    assert resolve_request.node_id == GraphNodeId("resolve")
    assert resolve_request.value.stage is ActHookStage.RESOLVE

    resolved = ports.resolved
    assert resolved is not None
    request_ref = AuthorizationRequestRef(
        resolved.request.pairing,
        OpaqueAuthorizationHandle(b"handle"),
    )
    authorize_envelope = ActHookEnvelope(
        ActHookStage.RESOLVE,
        ResolveStageValue(AuthorizationInput.resumed(resolved, request_ref, Allow())),
        request.hook_state,
    )
    authorize_output = await AuthorizeNode(
        ports,
        OpaqueGraphFailureReason("denied"),
        admission,
    )(_authorize_node_input(authorize_envelope))
    assert type(authorize_output) is HookRequest
    authorize_request = authorize_output
    assert authorize_request.node_id == GraphNodeId("authorize")
    assert authorize_request.value.stage is ActHookStage.AUTHORIZE

    execute_output = await ExecuteNode(ports, admission)(_execute_node_input(authorize_request.value))
    assert type(execute_output) is HookRequest
    execute_request = execute_output
    assert execute_request.node_id == GraphNodeId("execute")
    assert execute_request.value.stage is ActHookStage.EXECUTE

    settle_output = await SettleNode(ports, ports, admission)(_settle_node_input(execute_request.value))
    assert type(settle_output) is HookRequest
    assert settle_output.node_id == GraphNodeId("settle")
    assert settle_output.value.stage is ActHookStage.SETTLE
    assert (
        ports.resolve_calls,
        ports.request_authorization_calls,
        ports.execute_calls,
        ports.project_calls,
        ports.write_calls,
    ) == (1, 0, 1, 1, 1)


def test_later_business_node_inputs_reject_a_non_hook_result() -> None:
    wrong = cast(HookResult[ActHookEnvelope, _Command], OpaqueArguments(b"not-a-hook-result"))

    with pytest.raises(ActContractError, match="authorize node HookResult"):
        AuthorizeNodeInput(wrong)
    with pytest.raises(ActContractError, match="execute node HookResult"):
        ExecuteNodeInput(wrong)
    with pytest.raises(ActContractError, match="settle node HookResult"):
        SettleNodeInput(wrong)


@pytest.mark.asyncio
async def test_later_business_nodes_reject_a_hook_result_from_the_wrong_producer() -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    resolved = _resolved()
    resolve_envelope = ActHookEnvelope(
        ActHookStage.RESOLVE,
        ResolveStageValue(AuthorizationInput.initial(resolved)),
        _State(),
    )
    authorize_envelope = ActHookEnvelope(
        ActHookStage.AUTHORIZE,
        AuthorizeStageValue(AuthorizedInvocation(resolved)),
        _State(),
    )
    execute_envelope = ActHookEnvelope(
        ActHookStage.EXECUTE,
        ExecuteStageValue(_execution(resolved)),
        _State(),
    )

    with pytest.raises(ActContractError, match="node_id"):
        await AuthorizeNode(ports, OpaqueGraphFailureReason("denied"), admission)(
            AuthorizeNodeInput(_hook_result(resolve_envelope, GraphNodeId("execute")))
        )
    with pytest.raises(ActContractError, match="node_id"):
        await ExecuteNode(ports, admission)(ExecuteNodeInput(_hook_result(authorize_envelope, GraphNodeId("resolve"))))
    with pytest.raises(ActContractError, match="node_id"):
        await SettleNode(ports, ports, admission)(
            SettleNodeInput(_hook_result(execute_envelope, GraphNodeId("authorize")))
        )

    assert ports.request_authorization_calls == 0
    assert ports.execute_calls == 0
    assert ports.project_calls == 0
    assert ports.write_calls == 0


@pytest.mark.asyncio
async def test_act_uses_one_shared_hook_for_each_stage_and_executes_once() -> None:
    ports = _Ports()
    runtime = _HookRuntime()
    act = _act(ports, runtime)
    request = _request()

    awaiting = await act.run(Graph.values(request=request))

    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    assert len(awaiting.interrupts) == 1
    assert awaiting.interrupts[0].node_id == "authorize"
    assert ports.request_authorization_calls == 1
    assert ports.execute_calls == 0
    assert len(runtime.calls) == 3

    action = act.resume_authorization(
        awaiting=awaiting,
        interrupt_id=str(awaiting.interrupts[0].interrupt_id),
        decision=Allow(),
    )
    completed = await act.run(
        state=awaiting.state,
        continuation=awaiting.continuation,
        resume=(action,),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert ports.request_authorization_calls == 1
    assert ports.execute_calls == 1
    assert ports.project_calls == 1
    assert ports.write_calls == 1
    assert len(runtime.calls) == 12
    assert tuple(call.request.node_id for call in runtime.calls) == (
        *(GraphNodeId("resolve") for _ in range(3)),
        *(GraphNodeId("authorize") for _ in range(3)),
        *(GraphNodeId("execute") for _ in range(3)),
        *(GraphNodeId("settle") for _ in range(3)),
    )
    result = cast(HookResult[ActHookEnvelope, _Command], completed.outputs["result"])
    assert result.node_id == GraphNodeId("settle")
    assert result.value.stage is ActHookStage.SETTLE
    assert result.value.hook_state is request.hook_state
    assert result.commands == (_Command("settle"),) * 3
    assert completed.state.completion_route == "settle"
    assert ports.resolve_requests == [request]
    resolved = ports.resolved
    assert resolved is not None
    assert ports.authorization_requests == [resolved]
    assert ports.interrupt_requests[0].pairing == request.pairing
    resume_view, resume_decision = ports.resume_requests[0]
    assert resume_view.scope == ()
    assert resume_view.node_id == "authorize"
    assert resume_view.interrupt_id == str(awaiting.interrupts[0].interrupt_id)
    assert resume_view.request_payload == b"handle"
    assert type(resume_decision) is Allow
    assert ports.execute_requests[0].resolved is resolved
    assert ports.project_requests[0].identity.pairing == request.pairing
    assert ports.write_requests[0].projection.source_identity == ports.project_requests[0].identity


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", list(ActHookStage))
async def test_shared_hook_exports_each_originating_business_node_route(stage: ActHookStage) -> None:
    admission = ActPayloadAdmission(_State, _Command)
    hook = _hook("act.test", _HookRuntime(), admission)
    resolved = _resolved()
    envelope = _stage_envelope(stage, resolved)
    request = HookRequest(
        envelope,
        resolved.request.hook_state,
        GraphNodeId(stage.value),
    )

    completed = await hook.run(Graph.values(request=request))

    assert isinstance(completed, Graph.CompletedResult)
    result = cast(HookResult[ActHookEnvelope, _Command], completed.outputs["result"])
    assert result.value is envelope
    assert result.node_id == GraphNodeId(stage.value)
    assert completed.state.completion_route == stage.value
    assert result.commands == (_Command(stage.value),) * 3


@pytest.mark.asyncio
async def test_resolve_rejects_wrong_input_stops_and_mismatched_resolution() -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    node = ResolveNode(ports, admission)

    with pytest.raises(ActContractError, match="Act request"):
        await node(cast(ActRequest, OpaqueArguments(b"wrong")))

    async def stop(_request: ActRequest, /) -> ResolvePortResult:
        return ResolutionStopped(OpaqueGraphFailureReason("not resolved"))

    ports.resolve_override = stop
    stopped = await node(_request())
    assert isinstance(stopped, Graph.FailureOutcome)
    assert stopped.failure == "not resolved"

    request = _request()
    other_request = ActRequest(
        request.pairing,
        ToolSelector("other-tool"),
        request.arguments,
        request.caller,
        request.hook_state,
    )

    async def mismatched(_request: ActRequest, /) -> ResolvePortResult:
        return _resolved(other_request)

    ports.resolve_override = mismatched
    with pytest.raises(ActContractError, match="does not match Act request"):
        await node(request)


@pytest.mark.asyncio
async def test_authorize_rejects_wrong_stage_and_mismatched_request_pairing() -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    resolved = _resolved()
    wrong_stage = ActHookEnvelope(
        ActHookStage.AUTHORIZE,
        AuthorizeStageValue(AuthorizedInvocation(resolved)),
        _State(),
    )
    node = AuthorizeNode(ports, OpaqueGraphFailureReason("denied"), admission)

    with pytest.raises(ActContractError, match="authorize input"):
        await node(_authorize_node_input(wrong_stage))

    other_pairing = ToolPairingIdentity(
        ToolExchangeScopeId("other-scope"),
        ActInvocationKey("invocation"),
        ToolCallId("call"),
    )

    async def mismatched(_resolved: ResolvedInvocation, /) -> AuthorizationRequestRef:
        return AuthorizationRequestRef(
            other_pairing,
            OpaqueAuthorizationHandle(b"handle"),
        )

    ports.authorize_override = mismatched
    initial = ActHookEnvelope(
        ActHookStage.RESOLVE,
        ResolveStageValue(AuthorizationInput.initial(resolved)),
        _State(),
    )
    with pytest.raises(ActContractError, match="pairing does not match"):
        await node(_authorize_node_input(initial))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [cast(bytes, bytearray(b"mutable")), b"x" * 65_537],
    ids=["non-bytes", "oversized"],
)
async def test_authorize_rejects_an_invalid_interrupt_payload_before_graph_interrupt(payload: bytes) -> None:
    class InvalidInterruptPorts(_Ports):
        def encode_interrupt(self, request_ref: AuthorizationRequestRef) -> bytes:
            self.interrupt_requests.append(request_ref)
            return payload

    admission = ActPayloadAdmission(_State, _Command)
    ports = InvalidInterruptPorts()
    resolved = _resolved()
    envelope = ActHookEnvelope(
        ActHookStage.RESOLVE,
        ResolveStageValue(AuthorizationInput.initial(resolved)),
        _State(),
    )

    with pytest.raises(ActContractError, match="request_payload"):
        await AuthorizeNode(
            ports,
            OpaqueGraphFailureReason("denied"),
            admission,
        )(_authorize_node_input(envelope))

    assert ports.request_authorization_calls == 1
    assert len(ports.interrupt_requests) == 1
    assert ports.execute_calls == 0


@pytest.mark.asyncio
async def test_authorize_deny_is_a_graph_stop_and_does_not_publish_to_execute() -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    resolved = _resolved()
    request_ref = AuthorizationRequestRef(
        resolved.request.pairing,
        OpaqueAuthorizationHandle(b"handle"),
    )
    resumed = AuthorizationInput.resumed(resolved, request_ref, Deny())
    envelope = ActHookEnvelope(
        ActHookStage.RESOLVE,
        ResolveStageValue(resumed),
        _State(),
    )

    outcome = await AuthorizeNode(
        ports,
        OpaqueGraphFailureReason("authorization denied"),
        admission,
    )(_authorize_node_input(envelope))

    assert isinstance(outcome, Graph.FailureOutcome)
    assert outcome.failure == "authorization denied"
    assert ports.execute_calls == 0


@pytest.mark.asyncio
async def test_full_act_deny_stops_without_execute_settle_or_an_extra_hook() -> None:
    ports = _Ports()
    runtime = _HookRuntime()
    act = _act(ports, runtime)
    awaiting = await act.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    action = act.resume_authorization(
        awaiting=awaiting,
        interrupt_id=str(awaiting.interrupts[0].interrupt_id),
        decision=Deny(),
    )

    failed = await act.run(
        state=awaiting.state,
        continuation=awaiting.continuation,
        resume=(action,),
    )

    assert isinstance(failed, Graph.FailedResult)
    assert tuple(failure.node_id for failure in failed.failures) == ("authorize",)
    assert ports.request_authorization_calls == 1
    assert ports.execute_calls == 0
    assert ports.project_calls == 0
    assert ports.write_calls == 0
    assert len(runtime.calls) == 3


@pytest.mark.asyncio
async def test_full_act_resolve_stop_never_enters_authorize_or_shared_hook() -> None:
    ports = _Ports()

    async def stop(_request: ActRequest, /) -> ResolvePortResult:
        return ResolutionStopped(OpaqueGraphFailureReason("resolution stopped"))

    ports.resolve_override = stop
    runtime = _HookRuntime()

    failed = await _act(ports, runtime).run(Graph.values(request=_request()))

    assert isinstance(failed, Graph.FailedResult)
    assert tuple((failure.node_id, failure.failure) for failure in failed.failures) == (
        ("resolve", "resolution stopped"),
    )
    assert (
        ports.resolve_calls,
        ports.request_authorization_calls,
        ports.execute_calls,
        ports.project_calls,
        ports.write_calls,
    ) == (1, 0, 0, 0, 0)
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_full_act_execute_stop_never_enters_settle_or_its_hook_activation() -> None:
    ports = _Ports()

    async def stop(_invocation: AuthorizedInvocation, /) -> ExecutePortResult:
        return ExecutionStopped(OpaqueGraphFailureReason("execution stopped"))

    ports.execute_override = stop
    runtime = _HookRuntime()
    act = _act(ports, runtime)
    awaiting = await act.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    action = act.resume_authorization(
        awaiting=awaiting,
        interrupt_id=str(awaiting.interrupts[0].interrupt_id),
        decision=Allow(),
    )

    failed = await act.run(
        state=awaiting.state,
        continuation=awaiting.continuation,
        resume=(action,),
    )

    assert isinstance(failed, Graph.FailedResult)
    assert tuple((failure.node_id, failure.failure) for failure in failed.failures) == (
        ("execute", "execution stopped"),
    )
    assert (
        ports.resolve_calls,
        ports.request_authorization_calls,
        ports.execute_calls,
        ports.project_calls,
        ports.write_calls,
    ) == (1, 1, 1, 0, 0)
    assert len(runtime.calls) == 6


@pytest.mark.asyncio
async def test_execute_rejects_wrong_stage_and_propagates_typed_stop() -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    resolved = _resolved()
    node = ExecuteNode(ports, admission)
    wrong_stage = ActHookEnvelope(
        ActHookStage.RESOLVE,
        ResolveStageValue(AuthorizationInput.initial(resolved)),
        _State(),
    )

    with pytest.raises(ActContractError, match="execute input"):
        await node(_execute_node_input(wrong_stage))

    async def stop(_invocation: AuthorizedInvocation, /) -> ExecutePortResult:
        return ExecutionStopped(OpaqueGraphFailureReason("not executable"))

    ports.execute_override = stop
    authorized = ActHookEnvelope(
        ActHookStage.AUTHORIZE,
        AuthorizeStageValue(AuthorizedInvocation(resolved)),
        _State(),
    )
    stopped = await node(_execute_node_input(authorized))
    assert isinstance(stopped, Graph.FailureOutcome)
    assert stopped.failure == "not executable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("pairing", "execution pairing"),
        ("binding", "execution binding"),
        ("digest", "execution arguments digest"),
    ],
)
async def test_execute_rejects_each_result_identity_mismatch_before_shared_hook(
    field: str,
    message: str,
) -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    resolved = _resolved()
    pairing = resolved.request.pairing
    binding = resolved.binding
    digest = resolved.arguments.digest
    if field == "pairing":
        pairing = ToolPairingIdentity(
            ToolExchangeScopeId("other-scope"),
            ActInvocationKey("other-invocation"),
            ToolCallId("other-call"),
        )
    elif field == "binding":
        binding = ToolBindingRef("other-binding")
    else:
        digest = ArgumentsDigest(b"other-digest")
    wrong_result = ToolExecutionResult(
        ToolExecutionIdentity(pairing, binding, digest),
        OpaqueExecutionOutcome(b"outcome"),
    )

    async def execute(_invocation: AuthorizedInvocation, /) -> ExecutePortResult:
        return wrong_result

    ports.execute_override = execute
    envelope = ActHookEnvelope(
        ActHookStage.AUTHORIZE,
        AuthorizeStageValue(AuthorizedInvocation(resolved)),
        _State(),
    )

    with pytest.raises(ActContractError, match=message):
        await ExecuteNode(ports, admission)(_execute_node_input(envelope))

    assert ports.execute_calls == 1
    assert ports.project_calls == 0
    assert ports.write_calls == 0


@pytest.mark.asyncio
async def test_settle_rejects_any_non_execute_hook_stage() -> None:
    admission = ActPayloadAdmission(_State, _Command)
    ports = _Ports()
    resolved = _resolved()
    wrong_stage = ActHookEnvelope(
        ActHookStage.AUTHORIZE,
        AuthorizeStageValue(AuthorizedInvocation(resolved)),
        _State(),
    )

    with pytest.raises(ActContractError, match="settle input"):
        await SettleNode(ports, ports, admission)(_settle_node_input(wrong_stage))


def test_act_assembly_requires_exact_admission_and_exact_hook_node() -> None:
    ports = _Ports()
    runtime = _HookRuntime()
    admission = ActPayloadAdmission(_State, _Command)
    hook = _hook("act.test", runtime, admission)

    with pytest.raises(ActContractError, match="ActPayloadAdmission"):
        _assemble(
            ports,
            hook,
            cast(ActPayloadAdmission[_State, _Command], object()),
        )
    with pytest.raises(ActContractError, match="shared HookNode"):
        _assemble(
            ports,
            cast(
                HookNode[_Config, _PriorityConfig, ActHookEnvelope, _State, _Command],
                object(),
            ),
            admission,
        )

    subclass = _ActHookSubclass(
        hook.slot,
        _ConfigSource(),
        _PlanLoader(),
        _HookRuntime(),
        hook.payload_admission,
    )
    with pytest.raises(ActContractError, match="shared HookNode"):
        _assemble(ports, subclass, admission)


@pytest.mark.parametrize("field", ["payload_admission", "slot"])
def test_act_assembly_rejects_a_forged_shared_hook_descriptor(field: str) -> None:
    ports = _Ports()
    admission = ActPayloadAdmission(_State, _Command)
    hook = _hook("act.test", _HookRuntime(), admission)
    object.__setattr__(
        hook,
        "_payload_admission" if field == "payload_admission" else "_slot",
        object(),
    )

    with pytest.raises(ActContractError, match=r"HookPayloadAdmission|HookSlotId"):
        _assemble(ports, hook, admission)


def test_act_assembly_rejects_wrong_hook_value_state_command_and_transition_contracts() -> None:
    ports = _Ports()
    admission = ActPayloadAdmission(_State, _Command)
    slot = HookSlotId(
        GraphDefinitionId("act.test"),
        GraphDefinitionVersion(1),
        GraphNodeId("hook"),
        HookStage.AFTER_NODE,
    )
    wrong_value = HookNode[
        _Config,
        _PriorityConfig,
        _OtherHookValue,
        _State,
        _Command,
    ](
        slot,
        _ConfigSource(),
        _PlanLoader(),
        _PassThroughHookRuntime[_OtherHookValue, _State, _Command](),
        HookPayloadAdmission(
            _Config,
            _PriorityConfig,
            _OtherHookValue,
            _State,
            _Command,
        ),
    )
    with pytest.raises(ActContractError, match="value type"):
        _assemble(
            ports,
            cast(
                HookNode[_Config, _PriorityConfig, ActHookEnvelope, _State, _Command],
                wrong_value,
            ),
            admission,
        )

    wrong_state = HookNode[
        _Config,
        _PriorityConfig,
        ActHookEnvelope,
        _OtherState,
        _Command,
    ](
        slot,
        _ConfigSource(),
        _PlanLoader(),
        _PassThroughHookRuntime[ActHookEnvelope, _OtherState, _Command](),
        HookPayloadAdmission(
            _Config,
            _PriorityConfig,
            ActHookEnvelope,
            _OtherState,
            _Command,
        ),
    )
    with pytest.raises(ActContractError, match="state type"):
        _assemble(
            ports,
            cast(
                HookNode[_Config, _PriorityConfig, ActHookEnvelope, _State, _Command],
                wrong_state,
            ),
            admission,
        )

    wrong_command = HookNode[
        _Config,
        _PriorityConfig,
        ActHookEnvelope,
        _State,
        _OtherCommand,
    ](
        slot,
        _ConfigSource(),
        _PlanLoader(),
        _PassThroughHookRuntime[ActHookEnvelope, _State, _OtherCommand](),
        HookPayloadAdmission(
            _Config,
            _PriorityConfig,
            ActHookEnvelope,
            _State,
            _OtherCommand,
        ),
    )
    with pytest.raises(ActContractError, match="command type"):
        _assemble(
            ports,
            cast(
                HookNode[_Config, _PriorityConfig, ActHookEnvelope, _State, _Command],
                wrong_command,
            ),
            admission,
        )

    no_transition = HookPayloadAdmission(
        _Config,
        _PriorityConfig,
        ActHookEnvelope,
        _State,
        _Command,
    )
    with pytest.raises(ActContractError, match="for transitions"):
        _assemble(
            ports,
            _hook(
                "act.test",
                _HookRuntime(),
                admission,
                payload_admission=no_transition,
            ),
            admission,
        )

    other_admission = ActPayloadAdmission(_State, _Command)
    with pytest.raises(ActContractError, match="for transitions"):
        _assemble(
            ports,
            _hook("act.test", _HookRuntime(), other_admission),
            admission,
        )


@pytest.mark.parametrize(
    ("definition_id", "version", "node_id", "corrupt_stage"),
    [
        ("other.act", 1, "hook", False),
        ("act.test", 2, "hook", False),
        ("act.test", 1, "other-hook", False),
        ("act.test", 1, "hook", True),
    ],
)
def test_act_assembly_rejects_each_hook_slot_coordinate_mismatch(
    definition_id: str,
    version: int,
    node_id: str,
    corrupt_stage: bool,
) -> None:
    admission = ActPayloadAdmission(_State, _Command)
    hook = _hook(
        definition_id,
        _HookRuntime(),
        admission,
        version=version,
        node_id=node_id,
    )
    if corrupt_stage:
        object.__setattr__(hook.slot, "stage", cast(HookStage, object()))

    with pytest.raises(ActContractError, match="HookSlotId"):
        _assemble(_Ports(), hook, admission)


@pytest.mark.asyncio
async def test_act_exposes_its_exact_shared_hook_and_freezes_after_compile() -> None:
    ports = _Ports()
    act = _act(ports, _HookRuntime())
    hook = act.hook
    assert type(hook) is HookNode

    awaiting = await act.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)

    async def late_node(_values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        return Graph.values()

    with pytest.raises(Graph.ValidationError, match="immutable"):
        act.add_node("late", late_node, inputs={}, outputs={})
    with pytest.raises(Graph.ValidationError, match="immutable"):
        act.add_edge("resolve", Graph.END)
    with pytest.raises(Graph.ValidationError, match="immutable"):
        act.set_outputs({"result": Graph.node_output("hook", "result")})
    with pytest.raises(Graph.ValidationError, match="immutable"):
        act.set_resume_codec(
            "replacement.codec",
            1,
            ports.encode_graph_input,
            ports.decode_graph_input,
        )


@pytest.mark.asyncio
async def test_act_rejects_a_wrong_graph_input_before_any_port_or_hook_call() -> None:
    ports = _Ports()
    runtime = _HookRuntime()
    act = _act(ports, runtime)

    with pytest.raises(Graph.ValueAdmissionError, match="exact declared type"):
        await act.run(Graph.values(request=cast(HookGraphValue, object())))

    assert (
        ports.resolve_calls,
        ports.request_authorization_calls,
        ports.execute_calls,
        ports.project_calls,
        ports.write_calls,
    ) == (0, 0, 0, 0, 0)
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_resume_helper_rejects_invalid_decision_unknown_interrupt_and_initial_input() -> None:
    ports = _Ports()
    act = _act(ports, _HookRuntime())
    awaiting = await act.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    interrupt_id = str(awaiting.interrupts[0].interrupt_id)

    with pytest.raises(ActContractError, match="Allow or Deny"):
        act.resume_authorization(
            awaiting=awaiting,
            interrupt_id=interrupt_id,
            decision=cast(AuthorizationDecision, object()),
        )
    with pytest.raises(ActContractError, match="exactly one"):
        act.resume_authorization(
            awaiting=awaiting,
            interrupt_id="unknown-interrupt",
            decision=Allow(),
        )

    resolved = ports.resolved
    assert resolved is not None

    def initial_resume(
        _interrupt: AuthorizationInterruptView,
        _decision: AuthorizationDecision,
        /,
    ) -> AuthorizationInput:
        return AuthorizationInput.initial(resolved)

    ports.build_resume_override = initial_resume
    with pytest.raises(ActContractError, match="ResumedAuthorization"):
        act.resume_authorization(
            awaiting=awaiting,
            interrupt_id=interrupt_id,
            decision=Allow(),
        )

    def wrong_outer(
        _interrupt: AuthorizationInterruptView,
        _decision: AuthorizationDecision,
        /,
    ) -> AuthorizationInput:
        return cast(AuthorizationInput, object())

    ports.build_resume_override = wrong_outer
    with pytest.raises(ActContractError, match="authorization input"):
        act.resume_authorization(
            awaiting=awaiting,
            interrupt_id=interrupt_id,
            decision=Allow(),
        )


@pytest.mark.asyncio
async def test_graph_rejects_duplicate_stale_and_wrong_scope_authorization_resume() -> None:
    ports = _Ports()
    act = _act(ports, _HookRuntime())
    awaiting = await act.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    action = act.resume_authorization(
        awaiting=awaiting,
        interrupt_id=str(awaiting.interrupts[0].interrupt_id),
        decision=Allow(),
    )

    with pytest.raises(Graph.Error):
        await act.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            resume=(action, action),
        )
    with pytest.raises(Graph.Error):
        await act.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            resume=(replace(action, scope=(GraphNodeId("wrong"),)),),
        )

    completed = await act.run(
        state=awaiting.state,
        continuation=awaiting.continuation,
        resume=(action,),
    )
    assert isinstance(completed, Graph.CompletedResult)
    with pytest.raises(Graph.Error):
        await act.run(
            state=completed.state,
            continuation=completed.continuation,
            resume=(action,),
        )


@pytest.mark.asyncio
async def test_nested_act_resume_uses_the_root_visible_interrupt_scope() -> None:
    ports = _Ports()
    runtime = _HookRuntime()
    act = _act(ports, runtime)
    parent = Graph[HookGraphValue]("act.parent")
    request_type = cast(type[HookGraphValue], ActRequest)
    parent.add_node(
        "act",
        act,
        inputs={"request": Graph.graph_input("request", request_type)},
    )
    parent.add_edge("act", "settle", Graph.END)
    parent.set_outputs({"result": Graph.node_output("act", "result")})

    awaiting = await parent.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    assert awaiting.interrupts[0].scope == ("act",)
    action = act.resume_authorization(
        awaiting=awaiting,
        interrupt_id=str(awaiting.interrupts[0].interrupt_id),
        decision=Allow(),
    )
    completed = await parent.run(
        state=awaiting.state,
        continuation=awaiting.continuation,
        resume=(action,),
    )

    assert isinstance(completed, Graph.CompletedResult)
    result = completed.outputs["result"]
    assert type(result) is HookResult
    assert cast(HookResult[ActHookEnvelope, _Command], result).value.stage is ActHookStage.SETTLE


@pytest.mark.asyncio
async def test_two_concurrent_act_runs_keep_authorization_and_results_isolated() -> None:
    ports = _ConcurrentPorts()
    runtime = _HookRuntime()
    act = _act(ports, runtime)
    first_request = _named_request("first", 11)
    second_request = _named_request("second", 22)

    first_waiting, second_waiting = await asyncio.gather(
        act.run(Graph.values(request=first_request), run_id="act-first"),
        act.run(Graph.values(request=second_request), run_id="act-second"),
    )
    assert isinstance(first_waiting, Graph.AwaitingResumeResult)
    assert isinstance(second_waiting, Graph.AwaitingResumeResult)
    first_action = act.resume_authorization(
        awaiting=first_waiting,
        interrupt_id=str(first_waiting.interrupts[0].interrupt_id),
        decision=Allow(),
    )
    second_action = act.resume_authorization(
        awaiting=second_waiting,
        interrupt_id=str(second_waiting.interrupts[0].interrupt_id),
        decision=Allow(),
    )

    first, second = await asyncio.gather(
        act.run(
            state=first_waiting.state,
            continuation=first_waiting.continuation,
            resume=(first_action,),
        ),
        act.run(
            state=second_waiting.state,
            continuation=second_waiting.continuation,
            resume=(second_action,),
        ),
    )

    assert isinstance(first, Graph.CompletedResult)
    assert isinstance(second, Graph.CompletedResult)
    assert first.state is not second.state
    outputs = tuple(cast(HookResult[ActHookEnvelope, _Command], result.outputs["result"]) for result in (first, second))
    by_turn = {cast(_State, output.value.hook_state).turn: output for output in outputs}
    assert set(by_turn) == {11, 22}
    assert cast(SettleStageValue, by_turn[11].value.payload).result.projection.source_identity.pairing == (
        first_request.pairing
    )
    assert cast(SettleStageValue, by_turn[22].value.payload).result.projection.source_identity.pairing == (
        second_request.pairing
    )
    assert (
        ports.resolve_calls,
        ports.request_authorization_calls,
        ports.execute_calls,
        ports.project_calls,
        ports.write_calls,
    ) == (2, 2, 2, 2, 2)
    assert len(runtime.calls) == 24
    assert {call.request.state.turn for call in runtime.calls} == {11, 22}


@pytest.mark.asyncio
async def test_caller_cancellation_while_execute_is_waiting_never_reaches_settle() -> None:
    ports = _Ports()
    blocking = _BlockingExecuteCall()
    ports.execute_override = blocking
    runtime = _HookRuntime()
    act = _act(ports, runtime)
    awaiting = await act.run(Graph.values(request=_request()))
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    action = act.resume_authorization(
        awaiting=awaiting,
        interrupt_id=str(awaiting.interrupts[0].interrupt_id),
        decision=Allow(),
    )
    task = asyncio.create_task(
        act.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            resume=(action,),
        )
    )

    await asyncio.wait_for(blocking.entered.wait(), timeout=1)
    task.cancel("caller cancelled Act execute")
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ports.execute_calls == 1
    assert ports.project_calls == 0
    assert ports.write_calls == 0
    assert len(runtime.calls) == 6


@pytest.mark.asyncio
async def test_caller_cancellation_while_shared_hook_is_waiting_never_advances() -> None:
    ports = _Ports()
    runtime = _BlockingHookRuntime()
    act = _act(ports, runtime)
    task = asyncio.create_task(act.run(Graph.values(request=_request())))

    await asyncio.wait_for(runtime.entered.wait(), timeout=1)
    task.cancel("caller cancelled Act hook")
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ports.resolve_calls == 1
    assert ports.request_authorization_calls == 0
    assert ports.execute_calls == 0
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_nested_act_propagates_root_cancellation_from_its_shared_hook() -> None:
    ports = _Ports()
    runtime = _BlockingHookRuntime()
    act = _act(ports, runtime)
    parent = Graph[HookGraphValue]("act.cancel.parent")
    request_type = cast(type[HookGraphValue], ActRequest)
    parent.add_node(
        "act",
        act,
        inputs={"request": Graph.graph_input("request", request_type)},
    )
    parent.set_outputs({"result": Graph.node_output("act", "result")})
    task = asyncio.create_task(
        parent.run(
            Graph.values(request=_request()),
            run_id="nested-act-cancel",
        )
    )

    await asyncio.wait_for(runtime.entered.wait(), timeout=1)
    task.cancel("caller cancelled nested Act")
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ports.resolve_calls == 1
    assert ports.request_authorization_calls == 0
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "mutation", "expected_calls"),
    [
        (ActHookStage.RESOLVE, "phase", (0, 0, 0, 0)),
        (ActHookStage.RESOLVE, "stage", (0, 0, 0, 0)),
        (ActHookStage.RESOLVE, "hook_state", (0, 0, 0, 0)),
        (ActHookStage.RESOLVE, "pairing", (0, 0, 0, 0)),
        (ActHookStage.RESOLVE, "binding", (0, 0, 0, 0)),
        (ActHookStage.RESOLVE, "digest", (0, 0, 0, 0)),
        (ActHookStage.AUTHORIZE, "authorized_binding", (1, 0, 0, 0)),
        (ActHookStage.EXECUTE, "execution_result", (1, 1, 0, 0)),
        (ActHookStage.SETTLE, "receipt", (1, 1, 1, 1)),
    ],
)
async def test_shared_hook_cannot_rewrite_any_act_stage_fact(
    target: ActHookStage,
    mutation: str,
    expected_calls: tuple[int, int, int, int],
) -> None:
    ports = _Ports()
    runtime = _MutatingHookRuntime(target, mutation)
    act = _act(ports, runtime)

    if target is ActHookStage.RESOLVE:
        with pytest.raises(ActContractError):
            await act.run(Graph.values(request=_request()))
    else:
        awaiting = await act.run(Graph.values(request=_request()))
        assert isinstance(awaiting, Graph.AwaitingResumeResult)
        action = act.resume_authorization(
            awaiting=awaiting,
            interrupt_id=str(awaiting.interrupts[0].interrupt_id),
            decision=Allow(),
        )
        with pytest.raises(ActContractError):
            await act.run(
                state=awaiting.state,
                continuation=awaiting.continuation,
                resume=(action,),
            )

    assert (
        ports.request_authorization_calls,
        ports.execute_calls,
        ports.project_calls,
        ports.write_calls,
    ) == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "expected_hook_calls", "expected_port_calls"),
    [
        ("resolve", 0, (1, 0, 0, 0, 0)),
        ("authorize", 3, (1, 1, 0, 0, 0)),
        ("execute", 6, (1, 1, 1, 0, 0)),
        ("project", 9, (1, 1, 1, 1, 0)),
        ("write", 9, (1, 1, 1, 1, 1)),
    ],
)
async def test_full_act_port_failure_stops_before_every_later_stage(
    stage: str,
    expected_hook_calls: int,
    expected_port_calls: tuple[int, int, int, int, int],
) -> None:
    ports = _Ports()
    failing = _RaisingAsyncCall(RuntimeError(f"{stage} failure"))
    if stage == "resolve":
        ports.resolve_override = failing
    elif stage == "authorize":
        ports.authorize_override = failing
    elif stage == "execute":
        ports.execute_override = failing
    elif stage == "project":
        ports.project_override = failing
    else:
        ports.write_override = failing
    runtime = _HookRuntime()
    act = _act(ports, runtime)

    if stage in {"resolve", "authorize"}:
        with pytest.raises(RuntimeError) as raised:
            await act.run(Graph.values(request=_request()))
    else:
        awaiting = await act.run(Graph.values(request=_request()))
        assert isinstance(awaiting, Graph.AwaitingResumeResult)
        action = act.resume_authorization(
            awaiting=awaiting,
            interrupt_id=str(awaiting.interrupts[0].interrupt_id),
            decision=Allow(),
        )
        with pytest.raises(RuntimeError) as raised:
            await act.run(
                state=awaiting.state,
                continuation=awaiting.continuation,
                resume=(action,),
            )

    assert raised.value is failing.failure
    assert len(runtime.calls) == expected_hook_calls
    assert (
        ports.resolve_calls,
        ports.request_authorization_calls,
        ports.execute_calls,
        ports.project_calls,
        ports.write_calls,
    ) == expected_port_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "expected_hook_calls", "expected_port_calls"),
    [
        (ActHookStage.RESOLVE, 1, (1, 0, 0, 0, 0)),
        (ActHookStage.AUTHORIZE, 4, (1, 1, 0, 0, 0)),
        (ActHookStage.EXECUTE, 7, (1, 1, 1, 0, 0)),
        (ActHookStage.SETTLE, 10, (1, 1, 1, 1, 1)),
    ],
)
async def test_full_act_hook_failure_stops_before_the_next_business_stage(
    target: ActHookStage,
    expected_hook_calls: int,
    expected_port_calls: tuple[int, int, int, int, int],
) -> None:
    ports = _Ports()
    runtime = _FailingHookRuntime(target)
    act = _act(ports, runtime)

    if target is ActHookStage.RESOLVE:
        with pytest.raises(RuntimeError) as raised:
            await act.run(Graph.values(request=_request()))
    else:
        awaiting = await act.run(Graph.values(request=_request()))
        assert isinstance(awaiting, Graph.AwaitingResumeResult)
        action = act.resume_authorization(
            awaiting=awaiting,
            interrupt_id=str(awaiting.interrupts[0].interrupt_id),
            decision=Allow(),
        )
        with pytest.raises(RuntimeError) as raised:
            await act.run(
                state=awaiting.state,
                continuation=awaiting.continuation,
                resume=(action,),
            )

    assert raised.value is runtime.failure
    assert len(runtime.calls) == expected_hook_calls
    assert (
        ports.resolve_calls,
        ports.request_authorization_calls,
        ports.execute_calls,
        ports.project_calls,
        ports.write_calls,
    ) == expected_port_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolve", "authorize", "execute", "project", "write"])
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("port failed"), asyncio.CancelledError("port cancelled")],
    ids=["exception", "cancellation"],
)
async def test_each_async_port_preserves_exception_and_cancellation(stage: str, failure: BaseException) -> None:
    ports = _Ports()
    failing = _RaisingAsyncCall(failure)
    if stage == "resolve":
        ports.resolve_override = failing
    elif stage == "authorize":
        ports.authorize_override = failing
    elif stage == "execute":
        ports.execute_override = failing
    elif stage == "project":
        ports.project_override = failing
    else:
        ports.write_override = failing

    with pytest.raises((RuntimeError, asyncio.CancelledError)) as raised:
        await _invoke_external_stage(stage, ports)

    assert raised.value is failure
    if stage == "project":
        assert ports.write_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolve", "authorize", "execute", "project", "write"])
async def test_each_async_port_rejects_a_synchronous_result(stage: str) -> None:
    ports = _Ports()
    resolved = _resolved()
    synchronous_result: object
    if stage == "resolve":
        synchronous_result = resolved
    elif stage == "authorize":
        synchronous_result = AuthorizationRequestRef(
            resolved.request.pairing,
            OpaqueAuthorizationHandle(b"handle"),
        )
    elif stage == "execute":
        synchronous_result = _execution(resolved)
    elif stage == "project":
        synchronous_result = SettlementProjection(
            _execution(resolved).identity,
            OpaqueProtocolPayload(b"projection"),
        )
    else:
        synchronous_result = ToolExchangeWriteResult(OpaqueToolExchangeReceipt(b"receipt"))

    def synchronous(_value: object, /) -> object:
        return synchronous_result

    if stage == "resolve":
        ports.resolve_override = cast(_ResolveCall, synchronous)
    elif stage == "authorize":
        ports.authorize_override = cast(_AuthorizeCall, synchronous)
    elif stage == "execute":
        ports.execute_override = cast(_ExecuteCall, synchronous)
    elif stage == "project":
        ports.project_override = cast(_ProjectCall, synchronous)
    else:
        ports.write_override = cast(_WriteCall, synchronous)

    with pytest.raises(TypeError, match="await"):
        await _invoke_external_stage(stage, ports)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolve", "authorize", "execute", "project", "write"])
async def test_each_async_port_preserves_wrong_arity_type_error(stage: str) -> None:
    ports = _Ports()

    def wrong_arity() -> None:
        return None

    if stage == "resolve":
        ports.resolve_override = cast(_ResolveCall, wrong_arity)
    elif stage == "authorize":
        ports.authorize_override = cast(_AuthorizeCall, wrong_arity)
    elif stage == "execute":
        ports.execute_override = cast(_ExecuteCall, wrong_arity)
    elif stage == "project":
        ports.project_override = cast(_ProjectCall, wrong_arity)
    else:
        ports.write_override = cast(_WriteCall, wrong_arity)

    with pytest.raises(TypeError, match="argument"):
        await _invoke_external_stage(stage, ports)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolve", "authorize", "execute", "project", "write"])
async def test_each_async_port_rejects_a_wrong_result_outer_class(stage: str) -> None:
    ports = _Ports()

    async def wrong_result(_value: object, /) -> object:
        return object()

    if stage == "resolve":
        ports.resolve_override = cast(_ResolveCall, wrong_result)
    elif stage == "authorize":
        ports.authorize_override = cast(_AuthorizeCall, wrong_result)
    elif stage == "execute":
        ports.execute_override = cast(_ExecuteCall, wrong_result)
    elif stage == "project":
        ports.project_override = cast(_ProjectCall, wrong_result)
    else:
        ports.write_override = cast(_WriteCall, wrong_result)

    with pytest.raises(ActContractError):
        await _invoke_external_stage(stage, ports)


@pytest.mark.asyncio
async def test_settle_rejects_projection_with_another_execution_identity_before_write() -> None:
    ports = _Ports()
    resolved = _resolved()
    other_identity = ToolExecutionIdentity(
        resolved.request.pairing,
        ToolBindingRef("other-binding"),
        resolved.arguments.digest,
    )

    async def wrong_projection(_result: ToolExecutionResult, /) -> SettlementProjection:
        return SettlementProjection(
            other_identity,
            OpaqueProtocolPayload(b"projection"),
        )

    ports.project_override = wrong_projection

    with pytest.raises(ActContractError, match="settlement source identity"):
        await _invoke_external_stage("project", ports)

    assert ports.write_calls == 0
