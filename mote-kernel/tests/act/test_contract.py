"""Contract and admission tests for the Phase 0 Act values."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from typing import cast

import pytest

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    ActHookStage,
    ActRequest,
    ActSlotId,
    ActStageValue,
    Allow,
    ArgumentsDigest,
    AuthorizationDecision,
    AuthorizationInput,
    AuthorizationInterruptView,
    AuthorizationPhase,
    AuthorizationRequestRef,
    AuthorizedInvocation,
    AuthorizeNodeInput,
    AuthorizeStageValue,
    CanonicalArguments,
    Deny,
    ExecuteNodeInput,
    ExecutePortResult,
    ExecuteStageValue,
    ExecutionStopped,
    HookGraphValue,
    HookRequest,
    HookResult,
    HookStateProjection,
    InitialAuthorization,
    ResolutionStopped,
    ResolvedInvocation,
    ResolvePortResult,
    ResolveStageValue,
    ResumedAuthorization,
    SettledActResult,
    SettlementProjection,
    SettleNodeInput,
    SettleStageValue,
    ToolExchangeWriteRequest,
    ToolExchangeWriteResult,
    ToolExecutionResult,
)
from mote_kernel.act.identity import (
    ActIdentityError,
    ActInvocationKey,
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
from mote_kernel.hooks.contract import HookStageResult
from mote_kernel.state.graph_state import GraphNodeId


@dataclass(frozen=True, slots=True)
class _HookState(HookStateProjection):
    marker: str = "state"


@dataclass(frozen=True, slots=True)
class _HookCommand(ActHookCommand):
    ordinal: int = 1


@dataclass(frozen=True, slots=True)
class _OtherHookState(HookStateProjection):
    marker: str = "other"


@dataclass(frozen=True, slots=True)
class _OtherHookCommand(ActHookCommand):
    ordinal: int = 2


def _admission() -> ActPayloadAdmission[_HookState, _HookCommand]:
    return ActPayloadAdmission(_HookState, _HookCommand)


def _parts() -> tuple[_HookState, ToolPairingIdentity, ActRequest, ResolvedInvocation]:
    state = _HookState()
    pairing = ToolPairingIdentity(
        ToolExchangeScopeId("root"),
        ActInvocationKey("invocation"),
        ToolCallId("call"),
    )
    request = ActRequest(
        pairing,
        ToolSelector("search"),
        OpaqueArguments(b"{}"),
        CallerIdentityRef(b"caller"),
        state,
    )
    resolved = ResolvedInvocation(
        request,
        OpaqueDefinitionReference("definition"),
        ToolBindingRef("binding"),
        CanonicalArguments(OpaqueArguments(b"{}"), ArgumentsDigest(b"digest")),
    )
    return state, pairing, request, resolved


def _execution(resolved: ResolvedInvocation) -> tuple[ToolExecutionIdentity, ToolExecutionResult]:
    identity = ToolExecutionIdentity(
        resolved.request.pairing,
        resolved.binding,
        resolved.arguments.digest,
    )
    return identity, ToolExecutionResult(identity, OpaqueExecutionOutcome(b"outcome"))


def _envelope(stage: ActHookStage, state: _HookState, resolved: ResolvedInvocation) -> ActHookEnvelope:
    if stage is ActHookStage.RESOLVE:
        payload = ResolveStageValue(AuthorizationInput.initial(resolved))
    elif stage is ActHookStage.AUTHORIZE:
        payload = AuthorizeStageValue(AuthorizedInvocation(resolved))
    elif stage is ActHookStage.EXECUTE:
        _, result = _execution(resolved)
        payload = ExecuteStageValue(result)
    else:
        identity, _ = _execution(resolved)
        projection = SettlementProjection(identity, OpaqueProtocolPayload(b"projection"))
        settled = SettledActResult(projection, OpaqueToolExchangeReceipt(b"receipt"))
        payload = SettleStageValue(settled)
    return ActHookEnvelope(stage, payload, state)


@pytest.mark.parametrize(
    "wrapper",
    [
        ToolCallId,
        ToolExchangeScopeId,
        ActInvocationKey,
        ToolSelector,
        ToolBindingRef,
        OpaqueDefinitionReference,
    ],
)
def test_text_identity_wrappers_are_canonical_and_utf8_bounded(wrapper: Callable[[str], HookGraphValue]) -> None:
    wrapper("ok")
    with pytest.raises(ActIdentityError):
        wrapper(" bad ")
    with pytest.raises(ActIdentityError):
        wrapper("x" * 257)
    with pytest.raises(ActIdentityError):
        wrapper("é" * 129)
    with pytest.raises(ActIdentityError):
        wrapper("bad\nvalue")
    with pytest.raises(ActIdentityError):
        wrapper(cast(str, "\ud800"))


def test_opaque_bytes_and_digest_wrappers_enforce_types_and_limits() -> None:
    assert CallerIdentityRef(b"").value == b""
    assert OpaqueArguments(b"").value == b""
    assert ArgumentsDigest(b"d").value == b"d"
    with pytest.raises(ActIdentityError):
        ArgumentsDigest(b"")
    with pytest.raises(ActIdentityError):
        OpaqueAuthorizationHandle(b"x" * 4_097)
    with pytest.raises(ActIdentityError):
        OpaqueArguments(b"x" * 65_537)
    with pytest.raises(ActIdentityError):
        ArgumentsDigest(b"x" * 129)
    with pytest.raises(ActIdentityError):
        CallerIdentityRef(cast(bytes, bytearray(b"mutable")))


@pytest.mark.parametrize(
    ("wrapper", "limit"),
    [
        (CallerIdentityRef, 4_096),
        (OpaqueAuthorizationHandle, 4_096),
        (OpaqueArguments, 65_536),
        (ArgumentsDigest, 128),
        (OpaqueExecutionOutcome, 1_048_576),
        (OpaqueProtocolPayload, 1_048_576),
        (OpaqueToolExchangeReceipt, 1_048_576),
    ],
)
def test_each_opaque_wrapper_enforces_its_exact_byte_limit(
    wrapper: Callable[[bytes], HookGraphValue],
    limit: int,
) -> None:
    assert wrapper(b"x" * limit)
    with pytest.raises(ActIdentityError):
        wrapper(b"x" * (limit + 1))
    with pytest.raises(ActIdentityError):
        wrapper(cast(bytes, bytearray(b"mutable")))


def test_graph_failure_reason_has_its_own_text_limit() -> None:
    assert OpaqueGraphFailureReason("x" * 512).value == "x" * 512
    with pytest.raises(ActIdentityError):
        OpaqueGraphFailureReason("x" * 513)


def test_identity_records_are_immutable_and_slot_based() -> None:
    state, pairing, _, _ = _parts()
    assert not hasattr(pairing, "__dict__")
    with pytest.raises(FrozenInstanceError):
        pairing.tool_call_id = ToolCallId("other")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        state.marker = "changed"  # type: ignore[misc]


def test_identity_nested_records_require_their_exact_nominal_types() -> None:
    _, pairing, _, resolved = _parts()
    with pytest.raises(ActIdentityError):
        ToolPairingIdentity(cast(ToolExchangeScopeId, object()), pairing.invocation, pairing.tool_call_id)
    with pytest.raises(ActIdentityError):
        ToolPairingIdentity(pairing.scope, cast(ActInvocationKey, object()), pairing.tool_call_id)
    with pytest.raises(ActIdentityError):
        ToolPairingIdentity(pairing.scope, pairing.invocation, cast(ToolCallId, object()))
    with pytest.raises(ActIdentityError):
        ToolExecutionIdentity(cast(ToolPairingIdentity, object()), resolved.binding, resolved.arguments.digest)
    with pytest.raises(ActIdentityError):
        ToolExecutionIdentity(pairing, cast(ToolBindingRef, object()), resolved.arguments.digest)
    with pytest.raises(ActIdentityError):
        ToolExecutionIdentity(pairing, resolved.binding, cast(ArgumentsDigest, object()))


@pytest.mark.parametrize("node_id", ["resolve", "authorize", "execute", "settle"])
def test_act_slot_accepts_only_the_four_business_nodes(node_id: str) -> None:
    slot = ActSlotId("act.definition", 2, node_id)
    assert slot.node_id == node_id
    with pytest.raises(ActIdentityError):
        ActSlotId("act.definition", 0, node_id)
    with pytest.raises(ActIdentityError):
        ActSlotId("act.definition", True, node_id)  # type: ignore[arg-type]
    with pytest.raises(ActIdentityError):
        ActSlotId("act.definition", 1, "hook")


def test_authorization_phases_and_decisions_preserve_pairing() -> None:
    _, pairing, _, resolved = _parts()
    request_ref = AuthorizationRequestRef(pairing, OpaqueAuthorizationHandle(b"handle"))
    initial = AuthorizationInput.initial(resolved)
    resumed = AuthorizationInput.resumed(resolved, request_ref, Allow())
    assert type(initial.phase) is InitialAuthorization
    assert type(resumed.phase) is ResumedAuthorization
    assert resumed.phase.decision == Allow()
    assert Deny() != Allow()
    with pytest.raises(ActContractError):
        ResumedAuthorization(resolved, request_ref, cast(AuthorizationDecision, object()))
    other_pairing = ToolPairingIdentity(
        ToolExchangeScopeId("other"),
        ActInvocationKey("invocation"),
        ToolCallId("call"),
    )
    with pytest.raises(ActContractError):
        ResumedAuthorization(
            resolved,
            AuthorizationRequestRef(other_pairing, OpaqueAuthorizationHandle(b"handle")),
            Deny(),
        )
    with pytest.raises(ActContractError):
        InitialAuthorization(cast(ResolvedInvocation, object()))
    with pytest.raises(ActContractError):
        AuthorizationRequestRef(cast(ToolPairingIdentity, object()), request_ref.handle)
    with pytest.raises(ActContractError):
        AuthorizationRequestRef(resolved.request.pairing, cast(OpaqueAuthorizationHandle, object()))
    with pytest.raises(ActContractError):
        AuthorizationInput(cast(AuthorizationPhase, object()))


def test_stage_envelope_has_one_concrete_payload_and_no_next_hop() -> None:
    state, _, _, resolved = _parts()
    for stage in ActHookStage:
        envelope = _envelope(stage, state, resolved)
        assert envelope.stage is stage
        assert envelope.hook_state == state
        assert not hasattr(envelope, "route")
    with pytest.raises(ActContractError):
        ActHookEnvelope(
            ActHookStage.RESOLVE,
            cast(ActStageValue, object()),
            state,
        )
    with pytest.raises(ActContractError):
        ActHookEnvelope(
            cast(ActHookStage, object()),
            ResolveStageValue(AuthorizationInput.initial(resolved)),
            state,
        )


def test_business_node_input_dtos_are_exact_frozen_hook_handoffs() -> None:
    state, _, _, resolved = _parts()
    cases = (
        AuthorizeNodeInput(
            HookResult(
                _envelope(ActHookStage.RESOLVE, state, resolved),
                (_HookCommand(),),
                GraphNodeId("resolve"),
            )
        ),
        ExecuteNodeInput(
            HookResult(
                _envelope(ActHookStage.AUTHORIZE, state, resolved),
                (_HookCommand(),),
                GraphNodeId("authorize"),
            )
        ),
        SettleNodeInput(
            HookResult(
                _envelope(ActHookStage.EXECUTE, state, resolved),
                (_HookCommand(),),
                GraphNodeId("execute"),
            )
        ),
    )

    assert tuple(value.hook_result.node_id for value in cases) == (
        GraphNodeId("resolve"),
        GraphNodeId("authorize"),
        GraphNodeId("execute"),
    )
    for value in cases:
        with pytest.raises(FrozenInstanceError):
            value.hook_result = value.hook_result  # type: ignore[misc]


def test_interrupt_view_allows_root_scope_and_rejects_malformed_fields() -> None:
    view = AuthorizationInterruptView((), "authorize", "interrupt", b"")
    assert view.scope == ()
    with pytest.raises(ActContractError):
        AuthorizationInterruptView(tuple(f"n{index}" for index in range(17)), "authorize", "interrupt", b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((" bad",), "authorize", "interrupt", b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((cast(str, object()),), "authorize", "interrupt", b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((), "execute", "interrupt", b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((), "authorize", "bad\ninterrupt", b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((), "authorize", "interrupt", b"x" * 65_537)
    with pytest.raises(ActContractError):
        AuthorizationInterruptView(("x" * 257,), "authorize", "interrupt", b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView(("é" * 2_049,), "authorize", "interrupt", b"")
    max_scope = AuthorizationInterruptView(tuple("é" * 128 for _ in range(16)), "authorize", "interrupt", b"")
    assert len(max_scope.scope) == 16
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((cast(str, "\ud800"),), "authorize", "interrupt", b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((), "authorize", "x" * 257, b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((), "authorize", cast(str, "\ud800"), b"")
    with pytest.raises(ActContractError):
        AuthorizationInterruptView((), "authorize", "interrupt", cast(bytes, cast(object, bytearray())))


def test_admission_accepts_valid_values_and_checks_identity_relationships() -> None:
    admission = _admission()
    state, pairing, request, resolved = _parts()
    request_ref = AuthorizationRequestRef(pairing, OpaqueAuthorizationHandle(b"handle"))
    authorized = AuthorizedInvocation(resolved)
    identity, execution = _execution(resolved)
    projection = SettlementProjection(identity, OpaqueProtocolPayload(b"projection"))
    write_request = ToolExchangeWriteRequest(projection)
    write_result = ToolExchangeWriteResult(OpaqueToolExchangeReceipt(b"receipt"))
    settled = SettledActResult(projection, write_result.receipt)
    hook_request: HookRequest[ActHookEnvelope, _HookState] = HookRequest(
        _envelope(ActHookStage.EXECUTE, state, resolved),
        state,
    )
    hook_result: HookResult[ActHookEnvelope, _HookCommand] = HookResult(
        hook_request.value,
        (_HookCommand(),),
    )

    assert admission.admit_act_slot(ActSlotId("definition", 1, "resolve"))
    assert admission.admit_graph_failure_reason(OpaqueGraphFailureReason("failure"))
    assert admission.admit_request(request) is request
    assert admission.admit_resolved_invocation(resolved) is resolved
    assert admission.admit_authorization_request_ref(request_ref) is request_ref
    assert admission.admit_interrupt_view(AuthorizationInterruptView((), "authorize", "id", b""))
    assert admission.admit_interrupt_payload(b"payload") == b"payload"
    assert admission.admit_authorization_input(AuthorizationInput.initial(resolved))
    assert admission.admit_authorization_phase(AuthorizationInput.initial(resolved).phase)
    assert admission.admit_authorization_decision(Allow())
    assert admission.admit_authorized_invocation(authorized) is authorized
    assert admission.admit_resolution_result(resolved) is resolved
    assert admission.admit_resolution_result(ResolutionStopped(OpaqueGraphFailureReason("stop")))
    assert admission.admit_execution_result(authorized, execution) is execution
    assert admission.admit_execution_outcome(execution) is execution
    assert admission.admit_execution_outcome(ExecutionStopped(OpaqueGraphFailureReason("stop")))
    assert admission.admit_settlement_projection(execution, projection) is projection
    assert admission.admit_write_request(write_request) is write_request
    assert admission.admit_write_result(write_result) is write_result
    assert admission.admit_settled_result(settled) is settled
    assert admission.admit_hook_envelope(hook_request.value) is hook_request.value
    assert admission.admit_hook_request(hook_request) is hook_request
    assert admission.admit_hook_result(hook_result) is hook_result

    other_identity = ToolExecutionIdentity(
        pairing,
        ToolBindingRef("different-binding"),
        ArgumentsDigest(b"different-digest"),
    )
    with pytest.raises(ActContractError):
        admission.admit_execution_result(authorized, ToolExecutionResult(other_identity, execution.outcome))
    with pytest.raises(ActContractError):
        admission.admit_settlement_projection(
            execution,
            SettlementProjection(other_identity, OpaqueProtocolPayload(b"projection")),
        )


def test_admission_fails_closed_for_wrong_outer_classes_and_hook_state() -> None:
    admission = _admission()
    state, pairing, _, resolved = _parts()
    envelope = _envelope(ActHookStage.RESOLVE, state, resolved)
    with pytest.raises(ActContractError):
        admission.admit_request(cast(ActRequest, object()))
    with pytest.raises(ActContractError):
        admission.admit_interrupt_payload(cast(bytes, cast(object, bytearray(b"payload"))))
    with pytest.raises(ActContractError):
        admission.admit_interrupt_payload(b"x" * 65_537)
    with pytest.raises(ActContractError):
        admission.admit_authorization_phase(cast(AuthorizationPhase, object()))
    with pytest.raises(ActContractError):
        admission.admit_authorization_decision(cast(AuthorizationDecision, object()))
    with pytest.raises(ActContractError):
        ActRequest(
            pairing=cast(ToolPairingIdentity, object()),
            selector=ToolSelector("s"),
            arguments=OpaqueArguments(b""),
            caller=CallerIdentityRef(b""),
            hook_state=state,
        )
    with pytest.raises(ActContractError):
        ActRequest(
            pairing=state,  # type: ignore[arg-type]
            selector=ToolSelector("s"),
            arguments=OpaqueArguments(b""),
            caller=CallerIdentityRef(b""),
            hook_state=state,
        )
    with pytest.raises(ActContractError):
        ActRequest(
            pairing=pairing,
            selector=cast(ToolSelector, object()),
            arguments=OpaqueArguments(b""),
            caller=CallerIdentityRef(b""),
            hook_state=state,
        )
    with pytest.raises(ActContractError):
        ActRequest(
            pairing=pairing,
            selector=ToolSelector("s"),
            arguments=cast(OpaqueArguments, object()),
            caller=CallerIdentityRef(b""),
            hook_state=state,
        )
    with pytest.raises(ActContractError):
        ActRequest(
            pairing=pairing,
            selector=ToolSelector("s"),
            arguments=OpaqueArguments(b""),
            caller=cast(CallerIdentityRef, object()),
            hook_state=state,
        )
    with pytest.raises(ActContractError):
        ActRequest(
            pairing=pairing,
            selector=ToolSelector("s"),
            arguments=OpaqueArguments(b""),
            caller=CallerIdentityRef(b""),
            hook_state=cast(HookStateProjection, object()),
        )
    with pytest.raises(ActContractError):
        admission.admit_hook_request(
            cast(
                HookRequest[ActHookEnvelope, _HookState],
                HookRequest(envelope, HookStateProjection()),
            )
        )
    with pytest.raises(ActContractError):
        admission.admit_hook_request(HookRequest(envelope, _HookState("other")))
    with pytest.raises(ActContractError):
        admission.admit_hook_result(HookResult(envelope, (ActHookCommand(),)))
    with pytest.raises(ActContractError):
        admission.admit_hook_result(cast(HookResult[ActHookEnvelope, ActHookCommand], object()))
    with pytest.raises(ActContractError):
        admission.admit_hook_envelope(cast(ActHookEnvelope, object()))
    with pytest.raises(ActContractError):
        admission.admit_resolution_result(cast(ResolvePortResult, object()))
    with pytest.raises(ActContractError):
        admission.admit_execution_outcome(cast(ExecutePortResult, object()))


def test_admission_requires_concrete_hook_state_and_command_types() -> None:
    with pytest.raises(ActContractError, match="state type"):
        ActPayloadAdmission(cast(type[_HookState], HookStateProjection), _HookCommand)
    with pytest.raises(ActContractError, match="command type"):
        ActPayloadAdmission(_HookState, cast(type[_HookCommand], ActHookCommand))
    with pytest.raises(ActContractError, match="concrete classes"):
        ActPayloadAdmission(cast(type[_HookState], object()), _HookCommand)


def test_admission_rejects_each_stage_payload_mismatch_and_unknown_stage() -> None:
    state, _, _, resolved = _parts()
    identity, _ = _execution(resolved)
    settled = SettledActResult(
        SettlementProjection(identity, OpaqueProtocolPayload(b"projection")),
        OpaqueToolExchangeReceipt(b"receipt"),
    )
    mismatches = (
        ActHookEnvelope(
            ActHookStage.RESOLVE,
            AuthorizeStageValue(AuthorizedInvocation(resolved)),
            state,
        ),
        ActHookEnvelope(
            ActHookStage.AUTHORIZE,
            ExecuteStageValue(_execution(resolved)[1]),
            state,
        ),
        ActHookEnvelope(
            ActHookStage.EXECUTE,
            SettleStageValue(settled),
            state,
        ),
        ActHookEnvelope(
            ActHookStage.SETTLE,
            ResolveStageValue(AuthorizationInput.initial(resolved)),
            state,
        ),
    )
    for envelope in mismatches:
        with pytest.raises(ActContractError, match="invalid stage payload"):
            _admission().admit_hook_envelope(envelope)

    malformed = object.__new__(ActHookEnvelope)
    object.__setattr__(malformed, "stage", cast(ActHookStage, "unknown"))
    object.__setattr__(malformed, "payload", ResolveStageValue(AuthorizationInput.initial(resolved)))
    object.__setattr__(malformed, "hook_state", state)
    with pytest.raises(ActContractError, match="unknown stage"):
        _admission().admit_hook_envelope(malformed)


def test_hook_request_and_result_admission_rejects_every_concrete_boundary() -> None:
    admission = _admission()
    state, _, _, resolved = _parts()
    envelope = _envelope(ActHookStage.RESOLVE, state, resolved)

    assert admission.admit_hook_state(state) is state
    with pytest.raises(ActContractError, match="unexpected concrete type"):
        admission.admit_hook_state(_OtherHookState())

    wrong_state_request = cast(
        HookRequest[ActHookEnvelope, _HookState],
        HookRequest(envelope, _OtherHookState()),
    )
    with pytest.raises(ActContractError, match="request state"):
        admission.admit_hook_request(wrong_state_request)

    other_state_envelope = ActHookEnvelope(
        envelope.stage,
        envelope.payload,
        _OtherHookState(),
    )
    wrong_envelope_state = HookRequest(other_state_envelope, state)
    with pytest.raises(ActContractError, match="envelope state"):
        admission.admit_hook_request(wrong_envelope_state)

    wrong_node = HookRequest(envelope, state, GraphNodeId("execute"))
    with pytest.raises(ActContractError, match="node_id"):
        admission.admit_hook_request(wrong_node)

    wrong_state_result: HookResult[ActHookEnvelope, _HookCommand] = HookResult(other_state_envelope)
    with pytest.raises(ActContractError, match="result state"):
        admission.admit_hook_result(wrong_state_result)

    malformed_result = cast(HookResult[ActHookEnvelope, _HookCommand], object.__new__(HookResult))
    object.__setattr__(malformed_result, "value", envelope)
    object.__setattr__(malformed_result, "commands", [])
    object.__setattr__(malformed_result, "node_id", None)
    with pytest.raises(ActContractError, match="commands must be a tuple"):
        admission.admit_hook_result(malformed_result)

    wrong_command = cast(
        HookResult[ActHookEnvelope, _HookCommand],
        HookResult(envelope, (_OtherHookCommand(),)),
    )
    with pytest.raises(ActContractError, match="commands have an unexpected concrete type"):
        admission.admit_hook_result(wrong_command)

    wrong_result_node: HookResult[ActHookEnvelope, _HookCommand] = HookResult(
        envelope,
        (),
        GraphNodeId("execute"),
    )
    with pytest.raises(ActContractError, match="node_id"):
        admission.admit_hook_result(wrong_result_node)


def test_transition_admission_preserves_the_complete_envelope_and_concrete_commands() -> None:
    admission = _admission()
    state, _, _, resolved = _parts()
    envelope = _envelope(ActHookStage.RESOLVE, state, resolved)
    request = HookRequest(envelope, state, GraphNodeId("resolve"))
    admission.admit_transition(request, HookStageResult(envelope, (_HookCommand(),)))

    with pytest.raises(ActContractError, match="HookStageResult"):
        admission.admit_transition(
            request,
            cast(HookStageResult[ActHookEnvelope, _HookCommand], object()),
        )

    other_state_envelope = ActHookEnvelope(
        envelope.stage,
        envelope.payload,
        _OtherHookState(),
    )
    with pytest.raises(ActContractError, match="stage result state"):
        admission.admit_transition(
            request,
            cast(
                HookStageResult[ActHookEnvelope, _HookCommand],
                HookStageResult(other_state_envelope),
            ),
        )

    malformed_result = cast(
        HookStageResult[ActHookEnvelope, _HookCommand],
        object.__new__(HookStageResult),
    )
    object.__setattr__(malformed_result, "value", envelope)
    object.__setattr__(malformed_result, "commands", [])
    with pytest.raises(ActContractError, match="stage commands must be a tuple"):
        admission.admit_transition(request, malformed_result)

    with pytest.raises(ActContractError, match="stage commands have an unexpected concrete type"):
        admission.admit_transition(
            request,
            cast(
                HookStageResult[ActHookEnvelope, _HookCommand],
                HookStageResult(envelope, (_OtherHookCommand(),)),
            ),
        )

    changed = ActHookEnvelope(
        ActHookStage.RESOLVE,
        ResolveStageValue(
            AuthorizationInput.initial(
                ResolvedInvocation(
                    resolved.request,
                    resolved.definition,
                    ToolBindingRef("changed-binding"),
                    resolved.arguments,
                )
            )
        ),
        state,
    )
    with pytest.raises(ActContractError, match="stage value does not match"):
        admission.admit_transition(request, HookStageResult(changed))
