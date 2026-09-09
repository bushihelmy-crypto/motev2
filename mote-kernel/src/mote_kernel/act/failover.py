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

from dataclasses import dataclass

from mote_kernel.act.contract import ActContractError
from mote_kernel.act.port import (
    AuthorizePort,
    ExecutePort,
    ResolvePort,
    SettlementPort,
    ToolExchangeWriter,
)
from mote_kernel.failover.contract import PortDecorator as FailoverPortDecorator
from mote_kernel.failover.contract import (
    TypedPortDecorator,
    apply_port_decorator_boundary,
    require_port_decorator_boundary,
)


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
        require_port_decorator_boundary(self.resolve, "ActFailoverDecorators.resolve", ActContractError)
        require_port_decorator_boundary(self.authorize, "ActFailoverDecorators.authorize", ActContractError)
        require_port_decorator_boundary(self.execute, "ActFailoverDecorators.execute", ActContractError)
        require_port_decorator_boundary(self.settlement, "ActFailoverDecorators.settlement", ActContractError)
        require_port_decorator_boundary(self.exchange_writer, "ActFailoverDecorators.exchange_writer", ActContractError)

    @classmethod
    def disabled(cls) -> ActFailoverDecorators:
        """Return explicit no-failover bindings for all Act Ports."""

        return cls()

    @classmethod
    def uniform(cls, decorator: FailoverPortDecorator, /) -> ActFailoverDecorators:
        """Apply one typed-preserving decorator to every Act Port."""

        require_port_decorator_boundary(decorator, "ActFailoverDecorators.uniform", ActContractError)
        return cls(decorator, decorator, decorator, decorator, decorator)

    def resolve_port(self, port: ResolvePort, /) -> ResolvePort:
        return apply_port_decorator_boundary(port, self.resolve, ResolvePort, "ResolvePort", ActContractError)

    def authorize_port(self, port: AuthorizePort, /) -> AuthorizePort:
        # AuthorizePort exposes codec metadata as properties.  Structural
        # ``isinstance`` checks would read those properties once, and the
        # owner contract would read them again; defer the complete check to
        # ``require_act_port_contracts`` so each is captured exactly once.
        return apply_port_decorator_boundary(
            port,
            self.authorize,
            AuthorizePort,
            "AuthorizePort",
            ActContractError,
            validate=False,
        )

    def execute_port(self, port: ExecutePort, /) -> ExecutePort:
        return apply_port_decorator_boundary(port, self.execute, ExecutePort, "ExecutePort", ActContractError)

    def settlement_port(self, port: SettlementPort, /) -> SettlementPort:
        return apply_port_decorator_boundary(port, self.settlement, SettlementPort, "SettlementPort", ActContractError)

    def exchange_writer_port(self, port: ToolExchangeWriter, /) -> ToolExchangeWriter:
        return apply_port_decorator_boundary(
            port,
            self.exchange_writer,
            ToolExchangeWriter,
            "ToolExchangeWriter",
            ActContractError,
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


__all__ = [
    "ActFailoverDecorators",
    "FailoverPortDecorator",
    "normalize_act_failover_decorators",
]
