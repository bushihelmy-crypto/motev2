from typing import TypeVar, assert_type

from mote_kernel.act.contract import ActRequest, ResolvePortResult
from mote_kernel.act.port import ResolvePort
from mote_kernel.failover.contract import PortDecorator, TypedPortDecorator, apply_port_decorator


class R:
    async def resolve(self, request: ActRequest, /) -> ResolvePortResult:
        raise NotImplementedError


class D:
    def __call__(self, port: ResolvePort, /) -> ResolvePort:
        return port


def accepts_typed(value: TypedPortDecorator[ResolvePort]) -> None:
    pass


def check(port: ResolvePort) -> None:
    decorated = apply_port_decorator(port, D(), ResolvePort, "resolve")
    assert_type(decorated, ResolvePort)
    accepts_typed(D())


UPort = TypeVar("UPort")


class U:
    def __call__(self, port: UPort, /) -> UPort:
        return port


def accepts_uniform(value: PortDecorator) -> None:
    pass


accepts_uniform(U())


def check_uniform(port: ResolvePort) -> None:
    decorated2 = apply_port_decorator(port, U(), ResolvePort, "resolve")
    assert_type(decorated2, ResolvePort)
