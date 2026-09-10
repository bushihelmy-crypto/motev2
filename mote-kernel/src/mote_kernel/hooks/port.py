"""The private HookNode adapter for the shared invocation boundary."""

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.hooks.contract import (
    HookActivationRequest,
    HookContractError,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookStageResult,
)
from mote_kernel.hooks.plan import HookPriorityPlan
from mote_kernel.invocation import (
    Invocation,
    InvocationBoundaryAdmissionError,
    InvocationTypeContract,
    invoke_typed,
)
from mote_kernel.state.graph_state import GraphConfigCursor

PriorityConfigT = TypeVar("PriorityConfigT")
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")


@dataclass(frozen=True, slots=True)
class HookPort(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    """Adapt one priority plan to one transport-independent invocation.

    The class is available from this implementation module for composition, but
    it is intentionally not part of the package-level Hooks API.
    """

    admission: HookPayloadAdmission[PriorityConfigT, ValueT, StateT, CommandT]
    invocation: Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT],
        HookStageResult[ValueT, CommandT],
    ]

    async def execute(
        self,
        plan: HookPriorityPlan[PriorityConfigT],
        request: HookActivationRequest[ValueT, StateT],
        config_cursor: GraphConfigCursor | None,
        /,
    ) -> HookStageResult[ValueT, CommandT]:
        admitted_request = self.admission.admit_request(request)
        invocation_request = self.admission.admit_invocation_request(
            HookInvocationRequest(plan.config, admitted_request.value, config_cursor)
        )
        request_type = cast(
            type[HookInvocationRequest[PriorityConfigT, ValueT]],
            HookInvocationRequest,
        )
        result_type = cast(type[HookStageResult[ValueT, CommandT]], HookStageResult)
        contract = InvocationTypeContract(request_type, result_type)
        try:
            result = await invoke_typed(self.invocation, invocation_request, contract)
        except InvocationBoundaryAdmissionError as error:
            raise HookContractError(str(error)) from error
        admitted = self.admission.admit_stage_result(result)
        self.admission.admit_transition(admitted_request, admitted)
        return admitted


__all__ = ["HookPort"]
