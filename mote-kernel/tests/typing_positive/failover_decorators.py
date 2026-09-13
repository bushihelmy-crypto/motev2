"""Concrete Port decorator typing examples."""

from dataclasses import dataclass
from typing import assert_type

from mote_kernel.act.contract import (
    ActHookCommand,
    ActHookEnvelope,
    HookStateProjection,
)
from mote_kernel.act.failover import ActFailoverDecorators
from mote_kernel.act.port import (
    AuthorizePort,
    ExecutePort,
    ResolvePort,
    SettlementPort,
    ToolExchangeWriter,
)
from mote_kernel.hooks.contract import HookInvocationRequest, HookStageResult
from mote_kernel.hooks.failover import HookFailoverDecorators
from mote_kernel.invocation import Invocation
from mote_kernel.observe.failover import ObserveFailoverDecorators
from mote_kernel.observe.port import (
    BackgroundTaskPort,
    ConfigObservationPort,
    ContextObservationPort,
    ObservationAckPort,
    ObservationQueuePort,
    ObservationResumePort,
)
from mote_kernel.think.contract import (
    CommandPort,
    CompactedContext,
    CompactPort,
    CompactRequest,
    ContextFrame,
    ContextPort,
    ContextRequest,
    InferencePort,
    InferenceRequest,
    InferenceResult,
    PromptPort,
    RouterPort,
    RouterRequest,
    ThinkCoreResult,
)
from mote_kernel.think.failover import ThinkFailoverDecorators


@dataclass(frozen=True, slots=True)
class State(HookStateProjection):
    pass


@dataclass(frozen=True, slots=True)
class Command(ActHookCommand):
    pass


class ResolveDecorator:
    def __call__(self, port: ResolvePort, /) -> ResolvePort:
        return port


class AuthorizeDecorator:
    def __call__(self, port: AuthorizePort, /) -> AuthorizePort:
        return port


class ExecuteDecorator:
    def __call__(self, port: ExecutePort, /) -> ExecutePort:
        return port


class SettlementDecorator:
    def __call__(self, port: SettlementPort, /) -> SettlementPort:
        return port


class WriterDecorator:
    def __call__(self, port: ToolExchangeWriter, /) -> ToolExchangeWriter:
        return port


ActFailoverDecorators(
    resolve=ResolveDecorator(),
    authorize=AuthorizeDecorator(),
    execute=ExecuteDecorator(),
    settlement=SettlementDecorator(),
    exchange_writer=WriterDecorator(),
)


class PromptDecorator:
    def __call__(self, port: PromptPort[str, str, str, str], /) -> PromptPort[str, str, str, str]:
        return port


class ContextDecorator:
    def __call__(
        self,
        port: ContextPort[ContextRequest[str, State, str, str, str], ContextFrame[tuple[str, ...]]],
        /,
    ) -> ContextPort[ContextRequest[str, State, str, str, str], ContextFrame[tuple[str, ...]]]:
        return port


class CompactDecorator:
    def __call__(
        self,
        port: CompactPort[CompactRequest[str, str, str, tuple[str, ...]], CompactedContext[tuple[str, ...]]],
        /,
    ) -> CompactPort[CompactRequest[str, str, str, tuple[str, ...]], CompactedContext[tuple[str, ...]]]:
        return port


class RouterDecorator:
    def __call__(
        self,
        port: RouterPort[RouterRequest[str, str, str, tuple[str, ...]]],
        /,
    ) -> RouterPort[RouterRequest[str, str, str, tuple[str, ...]]]:
        return port


class InferenceDecorator:
    def __call__(
        self,
        port: InferencePort[
            InferenceRequest[str, str, str, tuple[str, ...]],
            InferenceResult[str],
        ],
        /,
    ) -> InferencePort[InferenceRequest[str, str, str, tuple[str, ...]], InferenceResult[str]]:
        return port


class CommandDecorator:
    def __call__(
        self,
        port: CommandPort[InferenceResult[str], ThinkCoreResult[str]],
        /,
    ) -> CommandPort[InferenceResult[str], ThinkCoreResult[str]]:
        return port


think_decorators = ThinkFailoverDecorators[
    str,
    State,
    str,
    str,
    str,
    tuple[str, ...],
    tuple[str, ...],
    str,
    str,
](
    prompt=PromptDecorator(),
    context=ContextDecorator(),
    compact=CompactDecorator(),
    router=RouterDecorator(),
    inference=InferenceDecorator(),
    command=CommandDecorator(),
)
assert_type(
    think_decorators,
    ThinkFailoverDecorators[str, State, str, str, str, tuple[str, ...], tuple[str, ...], str, str],
)


class QueueDecorator:
    def __call__(self, port: ObservationQueuePort, /) -> ObservationQueuePort:
        return port


class TaskDecorator:
    def __call__(self, port: BackgroundTaskPort, /) -> BackgroundTaskPort:
        return port


class ConfigDecorator:
    def __call__(self, port: ConfigObservationPort, /) -> ConfigObservationPort:
        return port


class ObservationContextDecorator:
    def __call__(self, port: ContextObservationPort, /) -> ContextObservationPort:
        return port


class AckDecorator:
    def __call__(self, port: ObservationAckPort, /) -> ObservationAckPort:
        return port


class ResumeDecorator:
    def __call__(self, port: ObservationResumePort, /) -> ObservationResumePort:
        return port


observe_decorators = ObserveFailoverDecorators(
    queue=QueueDecorator(),
    background_task=TaskDecorator(),
    config=ConfigDecorator(),
    context=ObservationContextDecorator(),
    ack=AckDecorator(),
    resume=ResumeDecorator(),
)
assert_type(observe_decorators, ObserveFailoverDecorators)


class HookDecorator:
    def __call__(
        self,
        invocation: Invocation[HookInvocationRequest[int, ActHookEnvelope], HookStageResult[ActHookEnvelope, Command]],
        /,
    ) -> Invocation[HookInvocationRequest[int, ActHookEnvelope], HookStageResult[ActHookEnvelope, Command]]:
        return invocation


hook_decorators = HookFailoverDecorators[int, ActHookEnvelope, State, Command](invocation=HookDecorator())
assert_type(hook_decorators, HookFailoverDecorators[int, ActHookEnvelope, State, Command])
