"""The private HookNode adapter for the shared invocation boundary."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.hooks.contract import (
    HookInvocationRequest,
    HookPayloadAdmission,
    HookRequest,
    HookStageResult,
)
from mote_kernel.hooks.plan import HookPriorityPlan
from mote_kernel.invocation import Invocation, invoke_strict

ConfigT = TypeVar("ConfigT")
PriorityConfigT = TypeVar("PriorityConfigT")
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")


@dataclass(frozen=True, slots=True)
class HookPort(Generic[ConfigT, PriorityConfigT, ValueT, StateT, CommandT]):
    """Adapt one priority plan to one transport-independent invocation.

    The class is available from this implementation module for composition, but
    it is intentionally not part of the package-level Hooks API.
    """

    admission: HookPayloadAdmission[ConfigT, PriorityConfigT, ValueT, StateT, CommandT]
    invocation: Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT, StateT],
        HookStageResult[ValueT, CommandT],
    ]

    async def execute(
        self,
        plan: HookPriorityPlan[PriorityConfigT],
        request: HookRequest[ValueT, StateT],
        /,
    ) -> HookStageResult[ValueT, CommandT]:
        invocation_request = self.admission.admit_invocation_request(HookInvocationRequest(plan.config, request))
        result = await invoke_strict(self.invocation, invocation_request)
        return self.admission.admit_stage_result(result)


__all__ = ["HookPort"]
