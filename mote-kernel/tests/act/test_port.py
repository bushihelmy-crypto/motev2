"""Runtime shape and assembly checks for the Act capability protocols."""

from typing import cast

import pytest

import mote_kernel.act as act_package
from mote_kernel.act.contract import (
    ActContractError,
    ActRequest,
    AuthorizationDecision,
    AuthorizationInput,
    AuthorizationInterruptView,
    AuthorizationRequestRef,
    AuthorizedInvocation,
    ExecutePortResult,
    HookGraphValue,
    ResolvedInvocation,
    ResolvePortResult,
    SettlementProjection,
    ToolExchangeWriteRequest,
    ToolExchangeWriteResult,
    ToolExecutionResult,
)
from mote_kernel.act.port import (
    AuthorizePort,
    ExecutePort,
    ResolvePort,
    SettlementPort,
    ToolExchangeWriter,
    require_act_port_contracts,
)
from mote_kernel.execution import Graph


class _PortBundle:
    async def resolve(self, request: ActRequest, /) -> ResolvePortResult:
        raise NotImplementedError

    async def request_authorization(self, invocation: ResolvedInvocation, /) -> AuthorizationRequestRef:
        raise NotImplementedError

    def encode_interrupt(self, request_ref: AuthorizationRequestRef, /) -> bytes:
        raise NotImplementedError

    def build_resume_input(
        self,
        interrupt: AuthorizationInterruptView,
        decision: AuthorizationDecision,
        /,
    ) -> AuthorizationInput:
        raise NotImplementedError

    def encode_graph_input(self, values: Graph.Values[HookGraphValue], /) -> bytes:
        raise NotImplementedError

    def decode_graph_input(self, payload: bytes, /) -> Graph.Values[HookGraphValue]:
        raise NotImplementedError

    @property
    def codec_id(self) -> str:
        return "test.codec"

    @property
    def codec_version(self) -> int:
        return 1

    async def execute(self, invocation: AuthorizedInvocation, /) -> ExecutePortResult:
        raise NotImplementedError

    async def project(self, result: ToolExecutionResult, /) -> SettlementProjection:
        raise NotImplementedError

    async def write(self, request: ToolExchangeWriteRequest, /) -> ToolExchangeWriteResult:
        raise NotImplementedError


class _Incomplete:
    pass


class _CodecBundle(_PortBundle):
    def __init__(self, codec_id: str, codec_version: int) -> None:
        self._codec_id = codec_id
        self._codec_version = codec_version

    @property
    def codec_id(self) -> str:
        return self._codec_id

    @property
    def codec_version(self) -> int:
        return self._codec_version


def test_runtime_checkable_protocols_admit_complete_bundle() -> None:
    bundle = _PortBundle()
    assert isinstance(bundle, ResolvePort)
    assert isinstance(bundle, AuthorizePort)
    assert isinstance(bundle, ExecutePort)
    assert isinstance(bundle, SettlementPort)
    assert isinstance(bundle, ToolExchangeWriter)


def test_runtime_checkable_protocols_reject_missing_members() -> None:
    incomplete = _Incomplete()
    assert not isinstance(incomplete, ResolvePort)
    assert not isinstance(incomplete, AuthorizePort)
    assert not isinstance(incomplete, ExecutePort)
    assert not isinstance(incomplete, SettlementPort)
    assert not isinstance(incomplete, ToolExchangeWriter)


@pytest.mark.parametrize(
    "missing",
    ["resolve", "authorize", "execute", "settlement", "writer"],
)
def test_act_assembly_rejects_each_missing_required_port(missing: str) -> None:
    bundle = _PortBundle()
    incomplete = _Incomplete()
    resolve = cast(ResolvePort, incomplete) if missing == "resolve" else bundle
    authorize = cast(AuthorizePort, incomplete) if missing == "authorize" else bundle
    execute = cast(ExecutePort, incomplete) if missing == "execute" else bundle
    settlement = cast(SettlementPort, incomplete) if missing == "settlement" else bundle
    writer = cast(ToolExchangeWriter, incomplete) if missing == "writer" else bundle

    with pytest.raises(ActContractError, match="requires"):
        require_act_port_contracts(resolve, authorize, execute, settlement, writer)


