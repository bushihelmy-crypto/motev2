"""Execution-owned request correlation identities."""

from typing import NewType

ExecutionRequestAttemptId = NewType("ExecutionRequestAttemptId", str)

__all__ = ["ExecutionRequestAttemptId"]
