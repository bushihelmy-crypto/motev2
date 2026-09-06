"""Narrow, already-decorated capabilities injected into the Act graph.

Composition owns the injection order: construct the concrete Port, apply the
Port-level ``mote_kernel.failover.Failover`` decorator when the capability is
enabled, then pass that same typed capability to Act.  These protocols keep
the post-decoration request/result surface; Act never starts a retry loop or
wraps a whole graph.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mote_kernel.act.contract import (
    ActContractError,
    ActRequest,
    AuthorizationDecision,
    AuthorizationInput,
    AuthorizationInterruptView,
    AuthorizationRequestRef,
    AuthorizedInvocation,
    ExecutePortResult,
    ResolvedInvocation,
    ResolvePortResult,
    SettlementProjection,
    ToolExchangeWriteRequest,
    ToolExchangeWriteResult,
    ToolExecutionResult,
)
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue
from mote_kernel.state.graph_state.identity import is_canonical_identity


@runtime_checkable
class ResolvePort(Protocol):
    """Resolve one selector into a stable invocation or a typed stop."""

    async def resolve(self, request: ActRequest, /) -> ResolvePortResult: ...


@runtime_checkable
class AuthorizePort(Protocol):
    """Own authorization request/correlation and the Graph resume codec."""

    async def request_authorization(
        self,
        invocation: ResolvedInvocation,
        /,
    ) -> AuthorizationRequestRef: ...

    def encode_interrupt(self, request_ref: AuthorizationRequestRef, /) -> bytes: ...

    def build_resume_input(
        self,
        interrupt: AuthorizationInterruptView,
        decision: AuthorizationDecision,
        /,
    ) -> AuthorizationInput: ...

    def encode_graph_input(self, values: Graph.Values[HookGraphValue], /) -> bytes: ...

    def decode_graph_input(self, payload: bytes, /) -> Graph.Values[HookGraphValue]: ...

    @property
    def codec_id(self) -> str: ...

    @property
    def codec_version(self) -> int: ...


@runtime_checkable
class ExecutePort(Protocol):
    """Invoke one already-authorized tool operation."""

    async def execute(self, invocation: AuthorizedInvocation, /) -> ExecutePortResult: ...


@runtime_checkable
class SettlementPort(Protocol):
    """Project one execution result for protocol delivery."""

    async def project(self, result: ToolExecutionResult, /) -> SettlementProjection: ...


@runtime_checkable
class ToolExchangeWriter(Protocol):
    """Deliver one normal settlement projection and return its receipt."""

    async def write(self, request: ToolExchangeWriteRequest, /) -> ToolExchangeWriteResult: ...


def require_act_port_contracts(
    resolve_port: ResolvePort | None,
    authorize_port: AuthorizePort | None,
    execute_port: ExecutePort | None,
    settlement_port: SettlementPort | None,
    exchange_writer: ToolExchangeWriter | None,
    /,
) -> tuple[str, int]:
    """Validate all required Act capabilities before Graph assembly."""

    if not isinstance(resolve_port, ResolvePort) or not callable(resolve_port.resolve):
        raise ActContractError("ActNode requires a ResolvePort")
    if authorize_port is None:
        raise ActContractError("ActNode requires an AuthorizePort")
    try:
        request_authorization = authorize_port.request_authorization
        encode_interrupt = authorize_port.encode_interrupt
        build_resume_input = authorize_port.build_resume_input
        encode_graph_input = authorize_port.encode_graph_input
        decode_graph_input = authorize_port.decode_graph_input
        codec_id = authorize_port.codec_id
        codec_version = authorize_port.codec_version
    except AttributeError as error:
        raise ActContractError("ActNode requires an AuthorizePort") from error
    if not all(
        callable(operation)
        for operation in (
            request_authorization,
            encode_interrupt,
            build_resume_input,
            encode_graph_input,
            decode_graph_input,
        )
    ):
        raise ActContractError("ActNode requires an AuthorizePort")
    if not is_canonical_identity(codec_id):
        raise ActContractError("AuthorizePort.codec_id must be a canonical string")
    if type(codec_version) is not int or codec_version < 1:
        raise ActContractError("AuthorizePort.codec_version must be a positive integer")
    if not isinstance(execute_port, ExecutePort) or not callable(execute_port.execute):
        raise ActContractError("ActNode requires an ExecutePort")
    if not isinstance(settlement_port, SettlementPort) or not callable(settlement_port.project):
        raise ActContractError("ActNode requires a SettlementPort")
    if not isinstance(exchange_writer, ToolExchangeWriter) or not callable(exchange_writer.write):
        raise ActContractError("ActNode requires a ToolExchangeWriter")
    return codec_id, codec_version


__all__ = [
    "AuthorizePort",
    "ExecutePort",
    "ResolvePort",
    "SettlementPort",
    "ToolExchangeWriter",
]
