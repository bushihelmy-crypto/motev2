"""The graph node that projects and delivers one execution result."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.config import SettleBinding
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    ExecuteStageValue,
    HookStateProjection,
    SettledActResult,
    SettleStageValue,
    ToolExchangeWriteRequest,
)
from mote_kernel.act.failover import ActFailoverDecorators, FailoverPortDecorator, normalize_act_failover_decorators
from mote_kernel.act.identity import ActHookStage, ActNodeId
from mote_kernel.act.port import SettlementPort, ToolExchangeWriter
from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.hooks.contract import HookActivationRequest, HookResult
from mote_kernel.state.graph_state import GraphNodeId

HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


@dataclass(frozen=True, slots=True)
class SettleNode(Generic[HookStateT, HookCommandT]):
    """Project, write, and publish the result produced by Execute."""

    settlement_port: SettlementPort
    exchange_writer: ToolExchangeWriter
    admission: ActPayloadAdmission[HookStateT, HookCommandT]
    failover: ActFailoverDecorators | FailoverPortDecorator | None = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        decorators = normalize_act_failover_decorators(self.failover)
        settlement_port = decorators.settlement_port(self.settlement_port)
        exchange_writer = decorators.exchange_writer_port(self.exchange_writer)
        try:
            settlement_method = settlement_port.project
            writer_method = exchange_writer.write
        except AttributeError as error:
            raise ActContractError("SettleNode requires a SettlementPort and ToolExchangeWriter") from error
        if not callable(settlement_method):
            raise ActContractError("SettleNode requires a SettlementPort")
        if not callable(writer_method):
            raise ActContractError("SettleNode requires a ToolExchangeWriter")
        object.__setattr__(self, "settlement_port", settlement_port)
        object.__setattr__(self, "exchange_writer", exchange_writer)
        object.__setattr__(self, "failover", decorators)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ActContractError("settle assembly snapshot key is malformed")

    async def __call__(
        self,
        activation: ConfigActivation[HookResult[ActHookEnvelope, HookCommandT]],
        /,
    ) -> HookActivationRequest[ActHookEnvelope, HookStateT]:
        hook_result = self.admission.admit_hook_result(activation.value)
        config = activation.activation_config
        # ``__post_init__`` stores the normalized bundle; reuse it for any
        # activation-time capability selection.
        decorators = cast(ActFailoverDecorators, self.failover)
        settlement_port = self.settlement_port
        exchange_writer = self.exchange_writer
        if config is not None:
            selected = config.bind(SettleBinding[HookStateT, HookCommandT]())
            if selected.admission != self.admission:
                raise ActContractError("Act config binding changed the compiled payload contract")
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                settlement_port = decorators.settlement_port(selected.capability.settlement_port)
                exchange_writer = decorators.exchange_writer_port(selected.capability.exchange_writer)
        envelope = hook_result.value
        if envelope.stage is not ActHookStage.EXECUTE or type(envelope.payload) is not ExecuteStageValue:
            raise ActContractError("settle input must be an Execute Hook envelope")
        execution = envelope.payload.result

        projection = self.admission.admit_settlement_projection(
            execution,
            await settlement_port.project(execution),
        )
        write_request = self.admission.admit_write_request(ToolExchangeWriteRequest(projection))
        write_result = self.admission.admit_write_result(await exchange_writer.write(write_request))
        settled = self.admission.admit_settled_result(SettledActResult(projection, write_result.receipt))
        hook_state = self.admission.admit_hook_state(envelope.hook_state)
        next_envelope = ActHookEnvelope(
            ActHookStage.SETTLE,
            SettleStageValue(settled),
            hook_state,
        )
        hook_request = HookActivationRequest(
            next_envelope,
            hook_state,
            GraphNodeId(str(ActNodeId.SETTLE)),
        )
        self.admission.admit_hook_request(hook_request)
        return hook_request


__all__ = ["SettleNode"]
