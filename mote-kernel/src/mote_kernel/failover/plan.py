"""Immutable failover profiles, plans, bindings, and durable cursors.

The graph receives one :class:`FailoverPlan` at its entry Plan node.  The
plan is a value, not a live configuration subscription.  All counters that
can affect a later edge are carried by :class:`RetryContext`, so a restart
continues from the last committed graph state rather than from a decorator's
memory.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Generic, NewType, Protocol, TypeVar

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
from mote_kernel.state.graph_state.identity import is_canonical_identity

FailoverProfileId = NewType("FailoverProfileId", str)
FailoverPolicyId = NewType("FailoverPolicyId", str)
FailoverPortId = NewType("FailoverPortId", str)
FailoverOperationId = NewType("FailoverOperationId", str)
FailoverConfigRevision = NewType("FailoverConfigRevision", int)


class FailureRoute(StrEnum):
    """The fixed graph route represented by a strategy."""

    PREPARE = "prepare"
    RECONCILE = "reconcile"
    RETURN_TO_MODEL = "return_to_model"
    ABORT = "abort"


class FailoverBindingMode(StrEnum):
    """How a Port obtains its profile from Role/Flow assembly."""

    INHERIT = "inherit"
    OVERRIDE = "override"
    DISABLED = "disabled"


class OperationSemantics(StrEnum):
    """Safety declaration for repeating or reconciling one Port operation."""

    PURE = "pure"
    IDEMPOTENT = "idempotent"
    RECEIPT_BASED = "receipt_based"
    NON_REPEATABLE = "non_repeatable"


class ReconcileMode(StrEnum):
    """Whether the bound Port can or must reconcile an uncertain operation."""

    DISABLED = "disabled"
    OPTIONAL = "optional"
    REQUIRED = "required"


TransformT = TypeVar("TransformT")


def _validate_status_code(status_code: int | None, *, kind: str) -> None:
    if status_code is not None and (type(status_code) is not int or not 100 <= status_code <= 599):
        raise FailoverContractError(f"{kind} status_code must be an HTTP status code")


def _validate_hint(error_hint: ErrorHint | None, *, kind: str) -> None:
    if error_hint is not None and (
        type(error_hint) is not str
        or not error_hint
        or error_hint != error_hint.strip()
        or "\n" in error_hint
        or "\r" in error_hint
        or len(error_hint) > 128
    ):
        raise FailoverContractError(f"{kind} error_hint must be a safe, short, trimmed value")


@dataclass(frozen=True, slots=True)
class FailureRule(Generic[TransformT]):
    """One fixed ``(status_code, error_hint)`` to strategy mapping.

    Rules describe behavior, not mutable retry limits.  Limits live in
    :class:`RetryBudget` and are counted by strategy, so two different error
    signals that both mean ``WAIT`` consume the same wait budget.
    """

    status_code: int | None
    error_hint: ErrorHint | None
    strategy: FailureStrategy
    preparation: PreparationAction[TransformT] | None = None

    def __post_init__(self) -> None:
        _validate_status_code(self.status_code, kind="failure rule")
        _validate_hint(self.error_hint, kind="failure rule")
        if type(self.strategy) is not FailureStrategy:
            raise FailoverContractError("failure rule requires a FailureStrategy")
        if self.strategy is FailureStrategy.WAIT:
            if self.preparation is not None and type(self.preparation) is not Wait:
                raise FailoverContractError("wait strategy requires a Wait preparation")
        elif self.strategy is FailureStrategy.TRANSFORM_REQUEST:
            if type(self.preparation) is not TransformRequest:
                raise FailoverContractError("transform strategy requires a TransformRequest preparation")
        elif self.strategy is FailureStrategy.REFRESH_CREDENTIAL:
            if self.preparation is not None and type(self.preparation) is not RefreshCredential:
                raise FailoverContractError("refresh strategy requires a RefreshCredential preparation")
        elif self.strategy is FailureStrategy.ROTATE_CREDENTIAL:
            if self.preparation is not None and type(self.preparation) is not RotateCredential:
                raise FailoverContractError("rotate strategy requires a RotateCredential preparation")
        elif self.strategy is FailureStrategy.SWITCH_ENDPOINT:
            if self.preparation is not None and type(self.preparation) is not SwitchEndpoint:
                raise FailoverContractError("endpoint strategy requires a SwitchEndpoint preparation")
        elif self.preparation is not None:
            raise FailoverContractError("terminal and reconcile strategies cannot carry preparation")

    @property
    def signal(self) -> FailureSignal:
        """Return the exact pair used for lookup."""

        return FailureSignal(self.status_code, self.error_hint)

    @property
    def route(self) -> FailureRoute:
        """Project the strategy onto one of the fixed graph edges."""

        if self.strategy in (
            FailureStrategy.WAIT,
            FailureStrategy.TRANSFORM_REQUEST,
            FailureStrategy.REFRESH_CREDENTIAL,
            FailureStrategy.ROTATE_CREDENTIAL,
            FailureStrategy.SWITCH_ENDPOINT,
        ):
            return FailureRoute.PREPARE
        if self.strategy is FailureStrategy.RECONCILE:
            return FailureRoute.RECONCILE
        if self.strategy is FailureStrategy.RETURN_TO_MODEL:
            return FailureRoute.RETURN_TO_MODEL
        return FailureRoute.ABORT


@dataclass(frozen=True, slots=True)
class StrategyLimit:
    """An optional strategy-specific maximum use count."""

    strategy: FailureStrategy
    max_uses: int

    def __post_init__(self) -> None:
        if type(self.strategy) is not FailureStrategy:
            raise FailoverContractError("strategy limit requires a FailureStrategy")
        if type(self.max_uses) is not int or self.max_uses < 0:
            raise FailoverContractError("strategy limit max_uses must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class RetryBudget:
    """Per-operation limits with strategy-specific and unified fallbacks.

    ``strategy_limits`` wins first.  If a strategy has no entry, the
    ``default_max_strategy_uses`` value is used.  The built-in value is the
    final fallback when a config source omits both fields.  The wire-attempt
    ceiling is independent from preparation/reconcile strategy counts.
    """

    max_wire_attempts: int = 3
    hard_max_wire_attempts: int = 10
    default_max_strategy_uses: int = 1
    strategy_limits: tuple[StrategyLimit, ...] = ()

    def __post_init__(self) -> None:
        if type(self.max_wire_attempts) is not int or self.max_wire_attempts < 1:
            raise FailoverContractError("max_wire_attempts must be a positive integer")
        if type(self.hard_max_wire_attempts) is not int or self.hard_max_wire_attempts < 1:
            raise FailoverContractError("hard_max_wire_attempts must be a positive integer")
        if self.max_wire_attempts > self.hard_max_wire_attempts:
            raise FailoverContractError("max_wire_attempts cannot exceed hard_max_wire_attempts")
        if type(self.default_max_strategy_uses) is not int or self.default_max_strategy_uses < 0:
            raise FailoverContractError("default_max_strategy_uses must be a non-negative integer")
        if type(self.strategy_limits) is not tuple or any(
            type(limit) is not StrategyLimit for limit in self.strategy_limits
        ):
            raise FailoverContractError("strategy_limits must be a tuple of StrategyLimit values")
        strategies = tuple(limit.strategy for limit in self.strategy_limits)
        if len(strategies) != len(set(strategies)):
            raise FailoverContractError("strategy_limits cannot contain duplicate strategies")

    def max_uses_for(self, strategy: FailureStrategy) -> int:
        """Resolve a strategy limit using specific → unified → default order."""

        if type(strategy) is not FailureStrategy:
            raise FailoverContractError("strategy lookup requires a FailureStrategy")
        for limit in self.strategy_limits:
            if limit.strategy is strategy:
                return limit.max_uses
        return self.default_max_strategy_uses


@dataclass(frozen=True, slots=True)
class RetryTiming:
    """Timing parameters captured by a Plan node."""

    base_backoff_seconds: float = 0.0
    max_backoff_seconds: float = 60.0
    attempt_timeout_seconds: float = 30.0
    total_deadline_seconds: float = 300.0

    def __post_init__(self) -> None:
        values = (
            self.base_backoff_seconds,
            self.max_backoff_seconds,
            self.attempt_timeout_seconds,
            self.total_deadline_seconds,
        )
        if any(
            type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value) or value < 0
            for value in values
        ):
            raise FailoverContractError("retry timing values must be finite and non-negative")
        if self.max_backoff_seconds < self.base_backoff_seconds:
            raise FailoverContractError("max_backoff_seconds cannot be less than base_backoff_seconds")
        if self.attempt_timeout_seconds <= 0 or self.total_deadline_seconds <= 0:
            raise FailoverContractError("attempt timeout and total deadline must be positive")
        if self.attempt_timeout_seconds > self.total_deadline_seconds:
            raise FailoverContractError("attempt timeout cannot exceed total deadline")


@dataclass(frozen=True, slots=True)
class FailoverProfile(Generic[TransformT]):
    """Reusable Role-level strategy template.

    ``rules`` are the fixed policy table for this profile revision.  A normal
    hot reload changes only the parameter fields (budget/timing/instructions),
    while the graph topology and the status-code/error-hint behavior remain
    unchanged.
    """

    profile_id: FailoverProfileId
    policy_id: FailoverPolicyId
    rules: tuple[FailureRule[TransformT], ...] = ()
    budget: RetryBudget = RetryBudget()
    timing: RetryTiming = RetryTiming()
    semantics: OperationSemantics = OperationSemantics.NON_REPEATABLE
    reconcile: ReconcileMode = ReconcileMode.OPTIONAL

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.profile_id):
            raise FailoverContractError("failover profile_id must be canonical")
        if not is_canonical_identity(self.policy_id):
            raise FailoverContractError("failover policy_id must be canonical")
        if type(self.rules) is not tuple or any(type(rule) is not FailureRule for rule in self.rules):
            raise FailoverContractError("failover profile rules must be a tuple of FailureRule values")
        signals = tuple(rule.signal for rule in self.rules)
        if len(signals) != len(set(signals)):
            raise FailoverContractError("failover profile cannot define duplicate status/error-hint rules")
        if type(self.budget) is not RetryBudget:
            raise FailoverContractError("failover profile budget must be RetryBudget")
        if type(self.timing) is not RetryTiming:
            raise FailoverContractError("failover profile timing must be RetryTiming")
        if type(self.semantics) is not OperationSemantics:
            raise FailoverContractError("failover profile semantics must be OperationSemantics")
        if type(self.reconcile) is not ReconcileMode:
            raise FailoverContractError("failover profile reconcile must be ReconcileMode")


@dataclass(frozen=True, slots=True)
class FailoverProfileOverride(Generic[TransformT]):
    """Optional Port-level fields applied over the Role default profile."""

    policy_id: FailoverPolicyId | None = None
    rules: tuple[FailureRule[TransformT], ...] | None = None
    budget: RetryBudget | None = None
    timing: RetryTiming | None = None
    semantics: OperationSemantics | None = None
    reconcile: ReconcileMode | None = None

    def __post_init__(self) -> None:
        if self.policy_id is not None and not is_canonical_identity(self.policy_id):
            raise FailoverContractError("failover override policy_id must be canonical")
        if self.rules is not None and (
            type(self.rules) is not tuple or any(type(rule) is not FailureRule for rule in self.rules)
        ):
            raise FailoverContractError("failover override rules must be a tuple of FailureRule values")
        if self.rules is not None:
            signals = tuple(rule.signal for rule in self.rules)
            if len(signals) != len(set(signals)):
                raise FailoverContractError("failover override cannot define duplicate status/error-hint rules")
        if self.budget is not None and type(self.budget) is not RetryBudget:
            raise FailoverContractError("failover override budget must be RetryBudget")
        if self.timing is not None and type(self.timing) is not RetryTiming:
            raise FailoverContractError("failover override timing must be RetryTiming")
        if self.semantics is not None and type(self.semantics) is not OperationSemantics:
            raise FailoverContractError("failover override semantics must be OperationSemantics")
        if self.reconcile is not None and type(self.reconcile) is not ReconcileMode:
            raise FailoverContractError("failover override reconcile must be ReconcileMode")


@dataclass(frozen=True, slots=True)
class PortBinding(Generic[TransformT]):
    """Typed result of resolving a Port's inherit/override/disabled declaration."""

    mode: FailoverBindingMode
    override: FailoverProfileOverride[TransformT] | None = None

    def __post_init__(self) -> None:
        if type(self.mode) is not FailoverBindingMode:
            raise FailoverContractError("port binding mode must be FailoverBindingMode")
        if self.mode is FailoverBindingMode.OVERRIDE:
            if type(self.override) is not FailoverProfileOverride:
                raise FailoverContractError("override binding requires FailoverProfileOverride")
        elif self.override is not None:
            raise FailoverContractError("inherit and disabled bindings cannot carry an override")


