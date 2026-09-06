"""The graph node that resolves one Act request into a stable invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
    AuthorizationInput,
    HookStateProjection,
    ResolutionStopped,
    ResolvedInvocation,
    ResolveStageValue,
)
from mote_kernel.act.identity import ActHookStage
from mote_kernel.act.port import ResolvePort
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookRequest
from mote_kernel.state.graph_state import GraphNodeId

HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


@dataclass(frozen=True, slots=True)
class ResolveNode(Generic[HookStateT, HookCommandT]):
    """Resolve an Act request and publish the first shared-Hook envelope."""

    resolve_port: ResolvePort
    admission: ActPayloadAdmission[HookStateT, HookCommandT]

    async def __call__(
        self,
        value: ActRequest,
        /,
    ) -> HookRequest[ActHookEnvelope, HookStateT] | Graph.Outcome[HookGraphValue]:
        request = self.admission.admit_request(value)

        result = self.admission.admit_resolution_result(await self.resolve_port.resolve(request))
        if type(result) is ResolutionStopped:
            return Graph.failure(result.reason.value)

        resolved = cast(ResolvedInvocation, result)
        self.admission.admit_resolved_invocation(resolved)
        if resolved.request != request:
            raise ActContractError("resolved invocation request does not match Act request")

        hook_state = self.admission.admit_hook_state(request.hook_state)
        authorization = AuthorizationInput.initial(resolved)
        envelope = ActHookEnvelope(
            ActHookStage.RESOLVE,
            ResolveStageValue(authorization),
            hook_state,
        )
        hook_request = HookRequest(envelope, hook_state, GraphNodeId("resolve"))
        self.admission.admit_hook_request(hook_request)
        return hook_request


__all__ = ["ResolveNode"]
