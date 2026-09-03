"""Pure observation and routing for the fixed failover graph.

This module is deliberately boring at runtime: it looks up the exact
``(status_code, error_hint)`` pair, checks the immutable plan's counters, and
returns a typed decision.  It never calls a Port, sleeps, reads configuration,
or mutates a cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar, cast

from mote_kernel.failover.contract import (
    Completed,
    ErrorHint,
    FailoverContractError,
    FailureEvidence,
    FailureStrategy,
    InProgress,
    PortOutcome,
    PreparationAction,
    RefreshCredential,
    Rejected,
    RotateCredential,
    SwitchEndpoint,
    TransformRequest,
    Unknown,
    Wait,
)
from mote_kernel.failover.plan import (
    FailoverPlan,
    FailureRule,
    ReconcileMode,
    RetryContext,
)

TransformT = TypeVar("TransformT")
ResultT = TypeVar("ResultT")
ReceiptT = TypeVar("ReceiptT")
HandleT = TypeVar("HandleT")


class ObservationRoute(StrEnum):
    """The outcome of observing one Port activation."""

    COMPLETED = "completed"
    PREPARE = "prepare"
    RECONCILE = "reconcile"
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
        elif self.route is ObservationRoute.RECONCILE:
            if self.strategy is not FailureStrategy.RECONCILE:
                raise FailoverContractError("reconcile decisions require the reconcile strategy")
            if self.preparation is not None:
                raise FailoverContractError("reconcile decisions cannot carry preparation")
        elif self.route is ObservationRoute.ABORT:
            if self.strategy is not FailureStrategy.ABORT:
                raise FailoverContractError("abort decisions require the abort strategy")
            if self.preparation is not None:
                raise FailoverContractError("abort decisions cannot carry preparation")
        else:
            if self.preparation is not None:
                raise FailoverContractError("terminal model decisions cannot carry preparation")


def fixed_rules() -> tuple[FailureRule[None], ...]:
    """Return the versioned built-in status/error-hint policy table.

    A provider adapter normalizes its response into one of these stable hints.
    The table is intentionally exact: a different hint is a different signal,
    and therefore cannot silently acquire a retry behavior.  Request
    transformation is left to a profile rule because only Role config knows
    the typed instruction to apply.
    """

    return (
        FailureRule[None](429, ErrorHint("rate_limited"), FailureStrategy.WAIT),
        FailureRule[None](429, ErrorHint("quota_exceeded"), FailureStrategy.WAIT),
        FailureRule[None](401, ErrorHint("token_expired"), FailureStrategy.REFRESH_CREDENTIAL),
        FailureRule[None](401, ErrorHint("invalid_credential"), FailureStrategy.ROTATE_CREDENTIAL),
        FailureRule[None](401, ErrorHint("invalid_api_key"), FailureStrategy.ROTATE_CREDENTIAL),
        FailureRule[None](503, ErrorHint("service_unavailable"), FailureStrategy.SWITCH_ENDPOINT),
        FailureRule[None](503, ErrorHint("overloaded"), FailureStrategy.SWITCH_ENDPOINT),
        FailureRule[None](408, ErrorHint("request_timeout"), FailureStrategy.WAIT),
        FailureRule[None](504, ErrorHint("request_timeout"), FailureStrategy.WAIT),
    )


def find_rule(
    evidence: FailureEvidence,
    plan: FailoverPlan[TransformT],
) -> FailureRule[TransformT] | None:
    """Find an exact profile rule, then an exact built-in rule."""

    if type(evidence) is not FailureEvidence:
        raise FailoverContractError("rule lookup requires FailureEvidence")
    if type(plan) is not FailoverPlan:
        raise FailoverContractError("rule lookup requires FailoverPlan")
    for rule in plan.profile.rules:
        if rule.signal == evidence.signal:
            return rule
    for rule in fixed_rules():
        if rule.signal == evidence.signal:
            return FailureRule[TransformT](
                rule.status_code,
                rule.error_hint,
                rule.strategy,
            )
    return None


def route_rejected(
    evidence: FailureEvidence,
    plan: FailoverPlan[TransformT],
    context: RetryContext[ReceiptT, HandleT],
) -> FailoverDecision[TransformT]:
    """Route a definitive rejection without performing the next action."""

    rule = find_rule(evidence, plan)
    if rule is None:
        return _return_to_model(evidence, plan=plan)
    return _route_rule(rule, evidence, plan, context)


def route_uncertain(
    evidence: FailureEvidence | None,
    has_reconcile_handle: bool,
    plan: FailoverPlan[TransformT],
    context: RetryContext[ReceiptT, HandleT],
) -> FailoverDecision[TransformT]:
    """Route an uncertain outcome.

    An uncertain operation is reconciled only when the adapter supplied a
    real handle and the profile permits reconciliation.  Without a handle the
    only safe route is to return control to the model.
    """

    if evidence is not None and type(evidence) is not FailureEvidence:
        raise FailoverContractError("uncertain routing requires FailureEvidence")
    if type(has_reconcile_handle) is not bool:
        raise FailoverContractError("has_reconcile_handle must be a bool")
    if not has_reconcile_handle:
        return _return_to_model(evidence, plan=plan)
    if plan.profile.reconcile is ReconcileMode.DISABLED:
        return _return_to_model(evidence, plan=plan)
    return _route_strategy(FailureStrategy.RECONCILE, evidence, plan, context, None)


def observe_and_route(
    outcome: PortOutcome[ResultT, ReceiptT, HandleT],
    plan: FailoverPlan[TransformT],
    context: RetryContext[ReceiptT, HandleT],
) -> FailoverDecision[TransformT]:
    """Observe one typed Port result and choose a fixed graph route."""

    if type(plan) is not FailoverPlan:
        raise FailoverContractError("observation requires a FailoverPlan")
    if type(context) is not RetryContext:
        raise FailoverContractError("observation requires a RetryContext")
    if isinstance(outcome, Completed):
        return FailoverDecision(ObservationRoute.COMPLETED)
    if isinstance(outcome, Rejected):
        return route_rejected(outcome.evidence, plan, context)
    if isinstance(outcome, InProgress):
        return route_uncertain(None, True, plan, context)
    if type(outcome) is Unknown:
        return route_uncertain(outcome.evidence, outcome.handle is not None, plan, context)
    raise FailoverContractError("observation received an unsupported Port outcome")


def _route_rule(
    rule: FailureRule[TransformT],
    evidence: FailureEvidence,
    plan: FailoverPlan[TransformT],
    context: RetryContext[ReceiptT, HandleT],
) -> FailoverDecision[TransformT]:
    return _route_strategy(rule.strategy, evidence, plan, context, rule.preparation)


def _route_strategy(
    strategy: FailureStrategy,
    evidence: FailureEvidence | None,
    plan: FailoverPlan[TransformT],
    context: RetryContext[ReceiptT, HandleT],
    configured_preparation: PreparationAction[TransformT] | None,
) -> FailoverDecision[TransformT]:
    if strategy is FailureStrategy.RETURN_TO_MODEL:
        return _return_to_model(evidence, plan=plan, strategy=strategy)
    if strategy is FailureStrategy.ABORT:
        return FailoverDecision(
            ObservationRoute.ABORT,
            strategy=FailureStrategy.ABORT,
            evidence=evidence,
        )
    limit = plan.profile.budget.max_uses_for(strategy)
    if context.uses_for(strategy) >= limit:
        return _return_to_model(evidence, plan=plan, strategy=strategy, budget_exhausted=True)
    if strategy is not FailureStrategy.RECONCILE and _wire_budget_exhausted(plan, context):
        return _return_to_model(evidence, plan=plan, strategy=strategy, budget_exhausted=True)
    if strategy is FailureStrategy.RECONCILE:
        return FailoverDecision(
            ObservationRoute.RECONCILE,
            strategy=FailureStrategy.RECONCILE,
            evidence=evidence,
        )
    preparation = _preparation_for(
        strategy,
        configured_preparation,
        cast(FailureEvidence, evidence),
        plan,
        context,
    )
    return FailoverDecision(
        ObservationRoute.PREPARE,
        strategy=strategy,
        preparation=preparation,
        evidence=evidence,
    )


def _wire_budget_exhausted(plan: FailoverPlan[TransformT], context: RetryContext[ReceiptT, HandleT]) -> bool:
    # ``attempt_ordinal`` is the number of wire attempts already committed;
    # the first attempt is ordinal zero in a fresh context.  No preparation is
    # allowed once another invocation would exceed the configured maximum.
    return context.attempt_ordinal >= plan.profile.budget.max_wire_attempts - 1


def _preparation_for(
    strategy: FailureStrategy,
    configured: PreparationAction[TransformT] | None,
    evidence: FailureEvidence,
    plan: FailoverPlan[TransformT],
    context: RetryContext[ReceiptT, HandleT],
) -> PreparationAction[TransformT]:
    if strategy is FailureStrategy.TRANSFORM_REQUEST:
        return cast(TransformRequest[TransformT], configured)
    if configured is not None:
        return _apply_retry_after(configured, evidence)
    if strategy is FailureStrategy.WAIT:
        return Wait(_backoff_seconds(plan, context, evidence))
    if strategy is FailureStrategy.REFRESH_CREDENTIAL:
        return RefreshCredential()
    if strategy is FailureStrategy.ROTATE_CREDENTIAL:
        return RotateCredential()
    # _route_strategy has already consumed every terminal/reconcile variant;
    # SWITCH_ENDPOINT is the only admitted preparation strategy left here.
    return SwitchEndpoint()


def _apply_retry_after(
    configured: PreparationAction[TransformT],
    evidence: FailureEvidence,
) -> PreparationAction[TransformT]:
    if not isinstance(configured, Wait) or evidence.retry_after_seconds is None:
        return configured
    return Wait(max(configured.delay_seconds, evidence.retry_after_seconds))


def _backoff_seconds(
    plan: FailoverPlan[TransformT],
    context: RetryContext[ReceiptT, HandleT],
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


def _return_to_model(
    evidence: FailureEvidence | None,
    *,
    plan: FailoverPlan[TransformT],
    strategy: FailureStrategy | None = None,
    budget_exhausted: bool = False,
) -> FailoverDecision[TransformT]:
    return FailoverDecision[TransformT](
        ObservationRoute.RETURN_TO_MODEL,
        strategy=strategy,
        evidence=evidence,
        budget_exhausted=budget_exhausted,
    )


__all__ = [
    "FailoverDecision",
    "ObservationRoute",
    "find_rule",
    "fixed_rules",
    "observe_and_route",
    "route_rejected",
    "route_uncertain",
]
