"""The graph node that executes one authorized tool invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.config import ExecuteBinding
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    AuthorizeStageValue,
    ExecuteStageValue,
    ExecutionStopped,
    HookStateProjection,
    ToolExecutionResult,
)
from mote_kernel.act.failover import ActFailoverDecorators, FailoverPortDecorator, normalize_act_failover_decorators
from mote_kernel.act.identity import ActHookStage, ActNodeId
from mote_kernel.act.port import ExecutePort
from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookResult
from mote_kernel.state.graph_state import GraphNodeId

HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


@dataclass(frozen=True, slots=True)
class ExecuteNode(Generic[HookStateT, HookCommandT]):
    """Execute an authorized invocation and publish its result to Settle."""

    execute_port: ExecutePort
    admission: ActPayloadAdmission[HookStateT, HookCommandT]
    failover: ActFailoverDecorators | FailoverPortDecorator | None = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        decorators = normalize_act_failover_decorators(self.failover)
        port = decorators.execute_port(self.execute_port)
        try:
            method = port.execute
        except AttributeError as error:
            raise ActContractError("ExecuteNode requires an ExecutePort") from error
        if not callable(method):
            raise ActContractError("ExecuteNode requires an ExecutePort")
        object.__setattr__(self, "execute_port", port)
        object.__setattr__(self, "failover", decorators)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ActContractError("execute assembly snapshot key is malformed")

    async def __call__(
        self,
        activation: ConfigActivation[HookResult[ActHookEnvelope, HookCommandT]],
        /,
    ) -> HookActivationRequest[ActHookEnvelope, HookStateT] | Graph.Outcome[HookGraphValue]:
        hook_result = self.admission.admit_hook_result(activation.value)
        config = activation.activation_config
        # ``__post_init__`` stores the normalized bundle; do not create a
        # second normalization path for activation-time rebinding.
        decorators = cast(ActFailoverDecorators, self.failover)
        execute_port = self.execute_port
        if config is not None:
            selected = config.bind(ExecuteBinding[HookStateT, HookCommandT]())
            if selected.admission != self.admission:
                raise ActContractError("Act config binding changed the compiled payload contract")
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                execute_port = decorators.execute_port(selected.capability)
        envelope = hook_result.value
        if envelope.stage is not ActHookStage.AUTHORIZE or type(envelope.payload) is not AuthorizeStageValue:
            raise ActContractError("execute input must be an Authorize Hook envelope")
        invocation = envelope.payload.invocation
        outcome = self.admission.admit_execution_outcome(await execute_port.execute(invocation))
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
        hook_request = HookActivationRequest(
            next_envelope,
            hook_state,
            GraphNodeId(str(ActNodeId.EXECUTE)),
        )
        self.admission.admit_hook_request(hook_request)
        return hook_request


__all__ = ["ExecuteNode"]
