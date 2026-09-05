"""Assemble one fixed failover graph around one concrete Port binding.

The graph owns retry control flow only.  The wrapped Port may itself be a
Hook capability, a model capability, or another typed Port; failover never
injects Hook execution into its internal state transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Generic, TypeAlias, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.failover.contract import (
    AttemptPreparation,
    Completed,
    FailoverContractError,
    FailureStrategy,
    InProgress,
    PortOutcome,
    PreparationAction,
    PreparedRequest,
    Rejected,
    RotateCredential,
    SingleAttempt,
    SwitchEndpoint,
    TransformRequest,
    Unknown,
)
from mote_kernel.failover.plan import (
    FailoverBindingMode,
    FailoverConfigSource,
    FailoverOperationId,
    FailoverPlan,
    FailoverPortId,
    PortBinding,
    RetryContext,
    resolve_plan,
)
from mote_kernel.failover.policy import FailoverDecision, ObservationRoute, observe_and_route
from mote_kernel.hooks.contract import HookGraphValue
from mote_kernel.state.graph_state.identity import is_canonical_identity

RequestT = TypeVar("RequestT")
ResultT = TypeVar("ResultT")
ReceiptT = TypeVar("ReceiptT")
HandleT = TypeVar("HandleT")
TransformT = TypeVar("TransformT")
CapabilityT = TypeVar("CapabilityT")

_FAILOVER_DEFINITION_DOMAIN = "mote.failover.v1"
_FINISH_ROUTE = "finish"


@dataclass(frozen=True, slots=True)
class FailoverCall(HookGraphValue, Generic[RequestT]):
    """One logical operation entering a Port's failover graph."""

    operation_id: FailoverOperationId
    request: RequestT

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.operation_id):
            raise FailoverContractError("failover call operation_id must be canonical")


@dataclass(frozen=True, slots=True)
class _StepContext(Generic[RequestT]):
    request: RequestT
    context: RetryContext


@dataclass(frozen=True, slots=True)
class _InvokeStep(
    _StepContext[RequestT],
    Generic[RequestT],
):
    pass


@dataclass(frozen=True, slots=True)
class _ObserveStep(
    _StepContext[RequestT],
    Generic[RequestT, ResultT, ReceiptT, HandleT],
):
    outcome: PortOutcome[ResultT, ReceiptT, HandleT]


@dataclass(frozen=True, slots=True)
class _PrepareStep(
    _StepContext[RequestT],
    Generic[RequestT, TransformT],
):
    decision: FailoverDecision[TransformT]


@dataclass(frozen=True, slots=True)
class _FinishStep(
    _StepContext[RequestT],
    Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT],
):
    outcome: PortOutcome[ResultT, ReceiptT, HandleT]
    decision: FailoverDecision[TransformT]


_FailoverStep: TypeAlias = (
    _FinishStep[RequestT, ResultT, ReceiptT, HandleT, TransformT]
    | _InvokeStep[RequestT]
    | _ObserveStep[RequestT, ResultT, ReceiptT, HandleT]
    | _PrepareStep[RequestT, TransformT]
)


@dataclass(frozen=True, slots=True)
class _FailoverFrame(
    HookGraphValue,
    Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT],
):
    """Immutable internal value committed between failover activations."""

    step: _FailoverStep[RequestT, ResultT, ReceiptT, HandleT, TransformT]
    plan: FailoverPlan[TransformT]

    def __post_init__(self) -> None:
        if type(self.step) not in (_InvokeStep, _ObserveStep, _PrepareStep, _FinishStep):
            raise FailoverContractError("failover frame contains an unsupported step")
        if type(self.plan) is not FailoverPlan:
            raise FailoverContractError("failover frame requires a FailoverPlan")
        if self.step.context.plan_revision != self.plan.plan_revision:
            raise FailoverContractError("failover frame context and plan revisions must match")


@dataclass(frozen=True, slots=True)
class FailoverResult(
    HookGraphValue,
    Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT],
):
    """The terminal typed result of one completed failover operation."""

    request: RequestT
    context: RetryContext
    outcome: PortOutcome[ResultT, ReceiptT, HandleT]
    decision: FailoverDecision[TransformT]

    def __post_init__(self) -> None:
        if self.decision.route not in (ObservationRoute.COMPLETED, ObservationRoute.RETURN_TO_MODEL):
            raise FailoverContractError("failover result requires a non-aborting terminal decision")
        if self.decision.route is ObservationRoute.COMPLETED and not isinstance(self.outcome, Completed):
            raise FailoverContractError("a completed failover result requires a completed Port outcome")


