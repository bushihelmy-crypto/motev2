"""Closed graph identities owned by the Failover package."""

from enum import StrEnum


class FailoverNodeId(StrEnum):
    """The closed node identities owned by one Failover graph."""

    OBSERVE = "observe"
    INVOKE = "invoke"
    PREPARE = "prepare"


class FailoverRoute(StrEnum):
    """The closed control routes emitted by one Failover graph."""

    PREPARE = "prepare"
    FINISH = "finish"


class FailoverValueName(StrEnum):
    """The closed value-port names owned by one Failover graph."""

    REQUEST = "request"
    FRAME = "frame"
    RESULT = "result"


__all__ = ["FailoverNodeId", "FailoverRoute", "FailoverValueName"]
