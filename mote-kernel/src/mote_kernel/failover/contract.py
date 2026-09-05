"""Typed values crossing the failover graph boundary.

The concrete Port adapter owns the messy provider details.  Before a value
enters the graph it must turn those details into one of the immutable values
defined here.  In particular, a strategy is selected from the pair
``(status_code, error_hint)``; the graph never parses an exception or a raw
provider message.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, NewType, Protocol, TypeAlias, TypeVar, runtime_checkable


class FailoverContractError(ValueError):
    """Raised when a value does not satisfy a failover contract."""


class FailureClass(StrEnum):
    """Coarse facts used for accounting and diagnostics.

    ``REQUEST_TIMEOUT`` means that the adapter received a timeout response
    with a status code.  ``NO_RESPONSE`` means that no response was received
    at all.  Neither value, by itself, chooses a retry strategy; the policy
    also considers the normalized ``error_hint``.
    """

    RATE_LIMITED = "rate_limited"
    AUTH_REJECTED = "auth_rejected"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INVALID_REQUEST = "invalid_request"
    REQUEST_TIMEOUT = "request_timeout"
    NO_RESPONSE = "no_response"
    UNKNOWN_OUTCOME = "unknown_outcome"
    POLICY_DENIED = "policy_denied"


class FailureStrategy(StrEnum):
    """The fixed action selected for one status-code/error-hint pair."""

    WAIT = "wait"
    TRANSFORM_REQUEST = "transform_request"
    REFRESH_CREDENTIAL = "refresh_credential"
    ROTATE_CREDENTIAL = "rotate_credential"
    SWITCH_ENDPOINT = "switch_endpoint"
    RETURN_TO_MODEL = "return_to_model"
    ABORT = "abort"


ErrorHint = NewType("ErrorHint", str)


def _validate_error_hint(value: str | None) -> None:
    if value is None:
        return
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or len(value) > 128
    ):
        raise FailoverContractError("error_hint must be a safe, short, trimmed value")


@dataclass(frozen=True, slots=True)
class FailureSignal:
    """The exact pair used to select a fixed failure strategy.

    ``None, None`` is the explicit no-response signal.  A status-only or
    hint-only signal is allowed for adapters that genuinely have only one
    piece of evidence, but it does not accidentally match a rule requiring a
    different pair.
    """

    status_code: int | None = None
    error_hint: ErrorHint | None = None

    def __post_init__(self) -> None:
        if self.status_code is not None and (type(self.status_code) is not int or not 100 <= self.status_code <= 599):
            raise FailoverContractError("failure signal status_code must be an HTTP status code")
        _validate_error_hint(self.error_hint)


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    """Provider-neutral evidence for a definitive rejection.

    ``error_hint`` is a normalized stable token produced by the adapter, for
    example ``"token_expired"`` or ``"quota_exceeded"``.  ``message`` and
    ``provider_code`` are bounded diagnostic fields; policy matching never
    uses their free-form text.
    """

    category: FailureClass = FailureClass.UNKNOWN_OUTCOME
    status_code: int | None = None
    error_hint: ErrorHint | None = None
    provider_code: str | None = None
    message: str | None = None
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if type(self.category) is not FailureClass:
            raise FailoverContractError("failure evidence category must be a FailureClass")
        FailureSignal(self.status_code, self.error_hint)
        if self.provider_code is not None and (
            type(self.provider_code) is not str
            or not self.provider_code
            or self.provider_code != self.provider_code.strip()
            or "\n" in self.provider_code
            or "\r" in self.provider_code
            or len(self.provider_code) > 256
        ):
            raise FailoverContractError("failure evidence provider_code must be a safe short value")
        if self.message is not None and (
            type(self.message) is not str
            or not self.message
            or self.message != self.message.strip()
            or "\n" in self.message
            or "\r" in self.message
            or len(self.message) > 1024
        ):
            raise FailoverContractError("failure evidence message must be a safe bounded value")
        if self.retry_after_seconds is not None and (
            type(self.retry_after_seconds) not in (int, float)
            or isinstance(self.retry_after_seconds, bool)
            or not math.isfinite(self.retry_after_seconds)
            or self.retry_after_seconds < 0
        ):
            raise FailoverContractError("failure evidence retry_after_seconds must be finite and non-negative")

    @property
    def signal(self) -> FailureSignal:
        """Return the exact status-code/error-hint policy key."""

        return FailureSignal(self.status_code, self.error_hint)


ResultT_co = TypeVar("ResultT_co", covariant=True)
ReceiptT_co = TypeVar("ReceiptT_co", covariant=True)
UnknownDetailT_co = TypeVar("UnknownDetailT_co", covariant=True)


class _PortOutcome:
    """Nominal base preventing unrelated values from becoming outcomes."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Completed(_PortOutcome, Generic[ResultT_co]):
    """The Port completed the logical operation and returned its result."""

    response: ResultT_co


