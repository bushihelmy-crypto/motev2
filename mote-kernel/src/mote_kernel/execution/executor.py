"""Stateless public graph execution driver."""

from typing import TypeVar

from mote_kernel.execution.engine.superstep import execute_superstep
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import StepResult

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


async def step_graph(request: StepRequest[InputT, OutputT]) -> StepResult[InputT, OutputT]:
    """Execute exactly one superstep without mutating or persisting state."""

    return await execute_superstep(request)


__all__ = ["step_graph"]