@dataclass(frozen=True, slots=True)
class FailoverConfigSnapshot(Generic[TransformT]):
    """One immutable Role configuration revision read by the Plan node."""

    revision: FailoverConfigRevision
    default_profile: FailoverProfile[TransformT]

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 1:
            raise FailoverContractError("failover config revision must be a positive integer")
        if type(self.default_profile) is not FailoverProfile:
            raise FailoverContractError("failover config snapshot requires a FailoverProfile")


@dataclass(frozen=True, slots=True)
class FailoverPlan(Generic[TransformT]):
    """Effective immutable profile captured for one Port operation."""

    plan_revision: FailoverConfigRevision
    port_id: FailoverPortId
    profile: FailoverProfile[TransformT]

    def __post_init__(self) -> None:
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise FailoverContractError("failover plan revision must be a positive integer")
        if not is_canonical_identity(self.port_id):
            raise FailoverContractError("failover plan port_id must be canonical")
        if type(self.profile) is not FailoverProfile:
            raise FailoverContractError("failover plan requires a FailoverProfile")


def _merge_rules(
    base: tuple[FailureRule[TransformT], ...],
    overrides: tuple[FailureRule[TransformT], ...],
) -> tuple[FailureRule[TransformT], ...]:
    """Replace matching pairs while preserving deterministic rule order."""

    merged: list[FailureRule[TransformT]] = []
    remaining = list(overrides)
    for rule in base:
        replacement = next((candidate for candidate in remaining if candidate.signal == rule.signal), None)
        if replacement is None:
            merged.append(rule)
            continue
        merged.append(replacement)
        remaining.remove(replacement)
    return (*merged, *remaining)