@dataclass(frozen=True, slots=True)
class Rejected(_PortOutcome):
    """The Port definitively rejected the operation before accepting it."""

    evidence: FailureEvidence

    def __post_init__(self) -> None:
        if type(self.evidence) is not FailureEvidence:
            raise FailoverContractError("rejected outcome evidence must be FailureEvidence")


@dataclass(frozen=True, slots=True)
class InProgress(_PortOutcome, Generic[ReceiptT_co]):
    """The Port reports that the logical operation is still in progress.

    Failover returns this typed content to its caller.  It never schedules a
    provider poll; a version-aware Port owns any status lookup on a later call.
    """

    receipt: ReceiptT_co


@dataclass(frozen=True, slots=True)
class Unknown(_PortOutcome, Generic[UnknownDetailT_co]):
    """The Port cannot determine the logical operation's current result.

    ``handle`` is opaque Port-owned context returned to the caller.  Failover
    neither interprets it nor uses it to query the provider.
    """

    handle: UnknownDetailT_co | None
    evidence: FailureEvidence

    def __post_init__(self) -> None:
        if type(self.evidence) is not FailureEvidence:
            raise FailoverContractError("unknown outcome evidence must be FailureEvidence")


PortOutcome: TypeAlias = Completed[ResultT_co] | Rejected | InProgress[ReceiptT_co] | Unknown[UnknownDetailT_co]


ChangeT = TypeVar("ChangeT")


class _PreparationAction:
    """Nominal base for one execution-before-next-attempt action."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Wait(_PreparationAction):
    """Wait before the next graph activation."""

    delay_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.delay_seconds) not in (int, float)
            or isinstance(self.delay_seconds, bool)
            or not math.isfinite(self.delay_seconds)
            or self.delay_seconds < 0
        ):
            raise FailoverContractError("wait delay_seconds must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class TransformRequest(_PreparationAction, Generic[ChangeT]):
    """Carry a config-selected typed request transformation instruction.

    The value is declarative: the profile selects it and the preparation
    capability applies it.  Neither the failover policy nor the Port chooses
    a transformation while the graph is running.
    """

    instruction: ChangeT


@dataclass(frozen=True, slots=True)
class RefreshCredential(_PreparationAction):
    """Refresh the credential selected for the next invocation."""


@dataclass(frozen=True, slots=True)
class RotateCredential(_PreparationAction):
    """Move to another credential slot for the next invocation."""


@dataclass(frozen=True, slots=True)
class SwitchEndpoint(_PreparationAction):
    """Move to another service endpoint for the next invocation."""


PreparationAction: TypeAlias = Wait | TransformRequest[ChangeT] | RefreshCredential | RotateCredential | SwitchEndpoint


PreparedRequestT_co = TypeVar("PreparedRequestT_co", covariant=True)


@dataclass(frozen=True, slots=True)
class PreparedRequest(Generic[PreparedRequestT_co]):
    """The request produced by one completed preparation action."""

    request: PreparedRequestT_co


RequestT_contra = TypeVar("RequestT_contra", contravariant=True)
AttemptResultT_co = TypeVar("AttemptResultT_co", covariant=True)


@runtime_checkable
class SingleAttempt(Protocol[RequestT_contra, AttemptResultT_co]):
    """A capability that performs at most one wire invocation per call."""

    async def invoke_once(self, request: RequestT_contra, /) -> AttemptResultT_co: ...


PreparedRequestT = TypeVar("PreparedRequestT")
PreparationT = TypeVar("PreparationT")


@runtime_checkable
class AttemptPreparation(Protocol[PreparedRequestT, PreparationT]):
    """Apply one policy-selected action before the next wire attempt.

    The action already contains the config-owned instruction.  This capability
    performs that instruction once and returns the resulting request; it never
    chooses a strategy or starts a retry loop.
    """

    async def prepare_next(
        self,
        request: PreparedRequestT,
        action: PreparationAction[PreparationT],
        /,
    ) -> PreparedRequest[PreparedRequestT]: ...


__all__ = [
    "AttemptPreparation",
    "Completed",
    "ErrorHint",
    "FailoverContractError",
    "FailureClass",
    "FailureEvidence",
    "FailureSignal",
    "FailureStrategy",
    "InProgress",
    "PortOutcome",
    "PreparationAction",
    "PreparedRequest",
    "RefreshCredential",
    "Rejected",
    "RotateCredential",
    "SingleAttempt",
    "SwitchEndpoint",
    "TransformRequest",
    "Unknown",
    "Wait",
]
