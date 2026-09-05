"""Deterministic tests for status/error-hint routing decisions."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from mote_kernel.failover.contract import (
    Completed,
    ErrorHint,
    FailoverContractError,
    FailureClass,
    FailureEvidence,
    FailureStrategy,
    InProgress,
    PortOutcome,
    RefreshCredential,
    Rejected,
    TransformRequest,
    Unknown,
    Wait,
)
from mote_kernel.failover.plan import (
    FailoverConfigRevision,
    FailoverOperationId,
    FailoverPlan,
    FailoverPortId,
    FailoverProfile,
    FailoverProfileId,
    ReconcileMode,
    RetryBudget,
    RetryContext,
    RetryTiming,
    StrategyLimit,
    StrategyUsage,
)
from mote_kernel.failover.policy import (
    FailoverDecision,
    ObservationRoute,
    observe_and_route,
    route_rejected,
    route_uncertain,
)

InvalidFactory = Callable[[], object]


def _profile(
    budget: RetryBudget | None = None,
    reconcile: ReconcileMode = ReconcileMode.OPTIONAL,
    request_transform: TransformRequest[str] | None = None,
    timing: RetryTiming | None = None,
) -> FailoverProfile[str]:
    return FailoverProfile(
        FailoverProfileId("standard"),
        budget=budget or RetryBudget(),
        timing=timing or RetryTiming(base_backoff_seconds=1.0, max_backoff_seconds=8.0),
        reconcile=reconcile,
        request_transform=request_transform,
    )


def _plan(
    budget: RetryBudget | None = None,
    reconcile: ReconcileMode = ReconcileMode.OPTIONAL,
    request_transform: TransformRequest[str] | None = None,
    timing: RetryTiming | None = None,
) -> FailoverPlan[str]:
    return FailoverPlan(
        FailoverConfigRevision(1),
        FailoverPortId("payment"),
        _profile(
            budget=budget,
            reconcile=reconcile,
            request_transform=request_transform,
            timing=timing,
        ),
    )


def _context(
    *,
    attempt_ordinal: int = 0,
    strategy_usages: tuple[StrategyUsage, ...] = (),
) -> RetryContext[object, object]:
    return RetryContext[object, object](
        FailoverOperationId("operation-1"),
        FailoverConfigRevision(1),
        attempt_ordinal=attempt_ordinal,
        strategy_usages=strategy_usages,
    )


def _evidence(
    status_code: int | None,
    hint: str | None,
    category: FailureClass = FailureClass.UNKNOWN_OUTCOME,
    *,
    retry_after: float | None = None,
) -> FailureEvidence:
    return FailureEvidence(
        category, status_code, ErrorHint(hint) if hint is not None else None, retry_after_seconds=retry_after
    )


def test_fixed_policy_matches_the_full_status_and_hint_pair() -> None:
    rate_limit = route_rejected(_evidence(429, "rate_limited"), _plan(), _context())
    different_hint = route_rejected(_evidence(429, "other"), _plan(), _context())
    missing_status = route_rejected(_evidence(None, "rate_limited"), _plan(), _context())

    assert rate_limit.strategy is FailureStrategy.WAIT
    assert different_hint.route is ObservationRoute.RETURN_TO_MODEL
    assert missing_status.route is ObservationRoute.RETURN_TO_MODEL


def test_configured_parameters_cannot_create_a_new_failure_mapping() -> None:
    plan = _plan(request_transform=TransformRequest("drop-invalid-field"))

    decision = route_rejected(_evidence(422, "bad_payload"), plan, _context())

    assert decision.route is ObservationRoute.RETURN_TO_MODEL
    assert decision.strategy is None


@pytest.mark.parametrize(
    ("status_code", "hint", "strategy"),
    (
        (429, "rate_limited", FailureStrategy.WAIT),
        (429, "quota_exceeded", FailureStrategy.WAIT),
        (401, "token_expired", FailureStrategy.REFRESH_CREDENTIAL),
        (401, "invalid_credential", FailureStrategy.ROTATE_CREDENTIAL),
        (401, "invalid_api_key", FailureStrategy.ROTATE_CREDENTIAL),
        (503, "service_unavailable", FailureStrategy.SWITCH_ENDPOINT),
        (503, "overloaded", FailureStrategy.SWITCH_ENDPOINT),
        (408, "request_timeout", FailureStrategy.WAIT),
        (504, "request_timeout", FailureStrategy.WAIT),
    ),
)
def test_default_status_and_hint_pairs_choose_the_fixed_strategy(
    status_code: int,
    hint: str,
    strategy: FailureStrategy,
) -> None:
    decision = route_rejected(_evidence(status_code, hint), _plan(), _context())
    assert decision.route is ObservationRoute.PREPARE
    assert decision.strategy is strategy


def test_same_status_code_can_choose_different_credential_strategies() -> None:
    refresh = route_rejected(_evidence(401, "token_expired"), _plan(), _context())
    rotate = route_rejected(_evidence(401, "invalid_api_key"), _plan(), _context())

    assert refresh.strategy is FailureStrategy.REFRESH_CREDENTIAL
    assert rotate.strategy is FailureStrategy.ROTATE_CREDENTIAL
    assert type(refresh.preparation).__name__ == "RefreshCredential"
    assert type(rotate.preparation).__name__ == "RotateCredential"


def test_profile_supplies_parameters_for_the_fixed_request_transform() -> None:
    transform = TransformRequest("drop-invalid-field")
    decision = route_rejected(
        _evidence(400, "bad_payload"),
        _plan(request_transform=transform),
        _context(),
    )

    assert decision.route is ObservationRoute.PREPARE
    assert decision.strategy is FailureStrategy.TRANSFORM_REQUEST
    assert decision.preparation is transform


def test_missing_request_transform_parameters_return_control_to_the_model() -> None:
    decision = route_rejected(_evidence(400, "bad_payload"), _plan(), _context())

    assert decision.route is ObservationRoute.RETURN_TO_MODEL
    assert decision.strategy is FailureStrategy.TRANSFORM_REQUEST
    assert decision.preparation is None


def test_wait_uses_exponential_backoff_and_provider_retry_after() -> None:
    evidence = _evidence(429, "rate_limited", retry_after=5.0)
    decision = route_rejected(evidence, _plan(), _context(attempt_ordinal=1))

    assert isinstance(decision.preparation, Wait)
    assert decision.preparation.delay_seconds == 5.0  # 1 * 2**2, then provider floor is equal
    second = route_rejected(_evidence(429, "rate_limited", retry_after=7.0), _plan(), _context())
    assert isinstance(second.preparation, Wait)
    assert second.preparation.delay_seconds == 7.0


def test_hot_loaded_timing_parameters_change_delay_without_changing_mapping() -> None:
    timing = RetryTiming(base_backoff_seconds=2.0, max_backoff_seconds=10.0)
    decision = route_rejected(
        _evidence(429, "rate_limited", retry_after=4.0),
        _plan(timing=timing),
        _context(attempt_ordinal=1),
    )

    assert isinstance(decision.preparation, Wait)
    assert decision.preparation.delay_seconds == 4.0


def test_retry_after_does_not_change_a_fixed_non_wait_strategy() -> None:
    decision = route_rejected(
        _evidence(401, "token_expired", retry_after=4.0),
        _plan(),
        _context(),
    )

    assert isinstance(decision.preparation, RefreshCredential)


def test_strategy_and_wire_budgets_are_checked_before_preparation() -> None:
    budget = RetryBudget(
        max_wire_attempts=3,
        strategy_limits=(StrategyLimit(FailureStrategy.WAIT, 1),),
    )
    plan = _plan(budget=budget)
    used = _context(strategy_usages=(StrategyUsage(FailureStrategy.WAIT, 1),))
    exhausted_strategy = route_rejected(_evidence(429, "rate_limited"), plan, used)
    exhausted_wire = route_rejected(_evidence(429, "rate_limited"), plan, _context(attempt_ordinal=2))

    assert exhausted_strategy.route is ObservationRoute.RETURN_TO_MODEL
    assert exhausted_strategy.budget_exhausted is True
    assert exhausted_wire.route is ObservationRoute.RETURN_TO_MODEL
    assert exhausted_wire.budget_exhausted is True


def test_unknown_without_handle_returns_to_model_even_with_a_status_code() -> None:
    unknown = Unknown[object](None, _evidence(504, "request_timeout", FailureClass.NO_RESPONSE))
    decision = observe_and_route(unknown, _plan(), _context())

    assert decision.route is ObservationRoute.RETURN_TO_MODEL
    assert decision.evidence is unknown.evidence


def test_unknown_with_handle_reconciles_and_counts_reconcile_strategy() -> None:
    unknown = Unknown("operation-receipt", _evidence(None, "no_response", FailureClass.NO_RESPONSE))
    decision = observe_and_route(unknown, _plan(), _context())

    assert decision.route is ObservationRoute.RECONCILE
    assert decision.strategy is FailureStrategy.RECONCILE


def test_in_progress_receipt_reconciles_without_fabricating_failure_evidence() -> None:
    decision = observe_and_route(InProgress("receipt-1"), _plan(), _context())

    assert decision.route is ObservationRoute.RECONCILE
    assert decision.evidence is None


def test_reconcile_disabled_returns_uncertain_result_to_model() -> None:
    unknown = Unknown("operation-receipt", _evidence(None, "no_response", FailureClass.NO_RESPONSE))
    decision = observe_and_route(
        unknown,
        _plan(reconcile=ReconcileMode.DISABLED),
        _context(),
    )

    assert decision.route is ObservationRoute.RETURN_TO_MODEL


def test_reconcile_budget_exhaustion_returns_uncertain_result_to_model() -> None:
    budget = RetryBudget(strategy_limits=(StrategyLimit(FailureStrategy.RECONCILE, 0),))
    unknown = Unknown("operation-receipt", _evidence(None, "no_response", FailureClass.NO_RESPONSE))

    decision = observe_and_route(unknown, _plan(budget=budget), _context())

    assert decision.route is ObservationRoute.RETURN_TO_MODEL
    assert decision.strategy is FailureStrategy.RECONCILE
    assert decision.budget_exhausted is True


def test_completed_outcome_is_terminal_success_route() -> None:
    decision = observe_and_route(Completed("ok"), _plan(), _context())
    assert decision == FailoverDecision(ObservationRoute.COMPLETED)


def test_observer_routes_a_definitive_rejection_through_the_exact_rule() -> None:
    evidence = _evidence(429, "rate_limited")

    decision = observe_and_route(Rejected(evidence), _plan(), _context())

    assert decision.route is ObservationRoute.PREPARE
    assert decision.strategy is FailureStrategy.WAIT


def test_fixed_terminal_and_abort_rules_are_projected() -> None:
    model = route_rejected(_evidence(403, "forbidden"), _plan(), _context())
    abort = route_rejected(_evidence(409, "conflict"), _plan(), _context())
    unknown = route_rejected(_evidence(500, "unlisted"), _plan(), _context())

    assert model.route is ObservationRoute.RETURN_TO_MODEL
    assert abort.route is ObservationRoute.ABORT
    assert abort.strategy is FailureStrategy.ABORT
    assert unknown.route is ObservationRoute.RETURN_TO_MODEL


@pytest.mark.parametrize(
    "factory",
    (
        lambda: route_rejected(cast(FailureEvidence, object()), _plan(), _context()),
        lambda: route_rejected(_evidence(429, "rate_limited"), cast(FailoverPlan[str], object()), _context()),
        lambda: route_rejected(_evidence(429, "rate_limited"), _plan(), cast(RetryContext[object, object], object())),
        lambda: route_uncertain(cast(FailureEvidence, object()), True, _plan(), _context()),
        lambda: route_uncertain(_evidence(429, "rate_limited"), cast(bool, "yes"), _plan(), _context()),
        lambda: route_uncertain(_evidence(429, "rate_limited"), True, cast(FailoverPlan[str], object()), _context()),
        lambda: route_uncertain(
            _evidence(429, "rate_limited"), True, _plan(), cast(RetryContext[object, object], object())
        ),
        lambda: observe_and_route(cast(PortOutcome[str, object, object], object()), _plan(), _context()),
        lambda: observe_and_route(Completed("ok"), cast(FailoverPlan[str], object()), _context()),
        lambda: observe_and_route(Completed("ok"), _plan(), cast(RetryContext[object, object], object())),
    ),
)
def test_policy_rejects_invalid_boundaries(factory: InvalidFactory) -> None:
    with pytest.raises(FailoverContractError):
        factory()


def test_policy_rejects_a_context_from_another_plan_revision() -> None:
    context = RetryContext[object, object](
        FailoverOperationId("operation-1"),
        FailoverConfigRevision(2),
    )

    with pytest.raises(FailoverContractError, match="captured plan revision"):
        route_rejected(_evidence(429, "rate_limited"), _plan(), context)
    with pytest.raises(FailoverContractError, match="captured plan revision"):
        route_uncertain(None, True, _plan(), context)
    with pytest.raises(FailoverContractError, match="captured plan revision"):
        observe_and_route(Completed("ok"), _plan(), context)


@pytest.mark.parametrize(
    "factory",
    (
        lambda: FailoverDecision[str](cast(ObservationRoute, "completed")),
        lambda: FailoverDecision[str](
            ObservationRoute.PREPARE, strategy=cast(FailureStrategy, "wait"), preparation=Wait(1.0)
        ),
        lambda: FailoverDecision[str](
            ObservationRoute.PREPARE,
            strategy=FailureStrategy.WAIT,
            preparation=Wait(1.0),
            evidence=cast(FailureEvidence, object()),
        ),
        lambda: FailoverDecision[str](ObservationRoute.COMPLETED, strategy=FailureStrategy.WAIT),
        lambda: FailoverDecision[str](ObservationRoute.PREPARE, strategy=FailureStrategy.WAIT),
        lambda: FailoverDecision[str](
            ObservationRoute.PREPARE,
            strategy=FailureStrategy.RECONCILE,
            preparation=Wait(1.0),
        ),
        lambda: FailoverDecision[str](ObservationRoute.RECONCILE, strategy=FailureStrategy.WAIT),
        lambda: FailoverDecision[str](
            ObservationRoute.RECONCILE,
            strategy=FailureStrategy.RECONCILE,
            preparation=Wait(1.0),
        ),
        lambda: FailoverDecision[str](ObservationRoute.ABORT, strategy=FailureStrategy.WAIT),
        lambda: FailoverDecision[str](
            ObservationRoute.ABORT,
            strategy=FailureStrategy.ABORT,
            preparation=Wait(1.0),
        ),
        lambda: FailoverDecision[str](ObservationRoute.RETURN_TO_MODEL, preparation=Wait(1.0)),
        lambda: FailoverDecision[str](
            ObservationRoute.PREPARE, strategy=FailureStrategy.WAIT, preparation=Wait(1.0), budget_exhausted=True
        ),
    ),
)
def test_decision_validates_route_shape(factory: InvalidFactory) -> None:
    with pytest.raises(FailoverContractError):
        factory()


def test_decisions_are_immutable() -> None:
    decision = FailoverDecision[str](ObservationRoute.COMPLETED)
    with pytest.raises(FrozenInstanceError):
        decision.route = ObservationRoute.ABORT  # type: ignore[misc]
