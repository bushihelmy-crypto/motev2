"""The graph node that executes one authorized tool invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    AuthorizeStageValue,
    ExecuteNodeInput,
    ExecuteStageValue,
    ExecutionStopped,
    HookStateProjection,
    ToolExecutionResult,
)
from mote_kernel.act.identity import ActHookStage
from mote_kernel.act.port import ExecutePort
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookRequest
from mote_kernel.state.graph_state import GraphNodeId

HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


@dataclass(frozen=True, slots=True)
class ExecuteNode(Generic[HookStateT, HookCommandT]):
    """Execute an authorized invocation and publish its result to Settle."""

    execute_port: ExecutePort
    admission: ActPayloadAdmission[HookStateT, HookCommandT]

    async def __call__(
        self,
        value: ExecuteNodeInput[HookCommandT],
        /,
    ) -> HookRequest[ActHookEnvelope, HookStateT] | Graph.Outcome[HookGraphValue]:
        hook_result = self.admission.admit_hook_result(value.hook_result)
        envelope = hook_result.value
        if envelope.stage is not ActHookStage.AUTHORIZE or type(envelope.payload) is not AuthorizeStageValue:
            raise ActContractError("execute input must be an Authorize Hook envelope")
        invocation = envelope.payload.invocation
        outcome = self.admission.admit_execution_outcome(await self.execute_port.execute(invocation))
        if type(outcome) is ExecutionStopped:
            return Graph.failure(outcome.reason.value)

        execution = cast(ToolExecutionResult, outcome)
        self.admission.admit_execution_result(invocation, execution)
        hook_state = self.admission.admit_hook_state(envelope.hook_state)
        next_envelope = ActHookEnvelope(
            ActHookStage.EXECUTE,
            ExecuteStageValue(execution),
            hook_state,
        )
        hook_request = HookRequest(next_envelope, hook_state, GraphNodeId("execute"))
        self.admission.admit_hook_request(hook_request)
        return hook_request


__all__ = ["ExecuteNode"]
