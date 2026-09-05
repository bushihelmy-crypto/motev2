"""Deterministic tests for the first failover contract boundary."""

from dataclasses import FrozenInstanceError

import pytest

from mote_kernel.failover.contract import (
    Completed,
    ErrorHint,
    FailoverContractError,
    FailureClass,
    FailureEvidence,
    FailureSignal,
    InProgress,
    RefreshCredential,
    Rejected,
    RotateCredential,
    SingleAttempt,
    SwitchEndpoint,
    TransformRequest,
    Unknown,
    Wait,
)


def test_failure_evidence_preserves_safe_provider_facts() -> None:
    evidence = FailureEvidence(
        category=FailureClass.RATE_LIMITED,
        status_code=429,
        error_hint=ErrorHint("quota_exceeded"),
        provider_code="quota",
        message="quota exceeded",
        retry_after_seconds=1.5,
    )

    assert evidence.category is FailureClass.RATE_LIMITED
    assert evidence.status_code == 429
    assert evidence.error_hint == "quota_exceeded"
    assert evidence.signal == FailureSignal(429, ErrorHint("quota_exceeded"))
    assert evidence.provider_code == "quota"
    assert evidence.message == "quota exceeded"
    assert evidence.retry_after_seconds == 1.5
    with pytest.raises(FrozenInstanceError):
        evidence.category = FailureClass.AUTH_REJECTED  # type: ignore[misc]


def test_failure_classes_separate_timeout_from_service_unavailability() -> None:
    assert FailureClass.REQUEST_TIMEOUT.value == "request_timeout"
    assert FailureClass.NO_RESPONSE.value == "no_response"
    assert FailureClass.SERVICE_UNAVAILABLE.value == "service_unavailable"
    assert FailureClass.REQUEST_TIMEOUT is not FailureClass.SERVICE_UNAVAILABLE


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("category", "rate_limited"),
        ("status_code", True),
        ("status_code", 99),
        ("status_code", 600),
        ("error_hint", ""),
        ("error_hint", " hint"),
        ("error_hint", "x" * 129),
        ("provider_code", ""),
        ("provider_code", " quota"),
        ("provider_code", "quota\ncode"),
        ("provider_code", "x" * 257),
        ("retry_after_seconds", True),
        ("retry_after_seconds", float("inf")),
        ("retry_after_seconds", -1.0),
        ("message", ""),
        ("message", " message"),
        ("message", "x" * 1025),
    ),
)
def test_failure_evidence_rejects_malformed_fields(field: str, value: object) -> None:
    values: dict[str, object] = {"category": FailureClass.RATE_LIMITED}
    values[field] = value
    with pytest.raises(FailoverContractError):
        FailureEvidence(**values)  # type: ignore[arg-type]


def test_port_outcomes_are_explicit_and_immutable() -> None:
    evidence = FailureEvidence(FailureClass.INVALID_REQUEST, status_code=400)
    completed = Completed("response")
    rejected = Rejected(evidence)
    in_progress = InProgress("receipt-1")
    unknown = Unknown(
        "provider-context",
        FailureEvidence(FailureClass.NO_RESPONSE, error_hint=ErrorHint("no_response")),
    )

    assert completed.response == "response"
    assert rejected.evidence is evidence
    assert in_progress.receipt == "receipt-1"
    assert unknown.handle == "provider-context"
    assert unknown.evidence.category is FailureClass.NO_RESPONSE
    assert type(completed).__name__ == "Completed"
    assert type(rejected).__name__ == "Rejected"
    assert type(in_progress).__name__ == "InProgress"
    assert type(unknown).__name__ == "Unknown"
    with pytest.raises(FailoverContractError):
        Rejected(object())  # type: ignore[arg-type]
    with pytest.raises(FailoverContractError):
        Unknown(None, object())  # type: ignore[arg-type]
    with pytest.raises(FrozenInstanceError):
        completed.response = "other"  # type: ignore[misc]


def test_preparation_actions_cover_the_fixed_action_set() -> None:
    actions = (
        Wait(0.25),
        TransformRequest("shrink-request"),
        RefreshCredential(),
        RotateCredential(),
        SwitchEndpoint(),
    )

    assert actions[0].delay_seconds == 0.25
    assert actions[1].instruction == "shrink-request"
    assert tuple(type(action).__name__ for action in actions) == (
        "Wait",
        "TransformRequest",
        "RefreshCredential",
        "RotateCredential",
        "SwitchEndpoint",
    )


@pytest.mark.parametrize("delay", (True, float("nan"), float("inf"), -0.1))
def test_wait_rejects_invalid_delays(delay: object) -> None:
    with pytest.raises(FailoverContractError):
        Wait(delay)  # type: ignore[arg-type]


class _Attempt:
    async def invoke_once(self, request: str) -> Completed[str]:
        return Completed(request)


def test_single_attempt_protocol_describes_one_narrow_port_call() -> None:
    attempt: SingleAttempt[str, Completed[str]] = _Attempt()

    assert hasattr(attempt, "invoke_once")
