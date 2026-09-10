"""Typed composition contracts for the top-level ReAct loop."""

from collections.abc import Callable
from enum import StrEnum
from typing import TypeAlias, TypeVar

from mote_kernel.act.contract import (
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
)
from mote_kernel.act.contract import (
    HookStateProjection as ActHookStateProjection,
)
from mote_kernel.hooks.contract import HookGraphValue, HookResult
from mote_kernel.observe.contract import (
    HookStateProjection as ObserveHookStateProjection,
)
from mote_kernel.observe.contract import (
    ObserveHookCommand,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
)
from mote_kernel.think.contract import ThinkFrame, ThinkRequest, ThinkStep

ObserveStateT = TypeVar("ObserveStateT", bound=ObserveHookStateProjection)
ObserveHookCommandT = TypeVar("ObserveHookCommandT", bound=ObserveHookCommand)
ThinkPayloadT = TypeVar("ThinkPayloadT")
ThinkStateT = TypeVar("ThinkStateT", bound=HookGraphValue)
ThinkHookCommandT = TypeVar("ThinkHookCommandT", bound=HookGraphValue)
ActStateT = TypeVar("ActStateT", bound=ActHookStateProjection)
ActHookCommandT = TypeVar("ActHookCommandT", bound=ActHookCommand)


class ReActContractError(ValueError):
    """Raised when ReAct assembly or a typed hand-off is invalid."""


class ReActRoute(StrEnum):
    """The closed business routes selected after one Observe completion."""

    CONFIG = "config"
    ASSISTANT = "assistant"
    THINK = "think"
    ACT = "act"


class ReActNodeId(StrEnum):
    """The closed node identities owned by the top-level ReAct graph."""

    OBSERVE = "observe"
    THINK = ReActRoute.THINK
    ACT = ReActRoute.ACT


class ReActPhaseNodeId(StrEnum):
    """The closed callable identities used inside ReAct phase graphs."""

    PREPARE = "prepare"
    PROJECT = "project"
    RUN = "run"
    ROUTE = "route"
    NEXT_OBSERVE = "next_observe"


class ReActValueName(StrEnum):
    """The closed value-port names owned by ReAct composition."""

    REQUEST = "request"
    RESULT = "result"


ObserveRoutePolicy: TypeAlias = Callable[[ObserveResult], ReActRoute]
ObserveToActProjector: TypeAlias = Callable[
    [HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
    ActRequest,
]
ObserveToThinkProjector: TypeAlias = Callable[
    [HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
    ThinkRequest[ThinkPayloadT, ThinkStateT],
]
ThinkToObserveProjector: TypeAlias = Callable[
    [HookResult[ThinkFrame[ThinkStep, ThinkStateT], ThinkHookCommandT]],
    ObserveRequest[ObserveStateT],
]
ActToObserveProjector: TypeAlias = Callable[
    [HookResult[ActHookEnvelope, ActHookCommandT]],
    ObserveRequest[ObserveStateT],
]


__all__: list[str] = []