def merge_profile(
    default: FailoverProfile[TransformT],
    override: FailoverProfileOverride[TransformT] | None,
) -> FailoverProfile[TransformT]:
    """Apply a Port override without changing unspecified Role values."""

    if type(default) is not FailoverProfile:
        raise FailoverContractError("profile merge requires a FailoverProfile")
    if override is None:
        return default
    return FailoverProfile(
        default.profile_id,
        override.policy_id if override.policy_id is not None else default.policy_id,
        _merge_rules(default.rules, override.rules or ()),
        override.budget if override.budget is not None else default.budget,
        override.timing if override.timing is not None else default.timing,
        override.semantics if override.semantics is not None else default.semantics,
        override.reconcile if override.reconcile is not None else default.reconcile,
    )


def resolve_plan(
    snapshot: FailoverConfigSnapshot[TransformT],
    port_id: FailoverPortId,
    binding: PortBinding[TransformT],
) -> FailoverPlan[TransformT] | None:
    """Resolve one binding at the Plan node's assembly boundary."""

    if type(snapshot) is not FailoverConfigSnapshot:
        raise FailoverContractError("plan resolution requires a FailoverConfigSnapshot")
    if not is_canonical_identity(port_id):
        raise FailoverContractError("plan resolution port_id must be canonical")
    if type(binding) is not PortBinding:
        raise FailoverContractError("plan resolution requires a PortBinding")
    if binding.mode is FailoverBindingMode.DISABLED:
        return None
    profile = merge_profile(snapshot.default_profile, binding.override)
    return FailoverPlan(snapshot.revision, port_id, profile)


