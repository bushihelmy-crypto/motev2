"""Graph execution resource and recursion limits."""

from dataclasses import dataclass

from mote_kernel.execution.errors import ExecutionLimitError


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    """Hard execution bounds; retry policy deliberately does not live here."""

    max_supersteps: int = 1_000
    max_parallel_tasks: int = 64

    def __post_init__(self) -> None:
        if (
            type(self.max_supersteps) is not int
            or type(self.max_parallel_tasks) is not int
            or self.max_supersteps < 1
            or self.max_parallel_tasks < 1
        ):
            raise ExecutionLimitError("execution limits must be exact positive integers")


__all__ = ["ExecutionLimits"]
