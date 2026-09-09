"""Boundary coverage for the typed Port failover decoration seams.

The public failover object is a Graph decorator.  Domain packages therefore
keep a small, private composition seam that applies a type-preserving callable
to each concrete Port.  These tests intentionally exercise that seam without
making the failover package depend on domain DTOs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Never, cast

import pytest

import mote_kernel.failover as failover_package
from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.authorize import AuthorizeNode
from mote_kernel.act.contract import ActHookCommand, HookStateProjection
from mote_kernel.act.execute import ExecuteNode
from mote_kernel.act.failover import (
    ActFailoverDecorators,
    ActPortSet,
    FailoverPortDecorator,
    decorate_act_ports,
    normalize_act_failover_decorators,
)
from mote_kernel.act.identity import OpaqueGraphFailureReason
from mote_kernel.act.resolve import ResolveNode
from mote_kernel.act.settle import SettleNode
from mote_kernel.failover import Failover
from mote_kernel.failover.contract import FailoverContractError, apply_port_decorator, require_port_decorator
from mote_kernel.hooks.contract import HookGraphValue, HookInvocationRequest, HookStageResult
from mote_kernel.hooks.failover import (
    HookFailoverDecorator,
    HookFailoverDecorators,
    decorate_hook_invocation,
    normalize_hook_failover_decorators,
)
from mote_kernel.invocation import Invocation
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.contract import (
    HookStateProjection as ObserveHookStateProjection,
)
from mote_kernel.observe.contract import (
    ObservationPayload,
    ObserveHookCommand,
)
from mote_kernel.observe.failover import (
    ObserveFailoverDecorators,
    ObservePortSet,
    decorate_observe_ports,
    normalize_observe_failover_decorators,
)
from mote_kernel.observe.node import GetObservationNode, WriteObservationNode
from mote_kernel.think.command import CommandNode
from mote_kernel.think.compact import CompactNode
from mote_kernel.think.context import ContextNode
from mote_kernel.think.failover import (
    ThinkFailoverDecorators,
    ThinkPortSet,
    decorate_think_ports,
    normalize_think_failover_decorators,
)
from mote_kernel.think.inference import InferenceNode
from mote_kernel.think.prompt import PromptNode
from mote_kernel.think.router import RouterNode

_USE_INPUT = object()


class _RecordingDecorator:
    """A type-erased test decorator; domain seams restore the Port type."""

    def __init__(self, result: object = _USE_INPUT) -> None:
        self.calls: list[object] = []
        self.result = result

    def __call__(self, port: object, /) -> object:
        self.calls.append(port)
        return port if self.result is _USE_INPUT else self.result


class _RaisingDecorator:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __call__(self, _port: object, /) -> object:
        raise self.error


class _UniversalPort:
    """One structural object exposing every domain Port member."""

    async def resolve(self, _request: object, /) -> object:
        return object()

    async def request_authorization(self, _invocation: object, /) -> object:
        return object()

    def encode_interrupt(self, _request_ref: object, /) -> bytes:
        return b"interrupt"

    def build_resume_input(self, _interrupt: object, _decision: object, /) -> object:
        return object()

    def encode_graph_input(self, _values: object, /) -> bytes:
        return b"graph-input"

    def decode_graph_input(self, _payload: bytes, /) -> object:
        return object()

    @property
    def codec_id(self) -> str:
        return "test.port"

    @property
    def codec_version(self) -> int:
        return 1

    async def execute(self, _invocation: object, /) -> object:
        return object()

    async def project(self, _result: object, /) -> object:
        return object()

    async def write(self, _request: object, /) -> object:
        return object()

    async def load_system_prompt(self, _payload: object, /) -> object:
        return object()

    async def load_placeholder(self, _payload: object, /) -> object:
        return object()

    async def load_user_prompt(self, _payload: object, /) -> object:
        return object()

    async def load_context(self, _request: object, /) -> object:
        return object()

    async def compact(self, _request: object, /) -> object:
        return object()

    async def route_model(self, _request: object, /) -> object:
        return object()

    async def infer(self, _request: object, /) -> object:
        return object()

    async def build_command(self, _request: object, /) -> object:
        return object()

    async def read_after(self, _cursor: object, /) -> object:
        return object()

    async def register_wait(self, _wait: object, /) -> object:
        return object()

    async def snapshot(self, _boundary: object, /) -> object:
        return object()

    async def apply(self, _batch: object, /) -> object:
        return object()

    async def append(self, _batch: object, /) -> object:
        return object()

    async def acknowledge(self, _delivery_ids: object, _receipt: object, /) -> object:
        return object()

    async def invoke(self, _request: object, /) -> object:
        return object()


class _SingleReadPort(_UniversalPort):
    def __init__(self) -> None:
        self.codec_id_reads = 0
        self.codec_version_reads = 0

    @property
    def codec_id(self) -> str:
        self.codec_id_reads += 1
        if self.codec_id_reads > 1:
            raise AssertionError("codec_id was read more than once")
        return "test.single-read"

    @property
    def codec_version(self) -> int:
        self.codec_version_reads += 1
        if self.codec_version_reads > 1:
            raise AssertionError("codec_version was read more than once")
        return 1


def _decorator(value: _RecordingDecorator) -> FailoverPortDecorator:
    return cast(FailoverPortDecorator, value)


def test_failover_package_keeps_only_the_public_graph_decorator() -> None:
    assert failover_package.__all__ == ["Failover"]
    assert failover_package.Failover is Failover
    assert not hasattr(failover_package, "PortDecorator")
    assert not hasattr(failover_package, "apply_port_decorator")


def test_generic_port_seam_is_fail_closed_and_preserves_decorator_errors() -> None:
    port = _UniversalPort()
    recorder = _RecordingDecorator()

    require_port_decorator(None, "test")
    require_port_decorator(_decorator(recorder), "test")
    with pytest.raises(FailoverContractError, match="callable"):
        require_port_decorator(cast(Never, object()), "test")

    assert apply_port_decorator(port, None, _UniversalPort, "test") is port
    assert apply_port_decorator(port, _decorator(recorder), _UniversalPort, "test") is port
    with pytest.raises(FailoverContractError, match="changed"):
        apply_port_decorator(port, _decorator(_RecordingDecorator(object())), _UniversalPort, "test")

    error = RuntimeError("decorator failed")
    with pytest.raises(RuntimeError) as raised:
        apply_port_decorator(port, cast(FailoverPortDecorator, _RaisingDecorator(error)), _UniversalPort, "test")
    assert raised.value is error


def test_domain_seams_preserve_failover_contract_errors_raised_by_the_decorator() -> None:
    """Only malformed decoration results are translated by domain owners."""

    port = _UniversalPort()
    error = FailoverContractError("provider-specific decoration failure")
    decorator = cast(FailoverPortDecorator, _RaisingDecorator(error))

    with pytest.raises(FailoverContractError) as raised:
        decorate_act_ports(
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            failover=decorator,
        )
    assert raised.value is error

    with pytest.raises(FailoverContractError) as raised:
        decorate_observe_ports(
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            failover=decorator,
        )
    assert raised.value is error

    with pytest.raises(FailoverContractError) as raised:
        decorate_think_ports(
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            failover=decorator,
        )
    assert raised.value is error

    with pytest.raises(FailoverContractError) as raised:
        decorate_hook_invocation(cast(Never, port), failover=decorator)
    assert raised.value is error


def test_act_bundle_decorates_each_port_once_and_keeps_authorize_codec_surface() -> None:
    port = _UniversalPort()
    recorders = tuple(_RecordingDecorator() for _ in range(5))
    decorators = ActFailoverDecorators(
        _decorator(recorders[0]),
        _decorator(recorders[1]),
        _decorator(recorders[2]),
        _decorator(recorders[3]),
        _decorator(recorders[4]),
    )

    decorated = decorate_act_ports(
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        failover=decorators,
    )

    assert tuple(len(recorder.calls) for recorder in recorders) == (1, 1, 1, 1, 1)
    assert decorated.authorize_port.encode_graph_input is not None
    assert decorated.authorize_port.decode_graph_input is not None
    assert decorated.authorize_port.codec_id == "test.port"
    assert decorated.authorize_port.codec_version == 1


def test_act_uniform_and_invalid_decorators_are_normalized_at_the_domain_boundary() -> None:
    port = _UniversalPort()
    recorder = _RecordingDecorator()
    decorated = decorate_act_ports(
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        failover=_decorator(recorder),
    )
    assert decorated.resolve_port is port
    assert len(recorder.calls) == 5
    assert type(normalize_act_failover_decorators(None)) is ActFailoverDecorators
    with pytest.raises(ValueError, match="callable decorator"):
        normalize_act_failover_decorators(cast(Never, object()))
    with pytest.raises(ValueError, match="changed"):
        decorate_act_ports(
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            cast(Never, port),
            failover=ActFailoverDecorators(resolve=_decorator(_RecordingDecorator(object()))),
        )


def test_port_set_helpers_do_not_reread_synchronous_codec_metadata() -> None:
    act_port = _SingleReadPort()
    act_set: ActPortSet = decorate_act_ports(
        cast(Never, act_port),
        cast(Never, act_port),
        cast(Never, act_port),
        cast(Never, act_port),
        cast(Never, act_port),
    )
    assert act_port.codec_id_reads == 1
    assert act_port.codec_version_reads == 1
    assert ActFailoverDecorators.uniform(_decorator(_RecordingDecorator())).decorate(act_set) is act_set
    assert act_port.codec_id_reads == 1
    assert act_port.codec_version_reads == 1

    observe_port = _SingleReadPort()
    observe_set: ObservePortSet = decorate_observe_ports(
        cast(Never, observe_port),
        cast(Never, observe_port),
        cast(Never, observe_port),
        cast(Never, observe_port),
        cast(Never, observe_port),
        cast(Never, observe_port),
    )
    assert observe_port.codec_id_reads == 1
    assert observe_port.codec_version_reads == 1
    assert ObserveFailoverDecorators.uniform(_decorator(_RecordingDecorator())).decorate(observe_set) is observe_set
    assert observe_port.codec_id_reads == 1
    assert observe_port.codec_version_reads == 1


def test_think_bundle_treats_prompt_as_one_three_method_capability() -> None:
    port = _UniversalPort()
    recorders = tuple(_RecordingDecorator() for _ in range(6))
    decorators: ThinkFailoverDecorators[
        object,
        HookGraphValue,
        object,
        object,
        object,
        object,
        object,
        object,
        object,
    ] = ThinkFailoverDecorators(
        _decorator(recorders[0]),
        _decorator(recorders[1]),
        _decorator(recorders[2]),
        _decorator(recorders[3]),
        _decorator(recorders[4]),
        _decorator(recorders[5]),
    )
    decorated: ThinkPortSet[
        object,
        HookGraphValue,
        object,
        object,
        object,
        object,
        object,
        object,
        object,
    ] = decorate_think_ports(
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        failover=decorators,
    )

    assert tuple(len(recorder.calls) for recorder in recorders) == (1, 1, 1, 1, 1, 1)
    assert decorated.prompt_port.load_system_prompt is not None
    assert decorated.prompt_port.load_placeholder is not None
    assert decorated.prompt_port.load_user_prompt is not None
    none_decorators: ThinkFailoverDecorators[
        object,
        HookGraphValue,
        object,
        object,
        object,
        object,
        object,
        object,
        object,
    ] = normalize_think_failover_decorators(None)
    assert type(none_decorators) is ThinkFailoverDecorators
    with pytest.raises(ValueError, match="callable decorator"):
        normalize_think_failover_decorators(cast(Never, object()))


def test_observe_bundle_decorates_all_six_ports_and_reads_resume_metadata_once() -> None:
    port = _SingleReadPort()
    recorders = tuple(_RecordingDecorator() for _ in range(6))
    decorators = ObserveFailoverDecorators(
        _decorator(recorders[0]),
        _decorator(recorders[1]),
        _decorator(recorders[2]),
        _decorator(recorders[3]),
        _decorator(recorders[4]),
        _decorator(recorders[5]),
    )
    decorated = decorate_observe_ports(
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        cast(Never, port),
        failover=decorators,
    )

    assert tuple(len(recorder.calls) for recorder in recorders) == (1, 1, 1, 1, 1, 1)
    assert decorated.resume_port.encode_graph_input is not None
    assert decorated.resume_port.decode_graph_input is not None
    assert port.codec_id_reads == 1
    assert port.codec_version_reads == 1
    assert type(normalize_observe_failover_decorators(None)) is ObserveFailoverDecorators
    with pytest.raises(ValueError, match="callable decorator"):
        normalize_observe_failover_decorators(cast(Never, object()))


def test_hook_uses_one_shared_invocation_declaration_for_both_priorities() -> None:
    invocation = _UniversalPort()
    recorder = _RecordingDecorator()
    hook_decorators: HookFailoverDecorators[object, object, object, object] = HookFailoverDecorators[
        object,
        object,
        object,
        object,
    ].uniform(cast(HookFailoverDecorator, recorder))
    decorated: Invocation[
        HookInvocationRequest[object, object],
        HookStageResult[object, object],
    ] = decorate_hook_invocation(
        cast(Invocation[HookInvocationRequest[object, object], HookStageResult[object, object]], invocation),
        failover=hook_decorators,
    )

    assert decorated is invocation
    assert recorder.calls == [invocation]
    disabled: HookFailoverDecorators[object, object, object, object] = normalize_hook_failover_decorators(None)
    assert disabled == HookFailoverDecorators[object, object, object, object].disabled()
    normalized: HookFailoverDecorators[object, object, object, object] = normalize_hook_failover_decorators(
        cast(HookFailoverDecorator, recorder)
    )
    assert normalized.invocation is recorder
    with pytest.raises(ValueError, match="callable decorator"):
        normalize_hook_failover_decorators(cast(Never, object()))
    with pytest.raises(ValueError, match="changed"):
        malformed: HookFailoverDecorators[object, object, object, object] = HookFailoverDecorators(
            cast(HookFailoverDecorator, _RecordingDecorator(object()))
        )
        decorate_hook_invocation(
            cast(Never, invocation),
            failover=malformed,
        )


@dataclass(frozen=True, slots=True)
class _ActState(HookStateProjection):
    marker: str = "state"


@dataclass(frozen=True, slots=True)
class _ActCommand(ActHookCommand):
    marker: str = "command"


@dataclass(frozen=True, slots=True)
class _ObserveState(ObserveHookStateProjection):
    marker: str = "state"


@dataclass(frozen=True, slots=True)
class _ObserveCommand(ObserveHookCommand):
    marker: str = "command"


@dataclass(frozen=True, slots=True)
class _Payload(ObservationPayload):
    marker: str = "payload"


def test_direct_stage_construction_decorates_initial_ports_once() -> None:
    port = _UniversalPort()
    recorder = _RecordingDecorator()
    decorators = _decorator(recorder)

    # Think has six stage owners; Prompt remains one object with three methods.
    PromptNode(cast(Never, port), decorators)
    ContextNode(cast(Never, port), decorators)
    CompactNode(cast(Never, port), decorators)
    RouterNode(cast(Never, port), decorators)
    InferenceNode(cast(Never, port), decorators)
    CommandNode(cast(Never, port), decorators)
    assert len(recorder.calls) == 6

    act_recorder = _RecordingDecorator()
    act_decorator = _decorator(act_recorder)
    admission: ActPayloadAdmission[_ActState, _ActCommand] = ActPayloadAdmission(_ActState, _ActCommand)
    resolve: ResolveNode[_ActState, _ActCommand] = ResolveNode[_ActState, _ActCommand](
        cast(Never, port), admission, act_decorator
    )
    authorize: AuthorizeNode[_ActState, _ActCommand] = AuthorizeNode[_ActState, _ActCommand](
        cast(Never, port), OpaqueGraphFailureReason("denied"), admission, act_decorator
    )
    execute: ExecuteNode[_ActState, _ActCommand] = ExecuteNode[_ActState, _ActCommand](
        cast(Never, port), admission, act_decorator
    )
    settle: SettleNode[_ActState, _ActCommand] = SettleNode[_ActState, _ActCommand](
        cast(Never, port), cast(Never, port), admission, act_decorator
    )
    assert resolve.resolve_port is port
    assert authorize.authorize_port is port
    assert execute.execute_port is port
    assert settle.settlement_port is port
    assert settle.exchange_writer is port
    assert len(act_recorder.calls) == 5

    observe_recorder = _RecordingDecorator()
    observe_decorator = _decorator(observe_recorder)
    observe_admission = ObservePayloadAdmission(
        _ObserveState,
        _ObserveCommand,
        _Payload,
        _Payload,
        _Payload,
        _Payload,
    )
    get_node: GetObservationNode[_ObserveState, _ObserveCommand] = GetObservationNode[
        _ObserveState,
        _ObserveCommand,
    ](cast(Never, port), cast(Never, port), observe_admission, observe_decorator)
    write_node: WriteObservationNode[_ObserveState, _ObserveCommand] = WriteObservationNode[
        _ObserveState,
        _ObserveCommand,
    ](cast(Never, port), cast(Never, port), observe_admission, observe_decorator)
    assert get_node.queue_port is port
    assert get_node.background_task_port is port
    assert write_node.config_port is port
    assert write_node.context_port is port
    assert len(observe_recorder.calls) == 4
