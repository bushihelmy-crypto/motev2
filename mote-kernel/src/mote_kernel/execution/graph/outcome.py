"""Factory-owned public outcomes of exactly one callable graph node."""

from dataclasses import InitVar, dataclass
from typing import Generic, TypeAlias, TypeVar, final

from mote_kernel.execution.errors import NodeExecutionContractError
from mote_kernel.execution.graph.values import FactoryValueT, _GraphValues, _require_graph_values

GraphValueT = TypeVar("GraphValueT")


class _OutcomeSeal:
    __slots__ = ()


_OUTCOME_SEAL = _OutcomeSeal()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphSuccessOutcome(Generic[GraphValueT]):
    output: _GraphValues[GraphValueT]
    route: str | None
    _seal: InitVar[_OutcomeSeal]

    def __post_init__(self, _seal: _OutcomeSeal) -> None:
        if _seal is not _OUTCOME_SEAL:
            raise NodeExecutionContractError("success outcomes require the Graph.success() factory")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphFailureOutcome:
    failure: str
    _seal: InitVar[_OutcomeSeal]

    def __post_init__(self, _seal: _OutcomeSeal) -> None:
        if _seal is not _OUTCOME_SEAL:
            raise NodeExecutionContractError("failure outcomes require the Graph.failure() factory")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphInterruptOutcome:
    request_payload: bytes
    _seal: InitVar[_OutcomeSeal]

    def __post_init__(self, _seal: _OutcomeSeal) -> None:
        if _seal is not _OUTCOME_SEAL:
            raise NodeExecutionContractError("interrupt outcomes require the Graph.interrupt() factory")


GraphOutcome: TypeAlias = _GraphSuccessOutcome[GraphValueT] | _GraphFailureOutcome | _GraphInterruptOutcome


def _success(
    output: _GraphValues[FactoryValueT],
    *,
    route: str | None = None,
) -> _GraphSuccessOutcome[FactoryValueT]:
    if route is not None and (
        type(route) is not str or not route or route.strip() != route or "\n" in route or "\r" in route
    ):
        raise NodeExecutionContractError("success route must be a non-empty trimmed string")
    return _GraphSuccessOutcome(output=_require_graph_values(output), route=route, _seal=_OUTCOME_SEAL)


def _failure(reason: str) -> _GraphFailureOutcome:
    if type(reason) is not str or not reason or reason.strip() != reason or "\n" in reason or "\r" in reason:
        raise NodeExecutionContractError("failure reason must be a non-empty trimmed string")
    return _GraphFailureOutcome(failure=reason, _seal=_OUTCOME_SEAL)


def _interrupt(request_payload: bytes) -> _GraphInterruptOutcome:
    if type(request_payload) is not bytes:
        raise NodeExecutionContractError("interrupt request payload must be bytes")
    return _GraphInterruptOutcome(request_payload=request_payload, _seal=_OUTCOME_SEAL)


__all__ = [
    "_GraphFailureOutcome",
    "_GraphInterruptOutcome",
    "_GraphSuccessOutcome",
    "_failure",
    "_interrupt",
    "_success",
]