ReceiptT = TypeVar("ReceiptT")
ReconcileHandleT = TypeVar("ReconcileHandleT")


@dataclass(frozen=True, slots=True)
class StrategyUsage:
    """Durable count for one strategy."""

    strategy: FailureStrategy
    used: int = 0

    def __post_init__(self) -> None:
        if type(self.strategy) is not FailureStrategy:
            raise FailoverContractError("strategy usage requires a FailureStrategy")
        if type(self.used) is not int or self.used < 0:
            raise FailoverContractError("strategy usage must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class RetryContext(Generic[ReceiptT, ReconcileHandleT]):
    """Durable cursor and per-strategy usage snapshot carried by the graph."""

    operation_id: FailoverOperationId
    plan_revision: FailoverConfigRevision
    budget: RetryBudget
    request_version: int = 1
    attempt_ordinal: int = 0
    endpoint_cursor: int = 0
    credential_cursor: int = 0
    strategy_usages: tuple[StrategyUsage, ...] = ()
    last_failure: FailureClass | None = None
    last_signal: FailureSignal | None = None
    last_strategy: FailureStrategy | None = None
    wait_until: datetime | None = None
    receipt: ReceiptT | None = None
    reconcile_handle: ReconcileHandleT | None = None

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.operation_id):
            raise FailoverContractError("retry context operation_id must be canonical")
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise FailoverContractError("retry context plan_revision must be a positive integer")
        if type(self.budget) is not RetryBudget:
            raise FailoverContractError("retry context budget must be RetryBudget")
        counters = (
            self.request_version,
            self.attempt_ordinal,
            self.endpoint_cursor,
            self.credential_cursor,
        )
        if any(type(counter) is not int or counter < 0 for counter in counters):
            raise FailoverContractError("retry context cursors must be non-negative integers")
        if self.request_version < 1:
            raise FailoverContractError("retry context request_version must be positive")
        if type(self.strategy_usages) is not tuple or any(
            type(usage) is not StrategyUsage for usage in self.strategy_usages
        ):
            raise FailoverContractError("retry context strategy_usages must be a tuple of StrategyUsage values")
        strategies = tuple(usage.strategy for usage in self.strategy_usages)
        if len(strategies) != len(set(strategies)):
            raise FailoverContractError("retry context strategy_usages cannot contain duplicates")
        if self.last_failure is not None and type(self.last_failure) is not FailureClass:
            raise FailoverContractError("retry context last_failure must be a FailureClass")
        if self.last_signal is not None and type(self.last_signal) is not FailureSignal:
            raise FailoverContractError("retry context last_signal must be a FailureSignal")
        if self.last_strategy is not None and type(self.last_strategy) is not FailureStrategy:
            raise FailoverContractError("retry context last_strategy must be a FailureStrategy")
        if self.wait_until is not None and self.wait_until.tzinfo is None:
            raise FailoverContractError("retry context wait_until must be timezone-aware")

    def uses_for(self, strategy: FailureStrategy) -> int:
        """Return the committed count for one strategy."""

        if type(strategy) is not FailureStrategy:
            raise FailoverContractError("strategy lookup requires a FailureStrategy")
        for usage in self.strategy_usages:
            if usage.strategy is strategy:
                return usage.used
        return 0

    def with_strategy_use(self, strategy: FailureStrategy) -> RetryContext[ReceiptT, ReconcileHandleT]:
        """Return a new context with one strategy use recorded."""

        if type(strategy) is not FailureStrategy:
            raise FailoverContractError("strategy update requires a FailureStrategy")
        current = self.uses_for(strategy)
        usages = tuple(usage for usage in self.strategy_usages if usage.strategy is not strategy)
        updated = (*usages, StrategyUsage(strategy, current + 1))
        return replace(self, strategy_usages=updated, last_strategy=strategy)


ConfigSourceT = TypeVar("ConfigSourceT")


class FailoverConfigSource(Protocol[ConfigSourceT]):
    """Read the current immutable configuration once at graph entry."""

    def snapshot(self) -> FailoverConfigSnapshot[ConfigSourceT]: ...


__all__ = [
    "FailoverBindingMode",
    "FailoverConfigRevision",
    "FailoverConfigSnapshot",
    "FailoverConfigSource",
    "FailoverOperationId",
    "FailoverPlan",
    "FailoverPolicyId",
    "FailoverPortId",
    "FailoverProfile",
    "FailoverProfileId",
    "FailoverProfileOverride",
    "FailureRoute",
    "FailureRule",
    "OperationSemantics",
    "PortBinding",
    "ReconcileMode",
    "RetryBudget",
    "RetryContext",
    "RetryTiming",
    "StrategyLimit",
    "StrategyUsage",
    "merge_profile",
    "resolve_plan",
]
