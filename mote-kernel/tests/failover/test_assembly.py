"""Execution tests for the single public Failover Port decorator."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from typing import Never, Protocol, TypeAlias, cast

import pytest

import mote_kernel.failover as failover_package
import mote_kernel.failover.assembly as assembly_module
from mote_kernel.execution import Graph
from mote_kernel.failover import Failover
from mote_kernel.failover.assembly import FailoverCall, FailoverResult
from mote_kernel.failover.contract import (
    Completed,
    ErrorHint,
    FailoverContractError,
    FailureClass,
    FailureEvidence,
    FailureStrategy,
    InProgress,
    PortOutcome,
    PreparationAction,
    PreparedRequest,
    RefreshCredential,
    Rejected,
    RotateCredential,
    SwitchEndpoint,
    TransformRequest,
    Unknown,
    Wait,
)
from mote_kernel.failover.plan import (
    FailoverConfigRevision,
    FailoverOperationId,
    FailoverPlan,
    FailoverPortId,
    FailoverProfile,
    FailoverProfileId,
    RetryContext,
)
from mote_kernel.failover.policy import FailoverDecision, ObservationRoute
from mote_kernel.hooks.contract import HookGraphValue


@dataclass(frozen=True, slots=True)
class Request:
    value: str


@dataclass(frozen=True, slots=True)
class Response:
    value: str


@dataclass(frozen=True, slots=True)
class Receipt:
    value: str


@dataclass(frozen=True, slots=True)
class Handle:
    value: str


@dataclass(frozen=True, slots=True)
class Transform:
    value: str


TestOutcome: TypeAlias = PortOutcome[Response, Receipt, Handle]
TestResult: TypeAlias = FailoverResult[Request, Response, Receipt, Handle, Transform]
_TEST_PORT_ID = FailoverPortId("payment")


def _config(
    revision: int = 1,
    profile: FailoverProfile[Transform] | None = None,
    *,
    port_id: FailoverPortId = _TEST_PORT_ID,
) -> FailoverPlan[Transform]:
    return FailoverPlan(
        FailoverConfigRevision(revision),
        port_id,
        profile or FailoverProfile(FailoverProfileId("standard")),
    )


class _PrivateConstructor(Protocol):
    """Typed test-side access to one owner-internal constructor."""

    def __call__(self, *args: object) -> object: ...


class _PrivateNode(Protocol):
    def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Awaitable[Graph.Values[HookGraphValue] | Graph.Outcome[HookGraphValue]]: ...


class _BuilderNode(Protocol):
    node_id: str


class _BuilderState(Protocol):
    nodes: tuple[_BuilderNode, ...]


class _InspectableGraph(Protocol):
    _builder_state: _BuilderState

    @staticmethod
    def node_ids(graph: object) -> tuple[str, ...]:
        view = cast(_InspectableGraph, graph)
        return tuple(str(node.node_id) for node in view._builder_state.nodes)


@dataclass(frozen=True, slots=True)
class _AssemblyTestAccess:
    frame: _PrivateConstructor
    invoke: _PrivateConstructor
    invoke_step: _PrivateConstructor
    observe: _PrivateConstructor
    observe_step: _PrivateConstructor
    prepare: _PrivateConstructor


class _AssemblyPrivateView(Protocol):
    _FailoverFrame: _PrivateConstructor
    _InvokeOnce: _PrivateConstructor
    _InvokeStep: _PrivateConstructor
    _ObserveCall: _PrivateConstructor
    _ObserveStep: _PrivateConstructor
    _PrepareNextAttempt: _PrivateConstructor

    @staticmethod
    def read(module: object) -> _AssemblyTestAccess:
        view = cast(_AssemblyPrivateView, module)
        return _AssemblyTestAccess(
            view._FailoverFrame,
            view._InvokeOnce,
            view._InvokeStep,
            view._ObserveCall,
            view._ObserveStep,
            view._PrepareNextAttempt,
        )


def _private_assembly() -> _AssemblyTestAccess:
    return _AssemblyPrivateView.read(cast(object, assembly_module))


class ScriptedAttempt:
    def __init__(self, outcomes: list[TestOutcome]) -> None:
        self.outcomes = outcomes
        self.calls: list[Request] = []

    async def invoke_once(self, request: Request, /) -> TestOutcome:
        self.calls.append(request)
        return self.outcomes.pop(0)


class CancelledAttempt:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke_once(self, _request: Request, /) -> TestOutcome:
        self.calls += 1
        raise asyncio.CancelledError


class RecordingPreparation:
    def __init__(self) -> None:
        self.calls: list[tuple[Request, PreparationAction[Transform]]] = []

    async def prepare_next(
        self,
        request: Request,
        action: PreparationAction[Transform],
        /,
    ) -> PreparedRequest[Request]:
        self.calls.append((request, action))
        return PreparedRequest(Request(f"{request.value}:prepared"))


class InvalidAttempt:
    async def invoke_once(self, _request: Request, /) -> TestOutcome:
        return cast(TestOutcome, object())


class InvalidPreparation:
    async def prepare_next(
        self,
        _request: Request,
        _action: PreparationAction[Transform],
        /,
    ) -> PreparedRequest[Request]:
        return cast(PreparedRequest[Request], object())


def _decorate(
    config: FailoverPlan[Transform],
    attempt: ScriptedAttempt | InvalidAttempt | CancelledAttempt,
    preparation: RecordingPreparation | InvalidPreparation,
) -> Graph[HookGraphValue]:
    decorator: Failover[Request, Response, Receipt, Handle, Transform] = Failover(
        config,
        preparation,
    )
    return decorator(attempt)


def _graph(
    outcomes: list[TestOutcome],
    profile: FailoverProfile[Transform] | None = None,
) -> tuple[
    Graph[HookGraphValue],
    FailoverPlan[Transform],
    ScriptedAttempt,
    RecordingPreparation,
]:
    config = _config(profile=profile)
    attempt = ScriptedAttempt(outcomes)
    preparation = RecordingPreparation()
    graph = _decorate(config, attempt, preparation)
    return graph, config, attempt, preparation


async def _run(graph: Graph[HookGraphValue]) -> TestResult:
    result = await graph.run(Graph.values(request=FailoverCall(FailoverOperationId("operation-1"), Request("pay"))))
    assert isinstance(result, Graph.CompletedResult)
    terminal = result.outputs["result"]
    assert type(terminal) is FailoverResult
    return cast(TestResult, terminal)


def test_package_exposes_only_the_failover_decorator() -> None:
    assert failover_package.__all__ == ["Failover"]
    assert failover_package.Failover is Failover


def test_failover_graph_contains_only_observe_invoke_and_prepare() -> None:
    graph, _config, _attempt, _preparation = _graph([Completed(Response("paid"))])

    assert _InspectableGraph.node_ids(graph) == ("observe", "invoke", "prepare")


@pytest.mark.asyncio
async def test_completed_operation_calls_the_wrapped_port_once() -> None:
    graph, config, attempt, preparation = _graph([Completed(Response("paid"))])

    terminal = await _run(graph)

    assert terminal.outcome == Completed(Response("paid"))
    assert terminal.context.attempt_ordinal == 0
    assert terminal.context.plan_revision == config.plan_revision
    assert attempt.calls == [Request("pay")]
    assert preparation.calls == []


@pytest.mark.asyncio
async def test_decorated_port_composes_as_one_nested_graph_node() -> None:
    child, _config, attempt, _preparation = _graph([Completed(Response("paid"))])
    parent = Graph[HookGraphValue]("test.failover.parent")
    request_type = cast(type[HookGraphValue], FailoverCall)
    request = parent.graph_input("request", request_type)
    parent.add_node("payment", child, inputs={"request": request})
    parent.set_outputs({"result": parent.node_output("payment", "result")})

    result = await parent.run(Graph.values(request=FailoverCall(FailoverOperationId("operation-1"), Request("pay"))))

    assert isinstance(result, Graph.CompletedResult)
    terminal = result.outputs["result"]
    assert type(terminal) is FailoverResult
    assert cast(TestResult, terminal).outcome == Completed(Response("paid"))
    assert attempt.calls == [Request("pay")]


@pytest.mark.asyncio
async def test_direct_config_is_reused_by_each_graph_operation() -> None:
    config = _config(revision=7)
    attempt = ScriptedAttempt([Completed(Response("first")), Completed(Response("second"))])
    graph = _decorate(config, attempt, RecordingPreparation())

    first = await _run(graph)
    second = await graph.run(
        Graph.values(request=FailoverCall(FailoverOperationId("operation-2"), Request("pay-again")))
    )

    assert isinstance(second, Graph.CompletedResult)
    second_terminal = second.outputs["result"]
    assert type(second_terminal) is FailoverResult
    assert first.context.plan_revision == 7
    assert cast(TestResult, second_terminal).context.plan_revision == 7
    assert attempt.calls == [Request("pay"), Request("pay-again")]


@pytest.mark.asyncio
async def test_retry_returns_to_the_same_wrapped_port_through_prepare() -> None:
    rejected = Rejected(
        FailureEvidence(
            FailureClass.RATE_LIMITED,
            429,
            ErrorHint("rate_limited"),
        )
    )
    graph, _config, attempt, preparation = _graph([rejected, Completed(Response("paid"))])

    terminal = await _run(graph)

    assert attempt.calls == [Request("pay"), Request("pay:prepared")]
    assert len(preparation.calls) == 1
    assert isinstance(preparation.calls[0][1], Wait)
    assert terminal.context.attempt_ordinal == 1
    assert terminal.context.uses_for(FailureStrategy.WAIT) == 1


@pytest.mark.asyncio
async def test_unknown_provider_context_is_returned_without_kernel_follow_up() -> None:
    handle = Handle("provider-operation")
    unknown = Unknown(
        handle,
        FailureEvidence(FailureClass.NO_RESPONSE, None, ErrorHint("no_response")),
    )
    graph, _config, attempt, preparation = _graph([unknown])

    terminal = await _run(graph)

    assert terminal.outcome is unknown
    assert terminal.decision.route is ObservationRoute.RETURN_TO_MODEL
    assert terminal.context.last_failure is FailureClass.NO_RESPONSE
    assert attempt.calls == [Request("pay")]
    assert preparation.calls == []


@pytest.mark.asyncio
async def test_in_progress_content_is_returned_without_kernel_polling() -> None:
    receipt = Receipt("accepted-operation")
    in_progress = InProgress(receipt)
    graph, _config, attempt, preparation = _graph([in_progress])

    terminal = await _run(graph)

    assert terminal.outcome is in_progress
    assert terminal.decision.route is ObservationRoute.RETURN_TO_MODEL
    assert attempt.calls == [Request("pay")]
    assert preparation.calls == []


@pytest.mark.asyncio
async def test_unknown_without_a_handle_returns_control_to_the_model() -> None:
    unknown = Unknown[Handle](
        None,
        FailureEvidence(FailureClass.NO_RESPONSE, None, ErrorHint("no_response")),
    )
    graph, _config, attempt, preparation = _graph([unknown])

    terminal = await _run(graph)

    assert terminal.outcome is unknown
    assert terminal.decision.route is ObservationRoute.RETURN_TO_MODEL
    assert terminal.context.last_failure is FailureClass.NO_RESPONSE
    assert terminal.context.last_signal == unknown.evidence.signal
    assert attempt.calls == [Request("pay")]
    assert preparation.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "hint", "strategy", "profile", "request_version", "credential_cursor", "endpoint_cursor"),
    (
        (
            400,
            "bad_payload",
            FailureStrategy.TRANSFORM_REQUEST,
            FailoverProfile[Transform](
                FailoverProfileId("standard"),
                request_transform=TransformRequest(Transform("drop-field")),
            ),
            2,
            0,
            0,
        ),
        (
            401,
            "token_expired",
            FailureStrategy.REFRESH_CREDENTIAL,
            FailoverProfile[Transform](FailoverProfileId("standard")),
            1,
            0,
            0,
        ),
        (
            401,
            "invalid_api_key",
            FailureStrategy.ROTATE_CREDENTIAL,
            FailoverProfile[Transform](FailoverProfileId("standard")),
            1,
            1,
            0,
        ),
        (
            503,
            "service_unavailable",
            FailureStrategy.SWITCH_ENDPOINT,
            FailoverProfile[Transform](FailoverProfileId("standard")),
            1,
            0,
            1,
        ),
    ),
)
async def test_each_preparation_strategy_advances_only_its_owned_cursor(
    status_code: int,
    hint: str,
    strategy: FailureStrategy,
    profile: FailoverProfile[Transform],
    request_version: int,
    credential_cursor: int,
    endpoint_cursor: int,
) -> None:
    rejected = Rejected(FailureEvidence(FailureClass.UNKNOWN_OUTCOME, status_code, ErrorHint(hint)))
    graph, _config, attempt, preparation = _graph(
        [rejected, Completed(Response("paid"))],
        profile=profile,
    )

    terminal = await _run(graph)

    assert len(preparation.calls) == 1
    action = preparation.calls[0][1]
    if strategy is FailureStrategy.TRANSFORM_REQUEST:
        assert isinstance(action, TransformRequest)
    elif strategy is FailureStrategy.REFRESH_CREDENTIAL:
        assert isinstance(action, RefreshCredential)
    elif strategy is FailureStrategy.ROTATE_CREDENTIAL:
        assert isinstance(action, RotateCredential)
    else:
        assert isinstance(action, SwitchEndpoint)
    assert terminal.context.request_version == request_version
    assert terminal.context.credential_cursor == credential_cursor
    assert terminal.context.endpoint_cursor == endpoint_cursor
    assert terminal.context.attempt_ordinal == 1
    assert terminal.context.uses_for(strategy) == 1
    assert attempt.calls == [Request("pay"), Request("pay:prepared")]


@pytest.mark.asyncio
async def test_terminal_rejection_is_returned_without_preparation() -> None:
    rejected = Rejected(FailureEvidence(FailureClass.POLICY_DENIED, 403, ErrorHint("forbidden")))
    graph, _config, _attempt, preparation = _graph([rejected])

    terminal = await _run(graph)

    assert terminal.outcome is rejected
    assert terminal.decision.route is ObservationRoute.RETURN_TO_MODEL
    assert terminal.context.last_failure is FailureClass.POLICY_DENIED
    assert preparation.calls == []


@pytest.mark.asyncio
async def test_abort_policy_fails_without_running_an_internal_hook() -> None:
    rejected = Rejected(FailureEvidence(FailureClass.UNKNOWN_OUTCOME, 409, ErrorHint("conflict")))
    graph, _config, attempt, preparation = _graph([rejected])

    result = await graph.run(Graph.values(request=FailoverCall(FailoverOperationId("operation-1"), Request("pay"))))

    assert isinstance(result, Graph.FailedResult)
    assert tuple(failure.failure for failure in result.failures) == ("failover policy aborted the operation",)
    assert attempt.calls == [Request("pay")]
    assert preparation.calls == []


@pytest.mark.asyncio
async def test_caller_cancellation_never_starts_another_attempt() -> None:
    attempt = CancelledAttempt()
    graph = _decorate(_config(), attempt, RecordingPreparation())

    with pytest.raises(asyncio.CancelledError):
        await graph.run(Graph.values(request=FailoverCall(FailoverOperationId("operation-1"), Request("pay"))))

    assert attempt.calls == 1


@pytest.mark.asyncio
async def test_external_capabilities_must_return_their_declared_nominal_results() -> None:
    with pytest.raises(FailoverContractError, match="single-attempt capability returned"):
        await _run(
            _decorate(
                _config(),
                InvalidAttempt(),
                RecordingPreparation(),
            )
        )

    rejected = Rejected(FailureEvidence(FailureClass.RATE_LIMITED, 429, ErrorHint("rate_limited")))
    with pytest.raises(FailoverContractError, match="must return a PreparedRequest"):
        await _run(
            _decorate(
                _config(),
                ScriptedAttempt([rejected]),
                InvalidPreparation(),
            )
        )


def test_failover_result_and_call_reject_malformed_values() -> None:
    context = RetryContext(FailoverOperationId("operation-1"), FailoverConfigRevision(1))
    completed = Completed(Response("paid"))
    terminal = FailoverResult[Request, Response, Receipt, Handle, Transform](
        Request("pay"),
        context,
        completed,
        FailoverDecision[Transform](ObservationRoute.COMPLETED),
    )

    with pytest.raises(FailoverContractError, match="operation_id"):
        FailoverCall(FailoverOperationId(" bad"), Request("pay"))
    with pytest.raises(FailoverContractError, match="abort decision"):
        replace(
            terminal,
            decision=FailoverDecision(
                ObservationRoute.ABORT,
                FailureStrategy.ABORT,
            ),
        )
    rejected = Rejected(FailureEvidence(FailureClass.RATE_LIMITED, 429, ErrorHint("rate_limited")))
    with pytest.raises(FailoverContractError, match="completed Port outcome"):
        replace(terminal, outcome=rejected)


async def _assert_step_rejected(node: object, frame: object, expected: str) -> None:
    operation = cast(_PrivateNode, node)
    with pytest.raises(FailoverContractError, match=expected):
        await operation(Graph.values(frame=cast(HookGraphValue, frame)))


@pytest.mark.asyncio
async def test_internal_frames_and_nodes_reject_impossible_recovery_values() -> None:
    private = _private_assembly()
    profile = FailoverProfile[Transform](FailoverProfileId("standard"))
    plan = FailoverPlan(FailoverConfigRevision(1), _TEST_PORT_ID, profile)
    context = RetryContext(FailoverOperationId("operation-1"), FailoverConfigRevision(1))
    request = Request("pay")
    completed = Completed(Response("paid"))
    invoke_step = private.invoke_step(request, context)
    invoke_frame = private.frame(invoke_step, plan)
    observe_frame = private.frame(private.observe_step(request, context, completed), plan)

    with pytest.raises(FailoverContractError, match="unsupported step"):
        private.frame(object(), plan)
    with pytest.raises(FailoverContractError, match="FailoverPlan"):
        private.frame(invoke_step, object())
    mismatched = replace(context, plan_revision=FailoverConfigRevision(2))
    with pytest.raises(FailoverContractError, match="revisions must match"):
        private.frame(private.invoke_step(request, mismatched), plan)

    await _assert_step_rejected(private.invoke(ScriptedAttempt([])), observe_frame, "invoke step")
    await _assert_step_rejected(private.prepare(RecordingPreparation()), invoke_frame, "observe step")


def test_decorator_rejects_malformed_config_or_capabilities() -> None:
    config = _config()
    preparation = RecordingPreparation()
    arguments = (config, preparation)

    with pytest.raises(FailoverContractError, match="FailoverPlan config"):
        Failover[Request, Response, Receipt, Handle, Transform](
            cast(Never, object()),
            preparation,
        )
    with pytest.raises(FailoverContractError, match="preparation capability"):
        Failover[Request, Response, Receipt, Handle, Transform](
            config,
            cast(Never, object()),
        )
    decorator: Failover[Request, Response, Receipt, Handle, Transform] = Failover(*arguments)
    with pytest.raises(FailoverContractError, match="single-attempt Port"):
        decorator(cast(Never, object()))