def test_act_assembly_rejects_an_explicitly_absent_authorize_port() -> None:
    bundle = _PortBundle()

    with pytest.raises(ActContractError, match="AuthorizePort"):
        require_act_port_contracts(bundle, None, bundle, bundle, bundle)


@pytest.mark.parametrize(
    "member",
    [
        "request_authorization",
        "encode_interrupt",
        "build_resume_input",
        "encode_graph_input",
        "decode_graph_input",
    ],
)
def test_act_assembly_rejects_each_non_callable_authorize_member(member: str) -> None:
    bundle = _PortBundle()
    setattr(bundle, member, None)

    with pytest.raises(ActContractError, match="AuthorizePort"):
        require_act_port_contracts(bundle, cast(AuthorizePort, bundle), bundle, bundle, bundle)


@pytest.mark.parametrize(
    ("port_name", "member"),
    [
        ("resolve", "resolve"),
        ("execute", "execute"),
        ("settlement", "project"),
        ("writer", "write"),
    ],
)
def test_act_assembly_rejects_each_non_callable_single_operation_port(
    port_name: str,
    member: str,
) -> None:
    bundle = _PortBundle()
    setattr(bundle, member, None)
    resolve = cast(ResolvePort, bundle) if port_name == "resolve" else _PortBundle()
    execute = cast(ExecutePort, bundle) if port_name == "execute" else _PortBundle()
    settlement = cast(SettlementPort, bundle) if port_name == "settlement" else _PortBundle()
    writer = cast(ToolExchangeWriter, bundle) if port_name == "writer" else _PortBundle()

    with pytest.raises(ActContractError, match="requires"):
        require_act_port_contracts(
            resolve,
            _PortBundle(),
            execute,
            settlement,
            writer,
        )


@pytest.mark.parametrize("codec_id", ["", " bad", "bad ", "bad\ncodec"])
def test_act_assembly_rejects_noncanonical_authorization_codec_id(codec_id: str) -> None:
    bundle = _CodecBundle(codec_id, 1)

    with pytest.raises(ActContractError, match="codec_id"):
        require_act_port_contracts(bundle, bundle, bundle, bundle, bundle)


@pytest.mark.parametrize("codec_version", [0, -1, True])
def test_act_assembly_rejects_invalid_authorization_codec_version(codec_version: int) -> None:
    bundle = _CodecBundle("test.codec", codec_version)

    with pytest.raises(ActContractError, match="codec_version"):
        require_act_port_contracts(bundle, bundle, bundle, bundle, bundle)


def test_act_port_contract_returns_the_frozen_resume_codec_binding() -> None:
    bundle = _CodecBundle("test.codec", 3)

    assert require_act_port_contracts(bundle, bundle, bundle, bundle, bundle) == ("test.codec", 3)


def test_act_port_contract_reads_each_resume_codec_coordinate_once() -> None:
    class SingleReadCodecBundle(_PortBundle):
        def __init__(self) -> None:
            self.codec_id_reads = 0
            self.codec_version_reads = 0

        @property
        def codec_id(self) -> str:
            self.codec_id_reads += 1
            if self.codec_id_reads > 1:
                raise AssertionError("codec_id was read more than once")
            return "single-read.codec"

        @property
        def codec_version(self) -> int:
            self.codec_version_reads += 1
            if self.codec_version_reads > 1:
                raise AssertionError("codec_version was read more than once")
            return 1

    bundle = SingleReadCodecBundle()

    assert require_act_port_contracts(bundle, bundle, bundle, bundle, bundle) == (
        "single-read.codec",
        1,
    )
    assert bundle.codec_id_reads == 1
    assert bundle.codec_version_reads == 1


def test_act_package_reserves_package_export_for_its_graph_node() -> None:
    assert act_package.__all__ == ["ActNode"]
    assert not hasattr(act_package, "ActRequest")
