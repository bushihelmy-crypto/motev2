"""The graph node that resolves one Act request into a stable invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.config import ResolveBinding
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
from mote_kernel.act.failover import ActFailoverDecorators, FailoverPortDecorator, normalize_act_failover_decorators
from mote_kernel.act.identity import ActHookStage, ActNodeId
from mote_kernel.act.port import ResolvePort
from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue
from mote_kernel.state.graph_state import GraphNodeId

HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


@dataclass(frozen=True, slots=True)
class ResolveNode(Generic[HookStateT, HookCommandT]):
    """Resolve an Act request and publish the first shared-Hook envelope."""

    resolve_port: ResolvePort
    admission: ActPayloadAdmission[HookStateT, HookCommandT]
    failover: ActFailoverDecorators | FailoverPortDecorator | None = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Decorate the assembly-time capability exactly once.

        Stage nodes are also useful as owner-local callables in tests and in
        composition code that does not build the enclosing ``ActNode``.  The
        initial Port therefore has to receive the same declaration here as it
        does when the parent graph is assembled.  Runtime Config projections
        are decorated separately in ``__call__``.
        """

        decorators = normalize_act_failover_decorators(self.failover)
        port = decorators.resolve_port(self.resolve_port)
        try:
            method = port.resolve
        except AttributeError as error:
            raise ActContractError("ResolveNode requires a ResolvePort") from error
        if not callable(method):
            raise ActContractError("ResolveNode requires a ResolvePort")
        object.__setattr__(self, "resolve_port", port)
        object.__setattr__(self, "failover", decorators)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ActContractError("resolve assembly snapshot key is malformed")

    async def __call__(
        self,
        value: ConfigActivation[ActRequest],
        /,
    ) -> HookActivationRequest[ActHookEnvelope, HookStateT] | Graph.Outcome[HookGraphValue]:
        request = self.admission.admit_request(value.value)
        config = value.activation_config

        # ``__post_init__`` stores the normalized bundle; reuse that single
        # owner for activation-time rebinding instead of normalizing again.
        decorators = cast(ActFailoverDecorators, self.failover)
        resolve_port = self.resolve_port
        if config is not None:
            selected = config.bind(ResolveBinding[HookStateT, HookCommandT]())
            if selected.admission != self.admission:
                raise ActContractError("Act config binding changed the compiled payload contract")
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                resolve_port = decorators.resolve_port(selected.capability)
        result = self.admission.admit_resolution_result(await resolve_port.resolve(request))
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
        hook_request = HookActivationRequest(
            envelope,
            hook_state,
            GraphNodeId(str(ActNodeId.RESOLVE)),
        )
        self.admission.admit_hook_request(hook_request)
        return hook_request


__all__ = ["ResolveNode"]