def _admit_port_outcome(
    outcome: PortOutcome[ResultT, ReceiptT, HandleT],
    error_message: str,
    /,
) -> PortOutcome[ResultT, ReceiptT, HandleT]:
    if type(outcome) not in (Completed, Rejected, InProgress, Unknown):
        raise FailoverContractError(error_message)
    return outcome


def _require_capability(
    capability: CapabilityT,
    protocol: type[CapabilityT],
    error_message: str,
    /,
) -> None:
    if not isinstance(capability, protocol):
        raise FailoverContractError(error_message)


@dataclass(frozen=True, slots=True)
class _LoadPlanOnce(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    port_id: FailoverPortId
    binding: PortBinding[TransformT]
    config_source: FailoverConfigSource[TransformT]

    async def __call__(self, values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        call = cast(FailoverCall[RequestT], values["request"])
        plan = cast(FailoverPlan[TransformT], resolve_plan(self.config_source.snapshot(), self.port_id, self.binding))
        frame = _FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT](
            _InvokeStep[RequestT](
                call.request,
                RetryContext(call.operation_id, plan.plan_revision),
            ),
            plan,
        )
        return Graph.values(frame=frame)


@dataclass(frozen=True, slots=True)
class _InvokeOnce(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    attempt: SingleAttempt[RequestT, PortOutcome[ResultT, ReceiptT, HandleT]]

    async def __call__(self, values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        frame = cast(_FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT], values["frame"])
        candidate = frame.step
        if not isinstance(candidate, _InvokeStep):
            raise FailoverContractError("invoke node requires an invoke step")
        step = candidate
        outcome = _admit_port_outcome(
            await self.attempt.invoke_once(step.request),
            "single-attempt capability returned an unsupported outcome",
        )
        next_frame = _FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT](
            _ObserveStep[RequestT, ResultT, ReceiptT, HandleT](step.request, step.context, outcome),
            frame.plan,
        )
        return Graph.values(frame=next_frame)


@dataclass(frozen=True, slots=True)
class _ObserveAndRoute(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    async def __call__(self, values: Graph.Values[HookGraphValue], /) -> Graph.Outcome[HookGraphValue]:
        frame = cast(_FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT], values["frame"])
        candidate = frame.step
        if not isinstance(candidate, _ObserveStep):
            raise FailoverContractError("observe node requires an observe step")
        step = candidate
        decision = observe_and_route(step.outcome, frame.plan, step.context)
        context = step.context
        if isinstance(step.outcome, Rejected) or type(step.outcome) is Unknown:
            context = replace(
                context,
                last_failure=step.outcome.evidence.category,
                last_signal=step.outcome.evidence.signal,
            )

        if decision.route is ObservationRoute.PREPARE:
            next_step: _FailoverStep[RequestT, ResultT, ReceiptT, HandleT, TransformT] = _PrepareStep[
                RequestT, TransformT
            ](
                step.request,
                context,
                decision,
            )
            route = ObservationRoute.PREPARE.value
        elif decision.route is ObservationRoute.ABORT:
            return Graph.failure("failover policy aborted the operation")
        else:
            next_step = _FinishStep[RequestT, ResultT, ReceiptT, HandleT, TransformT](
                step.request,
                context,
                step.outcome,
                decision,
            )
            route = _FINISH_ROUTE
        next_frame = _FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT](next_step, frame.plan)
        return Graph.success(Graph.values(frame=next_frame), route=route)


@dataclass(frozen=True, slots=True)
class _PrepareNextAttempt(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    preparation: AttemptPreparation[RequestT, TransformT]

    async def __call__(self, values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        frame = cast(_FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT], values["frame"])
        candidate = frame.step
        if not isinstance(candidate, _PrepareStep):
            raise FailoverContractError("prepare node requires a prepare step")
        step = candidate
        strategy = cast(FailureStrategy, step.decision.strategy)
        action = cast(PreparationAction[TransformT], step.decision.preparation)
        prepared = await self.preparation.prepare_next(step.request, action)
        if type(prepared) is not PreparedRequest:
            raise FailoverContractError("preparation capability must return a PreparedRequest")

        context = step.context.with_strategy_use(strategy)
        context = replace(
            context,
            request_version=context.request_version + int(isinstance(action, TransformRequest)),
            attempt_ordinal=context.attempt_ordinal + 1,
            endpoint_cursor=context.endpoint_cursor + int(isinstance(action, SwitchEndpoint)),
            credential_cursor=context.credential_cursor + int(isinstance(action, RotateCredential)),
            wait_until=None,
        )
        next_frame = _FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT](
            _InvokeStep[RequestT](prepared.request, context),
            frame.plan,
        )
        return Graph.values(frame=next_frame)


