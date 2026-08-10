"""Graph execution resource and recursion limits."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    """Hard execution bounds; retry policy deliberately does not live here."""

    max_supersteps: int = 1_000
    max_parallel_tasks: int = 64


__all__ = ["ExecutionLimits"]
