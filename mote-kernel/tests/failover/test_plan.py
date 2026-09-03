"""Deterministic tests for immutable failover plans and bindings."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import cast

import pytest

from mote_kernel.failover.contract import (
    ErrorHint,
    FailoverContractError,
    FailureClass,
    FailureSignal,
    FailureStrategy,
    PreparationAction,
    RefreshCredential,
    RotateCredential,
    SwitchEndpoint,
    TransformRequest,
    Wait,
)
from mote_kernel.failover.plan import (
    FailoverBindingMode,
    FailoverConfigRevision,
    FailoverConfigSnapshot,
    FailoverConfigSource,
    FailoverOperationId,
    FailoverPlan,
    FailoverPolicyId,
    FailoverPortId,
    FailoverProfile,
    FailoverProfileId,
    FailoverProfileOverride,
    FailureRoute,
    FailureRule,
    OperationSemantics,
    PortBinding,
    ReconcileMode,
    RetryBudget,
    RetryContext,
    RetryTiming,
    StrategyLimit,
    StrategyUsage,
    merge_profile,
    resolve_plan,
)

InvalidFactory = Callable[[], object]


def _rule(
    status_code: int | None = 429,
    error_hint: str | None = "rate_limited",
    strategy: FailureStrategy = FailureStrategy.WAIT,
    *,
    preparation: PreparationAction[str] | None = None,
) -> FailureRule[str]:
    return FailureRule(status_code, ErrorHint(error_hint) if error_hint is not None else None, strategy, preparation)


def _profile(*rules: FailureRule[str], budget: RetryBudget | None = None) -> FailoverProfile[str]:
    return FailoverProfile(
        FailoverProfileId("standard"),
        FailoverPolicyId("default-v1"),
        rules,
        budget=budget or RetryBudget(),
    )


def test_failure_rule_uses_a_status_and_hint_pair_and_projects_fixed_routes() -> None:
    rules = (
        _rule(429, "rate_limited", FailureStrategy.WAIT, preparation=Wait(1.0)),
        _rule(400, "bad_payload", FailureStrategy.TRANSFORM_REQUEST, preparation=TransformRequest("drop-field")),
        _rule(401, "token_expired", FailureStrategy.REFRESH_CREDENTIAL, preparation=RefreshCredential()),
        _rule(401, "invalid_key", FailureStrategy.ROTATE_CREDENTIAL, preparation=RotateCredential()),
        _rule(503, "busy", FailureStrategy.SWITCH_ENDPOINT, preparation=SwitchEndpoint()),
        _rule(500, "poll", FailureStrategy.RECONCILE),
        _rule(403, "denied", FailureStrategy.RETURN_TO_MODEL),
        _rule(409, "conflict", FailureStrategy.ABORT),
    )

    assert rules[0].signal == FailureSignal(429, ErrorHint("rate_limited"))
    assert rules[0].route is FailureRoute.PREPARE
    assert rules[1].route is FailureRoute.PREPARE
    assert rules[2].route is FailureRoute.PREPARE
    assert rules[3].route is FailureRoute.PREPARE
    assert rules[4].route is FailureRoute.PREPARE
    assert rules[5].route is FailureRoute.RECONCILE
    assert rules[6].route is FailureRoute.RETURN_TO_MODEL
    assert rules[7].route is FailureRoute.ABORT


@pytest.mark.parametrize(
    ("status_code", "error_hint", "strategy", "preparation"),
    (
        ("429", "rate_limited", FailureStrategy.WAIT, Wait(1.0)),
        (429, " hint", FailureStrategy.WAIT, Wait(1.0)),
        (429, "rate_limited", "wait", Wait(1.0)),
        (429, "rate_limited", FailureStrategy.WAIT, object()),
        (429, "rate_limited", FailureStrategy.TRANSFORM_REQUEST, None),
        (429, "rate_limited", FailureStrategy.REFRESH_CREDENTIAL, RotateCredential()),
        (429, "rate_limited", FailureStrategy.ROTATE_CREDENTIAL, RefreshCredential()),
        (429, "rate_limited", FailureStrategy.SWITCH_ENDPOINT, RefreshCredential()),
        (429, "rate_limited", FailureStrategy.RECONCILE, Wait(1.0)),
    ),
)
def test_failure_rule_rejects_invalid_shape(
    status_code: object,
    error_hint: object,
    strategy: object,
    preparation: object,
) -> None:
    with pytest.raises(FailoverContractError):
        FailureRule(status_code, error_hint, strategy, preparation)  # type: ignore[arg-type]


def test_strategy_limits_are_independent_and_budget_resolution_falls_back() -> None:
    budget = RetryBudget(
        max_wire_attempts=4,
        hard_max_wire_attempts=8,
        default_max_strategy_uses=3,
        strategy_limits=(
            StrategyLimit(FailureStrategy.WAIT, 5),
            StrategyLimit(FailureStrategy.ROTATE_CREDENTIAL, 2),
        ),
    )

    assert budget.max_uses_for(FailureStrategy.WAIT) == 5
    assert budget.max_uses_for(FailureStrategy.SWITCH_ENDPOINT) == 3
    assert budget.max_uses_for(FailureStrategy.ROTATE_CREDENTIAL) == 2
    with pytest.raises(FailoverContractError):
        budget.max_uses_for("wait")  # type: ignore[arg-type]
    with pytest.raises(FrozenInstanceError):
        budget.max_wire_attempts = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: StrategyLimit(cast(FailureStrategy, "wait"), 1),
        lambda: StrategyLimit(FailureStrategy.WAIT, -1),
        lambda: StrategyLimit(FailureStrategy.WAIT, True),
        lambda: RetryBudget(max_wire_attempts=0),
        lambda: RetryBudget(max_wire_attempts=True),
        lambda: RetryBudget(hard_max_wire_attempts=0),
        lambda: RetryBudget(hard_max_wire_attempts=True),
        lambda: RetryBudget(max_wire_attempts=5, hard_max_wire_attempts=4),
        lambda: RetryBudget(default_max_strategy_uses=-1),
        lambda: RetryBudget(default_max_strategy_uses=True),
        lambda: RetryBudget(strategy_limits=cast(tuple[StrategyLimit, ...], (object(),))),
        lambda: RetryBudget(
            strategy_limits=(StrategyLimit(FailureStrategy.WAIT, 1), StrategyLimit(FailureStrategy.WAIT, 2))
        ),
    ),
)
def test_strategy_budget_rejects_invalid_values(factory: InvalidFactory) -> None:
    with pytest.raises(FailoverContractError):
        factory()


def test_retry_timing_accepts_a_valid_window() -> None:
    timing = RetryTiming(
        base_backoff_seconds=0.5,
        max_backoff_seconds=4.0,
        attempt_timeout_seconds=10.0,
        total_deadline_seconds=30.0,
    )

    assert timing.base_backoff_seconds == 0.5
    assert timing.max_backoff_seconds == 4.0
    assert timing.attempt_timeout_seconds == 10.0
    assert timing.total_deadline_seconds == 30.0


@pytest.mark.parametrize(
    "kwargs",
    (
        {"base_backoff_seconds": True},
        {"max_backoff_seconds": float("nan")},
        {"attempt_timeout_seconds": -1.0},
        {"total_deadline_seconds": float("inf")},
        {"base_backoff_seconds": 5.0, "max_backoff_seconds": 4.0},
        {"attempt_timeout_seconds": 0.0},
        {"total_deadline_seconds": 0.0},
        {"attempt_timeout_seconds": 31.0, "total_deadline_seconds": 30.0},
    ),
)
def test_retry_timing_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(FailoverContractError):
        RetryTiming(**kwargs)  # type: ignore[arg-type]


def test_profile_and_override_merge_matching_pairs_without_losing_defaults() -> None:
    rate_limit = _rule(429, "rate_limited", FailureStrategy.WAIT, preparation=Wait(1.0))
    auth = _rule(401, "token_expired", FailureStrategy.REFRESH_CREDENTIAL)
    profile = _profile(rate_limit, auth)
    override_rule = _rule(429, "rate_limited", FailureStrategy.WAIT, preparation=Wait(2.0))
    extra_rule = _rule(400, "bad_payload", FailureStrategy.TRANSFORM_REQUEST, preparation=TransformRequest("shrink"))
    override = FailoverProfileOverride(
        policy_id=FailoverPolicyId("payment-policy"),
        rules=(override_rule, extra_rule),
        budget=RetryBudget(max_wire_attempts=2, hard_max_wire_attempts=5),
        timing=RetryTiming(total_deadline_seconds=60.0),
    )

    merged = merge_profile(profile, override)
    assert merged.profile_id == profile.profile_id
    assert merged.policy_id == "payment-policy"
    assert merged.rules == (override_rule, auth, extra_rule)
    assert merged.budget.max_wire_attempts == 2
    assert merged.timing.total_deadline_seconds == 60.0
    assert merged.semantics is profile.semantics
    assert merged.reconcile is profile.reconcile
    assert profile.rules == (rate_limit, auth)
    with pytest.raises(FrozenInstanceError):
        profile.policy_id = FailoverPolicyId("other")  # type: ignore[misc]


def test_profile_merge_rejects_a_non_profile_default() -> None:
    with pytest.raises(FailoverContractError, match="requires a FailoverProfile"):
        merge_profile(cast(FailoverProfile[str], object()), None)


@pytest.mark.parametrize(
    "factory",
    (
        lambda: FailoverProfile[str](FailoverProfileId(" bad"), FailoverPolicyId("policy")),
        lambda: FailoverProfile[str](FailoverProfileId("profile"), FailoverPolicyId(" policy")),
        lambda: FailoverProfile[str](
            FailoverProfileId("profile"),
            FailoverPolicyId("policy"),
            cast(tuple[FailureRule[str], ...], (object(),)),
        ),
        lambda: FailoverProfile[str](FailoverProfileId("profile"), FailoverPolicyId("policy"), (_rule(), _rule())),
        lambda: FailoverProfile[str](
            FailoverProfileId("profile"),
            FailoverPolicyId("policy"),
            budget=cast(RetryBudget, object()),
        ),
        lambda: FailoverProfile[str](
            FailoverProfileId("profile"),
            FailoverPolicyId("policy"),
            timing=cast(RetryTiming, object()),
        ),
        lambda: FailoverProfile[str](
            FailoverProfileId("profile"),
            FailoverPolicyId("policy"),
            semantics=cast(OperationSemantics, "pure"),
        ),
        lambda: FailoverProfile[str](
            FailoverProfileId("profile"),
            FailoverPolicyId("policy"),
            semantics=OperationSemantics.PURE,
            reconcile=cast(ReconcileMode, "optional"),
        ),
    ),
)
def test_profile_rejects_invalid_values(factory: InvalidFactory) -> None:
    with pytest.raises(FailoverContractError):
        factory()


@pytest.mark.parametrize(
    "factory",
    (
        lambda: FailoverProfileOverride[str](policy_id=FailoverPolicyId(" bad")),
        lambda: FailoverProfileOverride[str](rules=cast(tuple[FailureRule[str], ...], (object(),))),
        lambda: FailoverProfileOverride[str](rules=(_rule(), _rule())),
        lambda: FailoverProfileOverride[str](budget=cast(RetryBudget, object())),
        lambda: FailoverProfileOverride[str](timing=cast(RetryTiming, object())),
        lambda: FailoverProfileOverride[str](semantics=cast(OperationSemantics, "pure")),
        lambda: FailoverProfileOverride[str](reconcile=cast(ReconcileMode, "required")),
    ),
)
def test_profile_override_rejects_invalid_values(factory: InvalidFactory) -> None:
    with pytest.raises(FailoverContractError):
        factory()


def test_binding_resolution_models_inherit_override_and_disabled() -> None:
    profile = _profile(_rule())
    snapshot = FailoverConfigSnapshot(FailoverConfigRevision(7), profile)
    override = FailoverProfileOverride(
        rules=(_rule(400, "bad_payload", FailureStrategy.TRANSFORM_REQUEST, preparation=TransformRequest("fix")),)
    )
    inherit = PortBinding[str](FailoverBindingMode.INHERIT)
    replacement = PortBinding[str](FailoverBindingMode.OVERRIDE, override)
    disabled = PortBinding[str](FailoverBindingMode.DISABLED)

    inherited_plan = resolve_plan(snapshot, FailoverPortId("payment"), inherit)
    replacement_plan = resolve_plan(snapshot, FailoverPortId("approval"), replacement)
    assert inherited_plan is not None
    assert inherited_plan.profile is profile
    assert replacement_plan is not None
    assert override.rules is not None
    assert replacement_plan.profile.rules == (*profile.rules, *override.rules)
    assert resolve_plan(snapshot, FailoverPortId("disabled"), disabled) is None


@pytest.mark.parametrize(
    "factory",
    (
        lambda: PortBinding[str](cast(FailoverBindingMode, "inherit")),
        lambda: PortBinding[str](FailoverBindingMode.OVERRIDE, None),
        lambda: PortBinding[str](
            FailoverBindingMode.OVERRIDE,
            cast(FailoverProfileOverride[str], object()),
        ),
        lambda: PortBinding[str](FailoverBindingMode.INHERIT, FailoverProfileOverride[str]()),
        lambda: PortBinding[str](FailoverBindingMode.DISABLED, FailoverProfileOverride[str]()),
    ),
)
def test_port_binding_rejects_invalid_modes_and_payloads(factory: InvalidFactory) -> None:
    with pytest.raises(FailoverContractError):
        factory()


def test_snapshot_plan_and_initial_context_capture_one_revision() -> None:
    profile = _profile(_rule())
    snapshot = FailoverConfigSnapshot(FailoverConfigRevision(7), profile)
    plan = FailoverPlan(FailoverConfigRevision(7), FailoverPortId("payment"), profile)
    context = RetryContext[object, object](
        FailoverOperationId("operation-1"),
        FailoverConfigRevision(7),
        profile.budget,
    )

    assert snapshot.revision == 7
    assert snapshot.default_profile is profile
    assert plan.plan_revision == 7
    assert plan.port_id == "payment"
    assert plan.profile is profile
    assert context.plan_revision == 7
    assert context.budget is profile.budget
    assert context.request_version == 1
    assert context.attempt_ordinal == 0


@pytest.mark.parametrize(
    "factory",
    (
        lambda: FailoverConfigSnapshot(FailoverConfigRevision(0), _profile()),
        lambda: FailoverConfigSnapshot[str](FailoverConfigRevision(1), cast(FailoverProfile[str], object())),
        lambda: FailoverPlan(FailoverConfigRevision(0), FailoverPortId("port"), _profile()),
        lambda: FailoverPlan(FailoverConfigRevision(1), FailoverPortId(" port"), _profile()),
        lambda: FailoverPlan[str](
            FailoverConfigRevision(1), FailoverPortId("port"), cast(FailoverProfile[str], object())
        ),
        lambda: resolve_plan(
            cast(FailoverConfigSnapshot[str], object()),
            FailoverPortId("port"),
            PortBinding[str](FailoverBindingMode.INHERIT),
        ),
        lambda: resolve_plan(
            FailoverConfigSnapshot(FailoverConfigRevision(1), _profile()),
            FailoverPortId(" port"),
            PortBinding[str](FailoverBindingMode.INHERIT),
        ),
        lambda: resolve_plan(
            FailoverConfigSnapshot(FailoverConfigRevision(1), _profile()),
            FailoverPortId("port"),
            cast(PortBinding[str], object()),
        ),
    ),
)
def test_snapshot_plan_and_resolution_reject_invalid_values(factory: InvalidFactory) -> None:
    with pytest.raises(FailoverContractError):
        factory()


def test_retry_context_carries_strategy_usage_and_external_handles() -> None:
    wait_until = datetime(2030, 1, 1, tzinfo=UTC)
    context = RetryContext[str, str](
        FailoverOperationId("operation-2"),
        FailoverConfigRevision(3),
        RetryBudget(),
        request_version=2,
        attempt_ordinal=1,
        endpoint_cursor=2,
        credential_cursor=1,
        strategy_usages=(
            StrategyUsage(FailureStrategy.WAIT, 2),
            StrategyUsage(FailureStrategy.SWITCH_ENDPOINT, 1),
        ),
        wait_until=wait_until,
        receipt="receipt-1",
        reconcile_handle="handle-1",
    )

    assert context.uses_for(FailureStrategy.WAIT) == 2
    assert context.uses_for(FailureStrategy.ROTATE_CREDENTIAL) == 0
    updated = context.with_strategy_use(FailureStrategy.WAIT)
    assert updated.uses_for(FailureStrategy.WAIT) == 3
    assert updated.last_strategy is FailureStrategy.WAIT
    assert context.uses_for(FailureStrategy.WAIT) == 2
    assert context.wait_until == wait_until
    assert context.receipt == "receipt-1"
    assert context.reconcile_handle == "handle-1"

    with pytest.raises(FailoverContractError, match="strategy lookup"):
        context.uses_for(cast(FailureStrategy, "wait"))
    with pytest.raises(FailoverContractError, match="strategy update"):
        context.with_strategy_use(cast(FailureStrategy, "wait"))


@pytest.mark.parametrize(
    "factory",
    (
        lambda: RetryContext[object, object](
            FailoverOperationId(" operation"), FailoverConfigRevision(1), RetryBudget()
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"), FailoverConfigRevision(0), RetryBudget()
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"),
            FailoverConfigRevision(1),
            cast(RetryBudget, object()),
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"), FailoverConfigRevision(1), RetryBudget(), request_version=-1
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"), FailoverConfigRevision(1), RetryBudget(), request_version=0
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"), FailoverConfigRevision(1), RetryBudget(), attempt_ordinal=-1
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"), FailoverConfigRevision(1), RetryBudget(), endpoint_cursor=-1
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"), FailoverConfigRevision(1), RetryBudget(), credential_cursor=-1
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"),
            FailoverConfigRevision(1),
            RetryBudget(),
            strategy_usages=cast(tuple[StrategyUsage, ...], (object(),)),
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"),
            FailoverConfigRevision(1),
            RetryBudget(),
            strategy_usages=(StrategyUsage(FailureStrategy.WAIT, 1), StrategyUsage(FailureStrategy.WAIT, 2)),
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"),
            FailoverConfigRevision(1),
            RetryBudget(),
            last_failure=cast(FailureClass, "rate_limited"),
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"),
            FailoverConfigRevision(1),
            RetryBudget(),
            last_signal=cast(FailureSignal, object()),
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"),
            FailoverConfigRevision(1),
            RetryBudget(),
            last_strategy=cast(FailureStrategy, "wait"),
        ),
        lambda: RetryContext[object, object](
            FailoverOperationId("operation"),
            FailoverConfigRevision(1),
            RetryBudget(),
            wait_until=datetime(2030, 1, 1),
        ),
        lambda: StrategyUsage(cast(FailureStrategy, "wait"), 1),
        lambda: StrategyUsage(FailureStrategy.WAIT, -1),
    ),
)
def test_retry_context_rejects_invalid_values(factory: InvalidFactory) -> None:
    with pytest.raises(FailoverContractError):
        factory()


class _Source:
    def __init__(self, snapshot: FailoverConfigSnapshot[str]) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> FailoverConfigSnapshot[str]:
        return self._snapshot


def test_config_source_protocol_is_snapshot_only() -> None:
    snapshot = FailoverConfigSnapshot(FailoverConfigRevision(1), _profile())
    source: FailoverConfigSource[str] = _Source(snapshot)

    assert source.snapshot() is snapshot
