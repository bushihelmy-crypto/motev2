"""Backend-neutral severity values for structured diagnostic records."""

from enum import StrEnum


class LogLevel(StrEnum):
    """The finite severity vocabulary understood by the Kernel."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


__all__ = ["LogLevel"]
