"""Pure observation and routing for the fixed failover graph.

This module is deliberately boring at runtime: it looks up the exact
``(status_code, error_hint)`` pair, checks the immutable plan's counters, and
returns a typed decision.  It never calls a Port, sleeps, reads configuration,
or mutates a cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

from mote_kernel.failover.contract import (
    Completed,
    ErrorHint,
    FailoverContractError,
    FailureEvidence,
    FailureSignal,
    FailureStrategy,
    InProgress,
    PortOutcome,
    PreparationAction,
    RefreshCredential,
    Rejected,
    RotateCredential,
    SwitchEndpoint,
    Unknown,
    Wait,
)
from mote_kernel.failover.plan import FailoverPlan, RetryContext

TransformT = TypeVar("TransformT")
ResultT = TypeVar("ResultT")
ReceiptT = TypeVar("ReceiptT")
HandleT = TypeVar("HandleT")


class ObservationRoute(StrEnum):
    """The outcome of observing one Port activation."""

    COMPLETED = "completed"
    PREPARE = "prepare"
    RETURN_TO_MODEL = "return_to_model"
    ABORT = "abort"


@dataclass(frozen=True, slots=True)
class FailoverDecision(Generic[TransformT]):
    """A pure, typed choice made by ``ObserveAndRoute``."""

    route: ObservationRoute
    strategy: FailureStrategy | None = None
    preparation: PreparationAction[TransformT] | None = None
    evidence: FailureEvidence | None = None
    budget_exhausted: bool = False

    def __post_init__(self) -> None:
        if type(self.route) is not ObservationRoute:
            raise FailoverContractError("failover decision route must be an ObservationRoute")
        if self.strategy is not None and type(self.strategy) is not FailureStrategy:
            raise FailoverContractError("failover decision strategy must be a FailureStrategy")
        if self.evidence is not None and type(self.evidence) is not FailureEvidence:
            raise FailoverContractError("failover decision evidence must be FailureEvidence")
        if self.budget_exhausted and self.route is not ObservationRoute.RETURN_TO_MODEL:
            raise FailoverContractError("an exhausted budget must return to the model")
        if self.route is ObservationRoute.COMPLETED:
            if self.strategy is not None or self.preparation is not None or self.evidence is not None:
                raise FailoverContractError("completed decisions cannot carry failure data")
        elif self.route is ObservationRoute.PREPARE:
            if self.strategy not in (
                FailureStrategy.WAIT,
                FailureStrategy.TRANSFORM_REQUEST,
                FailureStrategy.REFRESH_CREDENTIAL,
                FailureStrategy.ROTATE_CREDENTIAL,
                FailureStrategy.SWITCH_ENDPOINT,
            ):
                raise FailoverContractError("prepare decisions require a preparation strategy")
            if self.preparation is None:
                raise FailoverContractError("prepare decisions require a preparation action")
        elif self.route is ObservationRoute.ABORT:
            if self.strategy is not FailureStrategy.ABORT:
                raise FailoverContractError("abort decisions require the abort strategy")
            if self.preparation is not None:
                raise FailoverContractError("abort decisions cannot carry preparation")
        else:
            if self.preparation is not None:
                raise FailoverContractError("terminal model decisions cannot carry preparation")


@dataclass(frozen=True, slots=True)
class _FailureRule:
    signal: FailureSignal
    strategy: FailureStrategy


_FIXED_RULES = (
    _FailureRule(FailureSignal(429, ErrorHint("rate_limited")), FailureStrategy.WAIT),
    _FailureRule(FailureSignal(429, ErrorHint("quota_exceeded")), FailureStrategy.WAIT),
    _FailureRule(FailureSignal(401, ErrorHint("token_expired")), FailureStrategy.REFRESH_CREDENTIAL),
    _FailureRule(FailureSignal(401, ErrorHint("invalid_credential")), FailureStrategy.ROTATE_CREDENTIAL),
    _FailureRule(FailureSignal(401, ErrorHint("invalid_api_key")), FailureStrategy.ROTATE_CREDENTIAL),
    _FailureRule(FailureSignal(503, ErrorHint("service_unavailable")), FailureStrategy.SWITCH_ENDPOINT),
    _FailureRule(FailureSignal(503, ErrorHint("overloaded")), FailureStrategy.SWITCH_ENDPOINT),
    _FailureRule(FailureSignal(408, ErrorHint("request_timeout")), FailureStrategy.WAIT),
    _FailureRule(FailureSignal(504, ErrorHint("request_timeout")), FailureStrategy.WAIT),
    _FailureRule(FailureSignal(400, ErrorHint("bad_payload")), FailureStrategy.TRANSFORM_REQUEST),
    _FailureRule(FailureSignal(403, ErrorHint("forbidden")), FailureStrategy.RETURN_TO_MODEL),
    _FailureRule(FailureSignal(409, ErrorHint("conflict")), FailureStrategy.ABORT),
)


def _fixed_strategy(evidence: FailureEvidence) -> FailureStrategy | None:
    """Resolve the one code-owned status/error-hint policy table.

    A provider adapter normalizes its response into one of these stable hints.
    The table is intentionally exact: a different hint is a different signal,
    and therefore cannot silently acquire a retry behavior.  Role config owns
    only the parameters used after this fixed choice.
    """

    for rule in _FIXED_RULES:
        if rule.signal == evidence.signal:
            return rule.strategy
    return None


def route_rejected(
    evidence: FailureEvidence,
    plan: FailoverPlan[TransformT],
    context: RetryContext,
) -> FailoverDecision[TransformT]:
    """Route a definitive rejection without performing the next action."""

    if type(evidence) is not FailureEvidence:
        raise FailoverContractError("rejected routing requires FailureEvidence")
    if type(plan) is not FailoverPlan:
        raise FailoverContractError("rejected routing requires FailoverPlan")
    if type(context) is not RetryContext:
        raise FailoverContractError("rejected routing requires RetryContext")
    if context.plan_revision != plan.plan_revision:
        raise FailoverContractError("rejected routing requires the context's captured plan revision")
    strategy = _fixed_strategy(evidence)
    if strategy is None:
        return FailoverDecision(ObservationRoute.RETURN_TO_MODEL, evidence=evidence)
    if strategy is FailureStrategy.RETURN_TO_MODEL:
        return FailoverDecision(ObservationRoute.RETURN_TO_MODEL, strategy=strategy, evidence=evidence)
    if strategy is FailureStrategy.ABORT:
        return FailoverDecision(
            ObservationRoute.ABORT,
            strategy=FailureStrategy.ABORT,
            evidence=evidence,
        )
    budget = plan.profile.budget
    if (
        context.uses_for(strategy) >= budget.max_uses_for(strategy)
        or context.attempt_ordinal >= budget.max_wire_attempts - 1
    ):
        return FailoverDecision(
            ObservationRoute.RETURN_TO_MODEL,
            strategy=strategy,
            evidence=evidence,
            budget_exhausted=True,
        )
    if strategy is FailureStrategy.TRANSFORM_REQUEST:
        preparation = plan.profile.request_transform
        if preparation is None:
            return FailoverDecision(
                ObservationRoute.RETURN_TO_MODEL,
                strategy=strategy,
                evidence=evidence,
            )
    else:
        preparation = _preparation_for(strategy, evidence, plan, context)
    return FailoverDecision(
        ObservationRoute.PREPARE,
        strategy=strategy,
        preparation=preparation,
        evidence=evidence,
    )


def observe_and_route(
    outcome: PortOutcome[ResultT, ReceiptT, HandleT],
    plan: FailoverPlan[TransformT],
    context: RetryContext,
) -> FailoverDecision[TransformT]:
    """Observe one typed Port result and choose a fixed graph route."""

    if type(plan) is not FailoverPlan:
        raise FailoverContractError("observation requires a FailoverPlan")
    if type(context) is not RetryContext:
        raise FailoverContractError("observation requires a RetryContext")
    if context.plan_revision != plan.plan_revision:
        raise FailoverContractError("observation requires the context's captured plan revision")
    if isinstance(outcome, Completed):
        return FailoverDecision(ObservationRoute.COMPLETED)
    if isinstance(outcome, Rejected):
        return route_rejected(outcome.evidence, plan, context)
    if isinstance(outcome, InProgress):
        return FailoverDecision(ObservationRoute.RETURN_TO_MODEL)
    if type(outcome) is Unknown:
        return FailoverDecision(ObservationRoute.RETURN_TO_MODEL, evidence=outcome.evidence)
    raise FailoverContractError("observation received an unsupported Port outcome")


def _preparation_for(
    strategy: FailureStrategy,
    evidence: FailureEvidence,
    plan: FailoverPlan[TransformT],
    context: RetryContext,
) -> PreparationAction[TransformT]:
    if strategy is FailureStrategy.WAIT:
        return Wait(_backoff_seconds(plan, context, evidence))
    if strategy is FailureStrategy.REFRESH_CREDENTIAL:
        return RefreshCredential()
    if strategy is FailureStrategy.ROTATE_CREDENTIAL:
        return RotateCredential()
    # route_rejected has already consumed every terminal variant;
    # SWITCH_ENDPOINT is the only admitted preparation strategy left here.
    return SwitchEndpoint()


def _backoff_seconds(
    plan: FailoverPlan[TransformT],
    context: RetryContext,
    evidence: FailureEvidence,
) -> float:
    timing = plan.profile.timing
    delay = timing.base_backoff_seconds
    for _ in range(min(context.attempt_ordinal, 1024)):
        delay = min(timing.max_backoff_seconds, delay * 2)
    delay = min(timing.max_backoff_seconds, delay)
    if evidence.retry_after_seconds is not None:
        delay = max(delay, evidence.retry_after_seconds)
    return delay


__all__ = [
    "FailoverDecision",
    "ObservationRoute",
    "observe_and_route",
    "route_rejected",
]