@dataclass(frozen=True, slots=True)
class _Finish(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    async def __call__(self, values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        frame = cast(_FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT], values["frame"])
        candidate = frame.step
        if not isinstance(candidate, _FinishStep):
            raise FailoverContractError("finish node requires a finish step")
        step = candidate
        result = FailoverResult[RequestT, ResultT, ReceiptT, HandleT, TransformT](
            step.request,
            step.context,
            step.outcome,
            step.decision,
        )
        return Graph.values(result=result)


def _definition_id(port_id: FailoverPortId) -> str:
    fields = (_FAILOVER_DEFINITION_DOMAIN, str(port_id))
    return "".join(f"{len(field)}:{field}" for field in fields)


@dataclass(frozen=True, slots=True)
class Failover(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    """Decorate one single-attempt Port with the canonical failover graph.

    Role/Flow composition supplies the binding and supporting capabilities
    once, then calls this object with the Port being wrapped.  The returned
    value is a normal nested :class:`Graph`, so retries remain in the parent
    graph's state and execution boundaries rather than a decorator-owned loop.
    """

    port_id: FailoverPortId
    binding: PortBinding[TransformT]
    config_source: FailoverConfigSource[TransformT]
    preparation: AttemptPreparation[RequestT, TransformT]

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.port_id):
            raise FailoverContractError("failover decorator port_id must be canonical")
        if type(self.binding) is not PortBinding:
            raise FailoverContractError("failover decorator requires a PortBinding")
        if self.binding.mode is FailoverBindingMode.DISABLED:
            raise FailoverContractError("disabled Port bindings must omit the failover decorator")
        _require_capability(
            self.config_source,
            FailoverConfigSource,
            "failover decorator requires a config source",
        )
        _require_capability(
            self.preparation,
            AttemptPreparation,
            "failover decorator requires one preparation capability",
        )

    def __call__(
        self,
        port: SingleAttempt[RequestT, PortOutcome[ResultT, ReceiptT, HandleT]],
        /,
    ) -> Graph[HookGraphValue]:
        _require_capability(port, SingleAttempt, "failover decorator requires one single-attempt Port")

        graph = Graph[HookGraphValue](_definition_id(self.port_id), version=1)
        request_type = cast(type[HookGraphValue], FailoverCall)
        frame_type = cast(type[HookGraphValue], _FailoverFrame)
        result_type = cast(type[HookGraphValue], FailoverResult)
        request = graph.graph_input("request", request_type)

        graph.add_node(
            "load_plan",
            _LoadPlanOnce[RequestT, ResultT, ReceiptT, HandleT, TransformT](
                self.port_id,
                self.binding,
                self.config_source,
            ),
            inputs={"request": request},
            outputs={"frame": frame_type},
        )
        graph.add_node(
            "invoke",
            _InvokeOnce[RequestT, ResultT, ReceiptT, HandleT, TransformT](port),
            inputs={"frame": graph.node_output("frame")},
            outputs={"frame": frame_type},
        )
        graph.add_node(
            "observe",
            _ObserveAndRoute[RequestT, ResultT, ReceiptT, HandleT, TransformT](),
            inputs={"frame": graph.node_output("invoke", "frame")},
            outputs={"frame": frame_type},
        )
        graph.add_node(
            "prepare",
            _PrepareNextAttempt[RequestT, ResultT, ReceiptT, HandleT, TransformT](self.preparation),
            inputs={"frame": graph.node_output("observe", "frame")},
            outputs={"frame": frame_type},
        )
        graph.add_node(
            "finish",
            _Finish[RequestT, ResultT, ReceiptT, HandleT, TransformT](),
            inputs={"frame": graph.node_output("observe", "frame")},
            outputs={"result": result_type},
        )

        graph.add_edge("load_plan", "invoke")
        graph.add_edge("invoke", "observe")
        graph.add_conditional_edge("observe", ObservationRoute.PREPARE.value, "prepare")
        graph.add_conditional_edge("observe", _FINISH_ROUTE, "finish")
        graph.add_edge("prepare", "invoke")
        graph.add_edge("finish", Graph.END)
        graph.set_outputs({"result": graph.node_output("finish", "result")})
        return graph


__all__ = ["Failover"]
