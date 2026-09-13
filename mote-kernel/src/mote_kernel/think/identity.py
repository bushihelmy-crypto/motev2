"""Closed graph identities owned by the Think package."""

from enum import StrEnum


class ThinkNodeId(StrEnum):
    """The closed node identities owned by the Think graph."""

    PROMPT = "prompt"
    HOOK = "hook"
    CONTEXT = "context"
    COMPACT = "compact"
    ROUTER = "router"
    INFERENCE = "inference"
    COMMAND = "command"


class ThinkValueName(StrEnum):
    """The closed value-port names owned by the Think graph."""

    REQUEST = "request"
    HOOK_REQUEST = "hook_request"
    HOOK_RESULT = "hook_result"
    RESULT = "result"


__all__ = ["ThinkNodeId", "ThinkValueName"]
