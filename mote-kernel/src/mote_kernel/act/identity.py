"""Stable Act identities and the shared Hook graph carrier.

The Act package deliberately keeps identity values small and opaque.  The
provider or protocol owner gives these values meaning; this module only
enforces the shape needed by the Graph value boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mote_kernel.hooks.contract import HookGraphValue

_IDENTITY_MAX_BYTES = 256
_PROTECTED_REF_MAX_BYTES = 4_096
_ARGUMENTS_MAX_BYTES = 65_536
_DIGEST_MAX_BYTES = 128
_OUTCOME_MAX_BYTES = 1_048_576
_PROJECTION_MAX_BYTES = 1_048_576
_RECEIPT_MAX_BYTES = 1_048_576
_FAILURE_REASON_MAX_BYTES = 512


class ActIdentityError(ValueError):
    """Raised when an Act identity or opaque wrapper has an invalid shape."""


def _require_bounded_text(value: str, field: str, maximum: int, /) -> None:
    if type(value) is not str or not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ActIdentityError(f"{field} must be a non-empty trimmed single-line string")
    try:
        length = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ActIdentityError(f"{field} must be valid UTF-8") from error
    if length > maximum:
        raise ActIdentityError(f"{field} exceeds its {maximum}-byte limit")


def _require_bytes(value: bytes, field: str, maximum: int, /, *, non_empty: bool = False) -> None:
    if type(value) is not bytes:
        raise ActIdentityError(f"{field} must be bytes")
    if non_empty and not value:
        raise ActIdentityError(f"{field} must not be empty")
    if len(value) > maximum:
        raise ActIdentityError(f"{field} exceeds its {maximum}-byte limit")


@dataclass(frozen=True, slots=True)
class ToolCallId(HookGraphValue):
    value: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.value, "tool call id", _IDENTITY_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class ToolExchangeScopeId(HookGraphValue):
    value: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.value, "tool exchange scope id", _IDENTITY_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class ActInvocationKey(HookGraphValue):
    value: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.value, "Act invocation key", _IDENTITY_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class ToolSelector(HookGraphValue):
    value: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.value, "tool selector", _IDENTITY_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class ToolBindingRef(HookGraphValue):
    value: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.value, "tool binding reference", _IDENTITY_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class OpaqueDefinitionReference(HookGraphValue):
    value: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.value, "definition reference", _IDENTITY_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class CallerIdentityRef(HookGraphValue):
    value: bytes

    def __post_init__(self) -> None:
        _require_bytes(self.value, "caller identity reference", _PROTECTED_REF_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class OpaqueAuthorizationHandle(HookGraphValue):
    value: bytes

    def __post_init__(self) -> None:
        _require_bytes(self.value, "authorization handle", _PROTECTED_REF_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class OpaqueArguments(HookGraphValue):
    value: bytes

    def __post_init__(self) -> None:
        _require_bytes(self.value, "arguments", _ARGUMENTS_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class ArgumentsDigest(HookGraphValue):
    value: bytes

    def __post_init__(self) -> None:
        _require_bytes(self.value, "arguments digest", _DIGEST_MAX_BYTES, non_empty=True)


@dataclass(frozen=True, slots=True)
class OpaqueExecutionOutcome(HookGraphValue):
    value: bytes

    def __post_init__(self) -> None:
        _require_bytes(self.value, "execution outcome", _OUTCOME_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class OpaqueProtocolPayload(HookGraphValue):
    value: bytes

    def __post_init__(self) -> None:
        _require_bytes(self.value, "protocol payload", _PROJECTION_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class OpaqueToolExchangeReceipt(HookGraphValue):
    value: bytes

    def __post_init__(self) -> None:
        _require_bytes(self.value, "tool exchange receipt", _RECEIPT_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class OpaqueGraphFailureReason(HookGraphValue):
    value: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.value, "Graph failure reason", _FAILURE_REASON_MAX_BYTES)


@dataclass(frozen=True, slots=True)
class ActSlotId(HookGraphValue):
    """Identity of one of Act's four business slots.

    This is not the parent graph's mount point and not the shared Hook slot.
    """

    definition_id: str
    definition_version: int
    node_id: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.definition_id, "Act definition id", _IDENTITY_MAX_BYTES)
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ActIdentityError("Act definition version must be an exact positive integer")
        _require_bounded_text(self.node_id, "Act slot node id", _IDENTITY_MAX_BYTES)
        if self.node_id not in ("resolve", "authorize", "execute", "settle"):
            raise ActIdentityError("Act slot node id must be resolve, authorize, execute, or settle")


@dataclass(frozen=True, slots=True)
class ToolPairingIdentity(HookGraphValue):
    scope: ToolExchangeScopeId
    invocation: ActInvocationKey
    tool_call_id: ToolCallId

    def __post_init__(self) -> None:
        if type(self.scope) is not ToolExchangeScopeId:
            raise ActIdentityError("pairing scope must be a ToolExchangeScopeId")
        if type(self.invocation) is not ActInvocationKey:
            raise ActIdentityError("pairing invocation must be an ActInvocationKey")
        if type(self.tool_call_id) is not ToolCallId:
            raise ActIdentityError("pairing tool_call_id must be a ToolCallId")


@dataclass(frozen=True, slots=True)
class ToolExecutionIdentity(HookGraphValue):
    pairing: ToolPairingIdentity
    binding: ToolBindingRef
    arguments_digest: ArgumentsDigest

    def __post_init__(self) -> None:
        if type(self.pairing) is not ToolPairingIdentity:
            raise ActIdentityError("execution identity pairing must be a ToolPairingIdentity")
        if type(self.binding) is not ToolBindingRef:
            raise ActIdentityError("execution identity binding must be a ToolBindingRef")
        if type(self.arguments_digest) is not ArgumentsDigest:
            raise ActIdentityError("execution identity digest must be an ArgumentsDigest")


class ActHookStage(StrEnum):
    """The stage whose completed value is entering the shared Hook."""

    RESOLVE = "resolve"
    AUTHORIZE = "authorize"
    EXECUTE = "execute"
    SETTLE = "settle"


__all__ = [
    "ActHookStage",
    "ActIdentityError",
    "ActInvocationKey",
    "ActSlotId",
    "ArgumentsDigest",
    "CallerIdentityRef",
    "OpaqueArguments",
    "OpaqueAuthorizationHandle",
    "OpaqueDefinitionReference",
    "OpaqueExecutionOutcome",
    "OpaqueGraphFailureReason",
    "OpaqueProtocolPayload",
    "OpaqueToolExchangeReceipt",
    "ToolBindingRef",
    "ToolCallId",
    "ToolExchangeScopeId",
    "ToolExecutionIdentity",
    "ToolPairingIdentity",
    "ToolSelector",
]
