"""Act-local assembly seam for typed Port failover decoration.

The failover graph itself lives in :mod:`mote_kernel.failover`.  Act only
owns the list of capabilities that must be handed to that graph's owner.  A
composition root supplies a *typed-preserving* decorator here; this module
applies it to every Act Port and checks that the complete capability surface
survived decoration.  In particular, Authorize's synchronous correlation and
resume-codec operations are part of the same Port and may not be dropped by a
wrapper.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import TypeVar

from mote_kernel.act.contract import ActContractError
from mote_kernel.act.port import (
    AuthorizeCodecBinding,
    AuthorizeCodecCapture,
    AuthorizePort,
    ExecutePort,
    ResolvePort,
    SettlementPort,
    ToolExchangeWriter,
    require_act_port_contracts,
)
from mote_kernel.failover.contract import PortDecorator as FailoverPortDecorator
from mote_kernel.failover.contract import (
    PortDecoratorContractError,
    TypedPortDecorator,
    apply_port_decorator,
    require_port_decorator,
)

PortT = TypeVar("PortT")


def _validate_decorator(
    value: TypedPortDecorator[PortT] | FailoverPortDecorator | None,
    field: str,
    /,
) -> None:
    try:
        require_port_decorator(value, field)
    except PortDecoratorContractError as error:
        raise ActContractError(str(error)) from error


def _apply(
    port: PortT,
    decorator: TypedPortDecorator[PortT] | FailoverPortDecorator | None,
    expected: type[PortT],
    field: str,
    /,
    *,
    validate: bool = True,
) -> PortT:
    try:
        return apply_port_decorator(port, decorator, expected, field, validate=validate)
    except PortDecoratorContractError as error:
        raise ActContractError(str(error)) from error


@dataclass(frozen=True, slots=True)
class ActPortSet:
    """The complete typed capability set consumed by ``ActNode``.

    Authorize exposes synchronous codec metadata.  Capture its identity once
    and carry it through partial decoration so unchanged capabilities do not
    trigger a second provider property read.
    """

    resolve_port: ResolvePort
    authorize_port: AuthorizePort
    execute_port: ExecutePort
    settlement_port: SettlementPort
    exchange_writer: ToolExchangeWriter
    _codec_binding: InitVar[AuthorizeCodecBinding | None] = field(default=None, kw_only=True, repr=False)
    _codec_capture: InitVar[AuthorizeCodecCapture | None] = field(default=None, kw_only=True, repr=False)
    _captured_codec_binding: AuthorizeCodecBinding = field(init=False, repr=False, compare=False)
    _captured_codec_capture: AuthorizeCodecCapture = field(init=False, repr=False, compare=False)

    def __post_init__(
        self,
        codec_binding: AuthorizeCodecBinding | None,
        codec_capture: AuthorizeCodecCapture | None,
    ) -> None:
        captured = require_act_port_contracts(
            self.resolve_port,
            self.authorize_port,
            self.execute_port,
            self.settlement_port,
            self.exchange_writer,
            authorize_codec_binding=codec_binding,
            authorize_codec_capture=codec_capture,
        )
        object.__setattr__(self, "_captured_codec_binding", captured)
        if codec_capture is None:
            # The helper has already captured the real provider metadata once;
            # retain that admitted value and the exact Port identity for a
            # future partial-decoration rebuild without rereading properties.
            codec_capture = AuthorizeCodecCapture(self.authorize_port, captured)
        object.__setattr__(self, "_captured_codec_capture", codec_capture)

    @property
    def authorize_codec_binding(self) -> AuthorizeCodecBinding:
        """Return the immutable codec identity admitted at construction."""

        return self._captured_codec_binding

    @property
    def authorize_codec_capture(self) -> AuthorizeCodecCapture:
        """Return the admitted codec provenance for partial decoration."""

        return self._captured_codec_capture


@dataclass(frozen=True, slots=True)
class ActFailoverDecorators:
    """Per-Port decoration declarations for Act.

    ``None`` is an explicit disabled binding for that Port.  ``uniform`` is a
    convenience for a composition that uses one decorator implementation for
    all five capabilities; a bundle is still retained so each Port remains a
    separately typed seam.
    """

    resolve: TypedPortDecorator[ResolvePort] | FailoverPortDecorator | None = None
    authorize: TypedPortDecorator[AuthorizePort] | FailoverPortDecorator | None = None
    execute: TypedPortDecorator[ExecutePort] | FailoverPortDecorator | None = None
    settlement: TypedPortDecorator[SettlementPort] | FailoverPortDecorator | None = None
    exchange_writer: TypedPortDecorator[ToolExchangeWriter] | FailoverPortDecorator | None = None

    def __post_init__(self) -> None:
        _validate_decorator(self.resolve, "ActFailoverDecorators.resolve")
        _validate_decorator(self.authorize, "ActFailoverDecorators.authorize")
        _validate_decorator(self.execute, "ActFailoverDecorators.execute")
        _validate_decorator(self.settlement, "ActFailoverDecorators.settlement")
        _validate_decorator(self.exchange_writer, "ActFailoverDecorators.exchange_writer")

    @classmethod
    def disabled(cls) -> ActFailoverDecorators:
        """Return explicit no-failover bindings for all Act Ports."""

        return cls()

    @classmethod
    def uniform(cls, decorator: FailoverPortDecorator, /) -> ActFailoverDecorators:
        """Apply one typed-preserving decorator to every Act Port."""

        _validate_decorator(decorator, "ActFailoverDecorators.uniform")
        return cls(decorator, decorator, decorator, decorator, decorator)

    def resolve_port(self, port: ResolvePort, /) -> ResolvePort:
        return _apply(port, self.resolve, ResolvePort, "ResolvePort")

    def authorize_port(self, port: AuthorizePort, /) -> AuthorizePort:
        # AuthorizePort exposes codec metadata as properties.  Structural
        # ``isinstance`` checks would read those properties once, and the
        # owner contract would read them again; defer the complete check to
        # ``require_act_port_contracts`` so each is captured exactly once.
        return _apply(port, self.authorize, AuthorizePort, "AuthorizePort", validate=False)

    def execute_port(self, port: ExecutePort, /) -> ExecutePort:
        return _apply(port, self.execute, ExecutePort, "ExecutePort")

    def settlement_port(self, port: SettlementPort, /) -> SettlementPort:
        return _apply(port, self.settlement, SettlementPort, "SettlementPort")

    def exchange_writer_port(self, port: ToolExchangeWriter, /) -> ToolExchangeWriter:
        return _apply(port, self.exchange_writer, ToolExchangeWriter, "ToolExchangeWriter")

    def decorate(self, ports: ActPortSet, /) -> ActPortSet:
        if type(ports) is not ActPortSet:
            raise ActContractError("Act failover decoration requires an ActPortSet")
        resolve_port = self.resolve_port(ports.resolve_port)
        authorize_port = self.authorize_port(ports.authorize_port)
        execute_port = self.execute_port(ports.execute_port)
        settlement_port = self.settlement_port(ports.settlement_port)
        exchange_writer = self.exchange_writer_port(ports.exchange_writer)
        # A type-preserving decorator is allowed to return the original
        # object.  Keep an already-validated PortSet in that case; rebuilding
        # it would reread Authorize's synchronous codec properties and could
        # violate a provider's single-read assembly contract.
        if (
            resolve_port is ports.resolve_port
            and authorize_port is ports.authorize_port
            and execute_port is ports.execute_port
            and settlement_port is ports.settlement_port
            and exchange_writer is ports.exchange_writer
        ):
            return ports
        cached_binding = ports.authorize_codec_binding if authorize_port is ports.authorize_port else None
        cached_capture = ports.authorize_codec_capture if authorize_port is ports.authorize_port else None
        return ActPortSet(
            resolve_port,
            authorize_port,
            execute_port,
            settlement_port,
            exchange_writer,
            _codec_binding=cached_binding,
            _codec_capture=cached_capture,
        )


def normalize_act_failover_decorators(
    value: ActFailoverDecorators | FailoverPortDecorator | None,
    /,
) -> ActFailoverDecorators:
    """Normalize a per-Port bundle or one uniform composition decorator."""

    if value is None:
        return ActFailoverDecorators.disabled()
    if type(value) is ActFailoverDecorators:
        return value
    if callable(value):
        return ActFailoverDecorators.uniform(value)
    raise ActContractError("Act failover decoration requires a callable decorator or ActFailoverDecorators")


def decorate_act_ports(
    resolve_port: ResolvePort,
    authorize_port: AuthorizePort,
    execute_port: ExecutePort,
    settlement_port: SettlementPort,
    exchange_writer: ToolExchangeWriter,
    /,
    *,
    failover: ActFailoverDecorators | FailoverPortDecorator | None = None,
) -> ActPortSet:
    """Apply Act's failover declarations before graph-node assembly."""
    decorators = normalize_act_failover_decorators(failover)
    # Decorate raw capabilities first, then validate the resulting complete
    # set once.  Constructing a raw set before decoration would perform two
    # structural checks (and two Authorize codec metadata reads).
    return ActPortSet(
        decorators.resolve_port(resolve_port),
        decorators.authorize_port(authorize_port),
        decorators.execute_port(execute_port),
        decorators.settlement_port(settlement_port),
        decorators.exchange_writer_port(exchange_writer),
    )


__all__ = [
    "ActFailoverDecorators",
    "ActPortSet",
    "FailoverPortDecorator",
    "decorate_act_ports",
    "normalize_act_failover_decorators",
]
