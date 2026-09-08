"""Assemble one fixed failover graph around one resolved Port config.

The graph owns retry control flow only.  The wrapped Port may itself be a
Hook capability, a model capability, or another typed Port; failover never
injects Hook execution into its internal state transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Generic, TypeAlias, TypeVar, cast

from mote_kernel.config import Config, ConfigContractError, require_config
from mote_kernel.execution import Graph
from mote_kernel.failover.config import (
    FailoverBinding,
    FailoverPlanBinding,
    FailoverPlanConfig,
    FailoverPrepareBinding,
    FailoverPrepareConfig,
)
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
from mote_kernel.failover.plan import FailoverOperationId, FailoverPlan, FailoverPortId, RetryContext
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


def _runtime_plan(
    config: Config | None,
    binding: FailoverPlanBinding[TransformT] | None,
    fallback_plan: FailoverPlan[TransformT],
    /,
) -> FailoverPlan[TransformT]:
    """Select the current plan for one failover activation."""

    if config is None:
        return fallback_plan
    effective_binding = binding or FailoverPlanBinding[TransformT](fallback_plan.port_id)
    try:
        selected = config.bind(effective_binding)
    except ConfigContractError as error:
        raise FailoverContractError(str(error)) from error
    if selected is None:
        raise FailoverContractError("failover Config binding did not provide the compiled Port projection")
    if type(selected) is not FailoverPlanConfig:
        raise FailoverContractError("failover plan binding returned an invalid projection")
    if selected.plan.port_id != fallback_plan.port_id:
        raise FailoverContractError("failover Config binding changed the compiled Port contract")
    return selected.plan


def _runtime_config(
    config: Config | None,
    binding: FailoverPrepareBinding[RequestT, TransformT] | None,
    fallback_plan: FailoverPlan[TransformT],
    fallback_preparation: AttemptPreparation[RequestT, TransformT],
    /,
) -> tuple[FailoverPlan[TransformT], AttemptPreparation[RequestT, TransformT]]:
    """Select the current Port projection for one failover activation.

    The fallback values support the low-level ``Failover(plan, preparation)``
    constructor used by callers that do not have a complete Config.  A
    Config-backed decorator always has a binding; missing/invalid projections
    therefore fail closed instead of silently retaining an old plan.
    """

    if config is None:
        return fallback_plan, fallback_preparation
    effective_binding = binding or FailoverPrepareBinding[RequestT, TransformT](fallback_plan.port_id)
    try:
        selected = config.bind(effective_binding)
    except ConfigContractError as error:
        raise FailoverContractError(str(error)) from error
    if selected is None:
        raise FailoverContractError("failover Config binding did not provide the compiled Port projection")
    if type(selected) is not FailoverPrepareConfig:
        raise FailoverContractError("failover prepare binding returned an invalid projection")
    if selected.plan.port_id != fallback_plan.port_id:
        raise FailoverContractError("failover Config binding changed the compiled Port contract")
    return selected.plan, selected.preparation


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


_FailoverStep: TypeAlias = _InvokeStep[RequestT] | _ObserveStep[RequestT, ResultT, ReceiptT, HandleT]


@dataclass(frozen=True, slots=True)
class _FailoverFrame(
    HookGraphValue,
    Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT],
):
    """Immutable internal value committed between failover activations."""

    step: _FailoverStep[RequestT, ResultT, ReceiptT, HandleT]
    plan: FailoverPlan[TransformT]

    def __post_init__(self) -> None:
        if type(self.step) not in (_InvokeStep, _ObserveStep):
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
    """One observed attempt, exported only from a terminal graph route."""

    request: RequestT
    context: RetryContext
    outcome: PortOutcome[ResultT, ReceiptT, HandleT]
    decision: FailoverDecision[TransformT]

    def __post_init__(self) -> None:
        if self.decision.route is ObservationRoute.ABORT:
            raise FailoverContractError("failover result cannot carry an abort decision")
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
class _ObserveCall(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    """Observe one new call and bind its Port projection at activation time."""

    config: FailoverPlan[TransformT]
    binding: FailoverPlanBinding[TransformT] | None = None

    async def __call__(self, values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        call = cast(FailoverCall[RequestT], values["request"])
        plan = _runtime_plan(
            values.activation_config,
            self.binding,
            self.config,
        )
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
    fallback_plan: FailoverPlan[TransformT] | None = None
    binding: FailoverPlanBinding[TransformT] | None = None

    async def __call__(self, values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        frame = cast(_FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT], values["frame"])
        candidate = frame.step
        if not isinstance(candidate, _InvokeStep):
            raise FailoverContractError("invoke node requires an invoke step")
        step = candidate
        fallback_plan = frame.plan if self.fallback_plan is None else self.fallback_plan
        plan = _runtime_plan(
            values.activation_config,
            self.binding,
            fallback_plan,
        )
        if step.context.plan_revision != plan.plan_revision:
            raise FailoverContractError("failover frame context and bound plan revisions must match")
        outcome = _admit_port_outcome(
            await self.attempt.invoke_once(step.request),
            "single-attempt capability returned an unsupported outcome",
        )
        next_frame = _FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT](
            _ObserveStep[RequestT, ResultT, ReceiptT, HandleT](step.request, step.context, outcome),
            plan,
        )
        return Graph.values(frame=next_frame)


@dataclass(frozen=True, slots=True)
class _PrepareNextAttempt(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    """Classify one outcome, finish it, or prepare exactly one next attempt."""

    preparation: AttemptPreparation[RequestT, TransformT]
    fallback_plan: FailoverPlan[TransformT] | None = None
    binding: FailoverPrepareBinding[RequestT, TransformT] | None = None

    async def __call__(self, values: Graph.Values[HookGraphValue], /) -> Graph.Outcome[HookGraphValue]:
        frame = cast(_FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT], values["frame"])
        candidate = frame.step
        if not isinstance(candidate, _ObserveStep):
            raise FailoverContractError("prepare node requires an observe step")
        step = candidate
        fallback_plan = frame.plan if self.fallback_plan is None else self.fallback_plan
        plan, preparation = _runtime_config(
            values.activation_config,
            self.binding,
            fallback_plan,
            self.preparation,
        )
        if step.context.plan_revision != plan.plan_revision:
            raise FailoverContractError("failover frame context and bound plan revisions must match")
        decision = observe_and_route(step.outcome, plan, step.context)
        context = step.context
        if isinstance(step.outcome, Rejected) or type(step.outcome) is Unknown:
            context = replace(
                context,
                last_failure=step.outcome.evidence.category,
                last_signal=step.outcome.evidence.signal,
            )

        if decision.route is ObservationRoute.ABORT:
            return Graph.failure("failover policy aborted the operation")
        result = FailoverResult[RequestT, ResultT, ReceiptT, HandleT, TransformT](
            step.request,
            context,
            step.outcome,
            decision,
        )
        if decision.route is ObservationRoute.PREPARE:
            strategy = cast(FailureStrategy, decision.strategy)
            action = cast(PreparationAction[TransformT], decision.preparation)
            prepared = await preparation.prepare_next(step.request, action)
            if type(prepared) is not PreparedRequest:
                raise FailoverContractError("preparation capability must return a PreparedRequest")
            next_context = context.with_strategy_use(strategy)
            next_context = replace(
                next_context,
                request_version=next_context.request_version + int(isinstance(action, TransformRequest)),
                attempt_ordinal=next_context.attempt_ordinal + 1,
                endpoint_cursor=next_context.endpoint_cursor + int(isinstance(action, SwitchEndpoint)),
                credential_cursor=next_context.credential_cursor + int(isinstance(action, RotateCredential)),
                wait_until=None,
            )
            next_step: _FailoverStep[RequestT, ResultT, ReceiptT, HandleT] = _InvokeStep[RequestT](
                prepared.request,
                next_context,
            )
            route = ObservationRoute.PREPARE.value
        else:
            next_step = _ObserveStep[RequestT, ResultT, ReceiptT, HandleT](
                step.request,
                context,
                step.outcome,
            )
            route = _FINISH_ROUTE
        next_frame = _FailoverFrame[RequestT, ResultT, ReceiptT, HandleT, TransformT](
            next_step,
            plan,
        )
        return Graph.success(Graph.values(frame=next_frame, result=result), route=route)


def _definition_id(port_id: FailoverPortId) -> str:
    fields = (_FAILOVER_DEFINITION_DOMAIN, str(port_id))
    return "".join(f"{len(field)}:{field}" for field in fields)


@dataclass(frozen=True, slots=True)
class Failover(Generic[RequestT, ResultT, ReceiptT, HandleT, TransformT]):
    """Decorate one single-attempt Port with the canonical failover graph.

    Role/Flow composition supplies the resolved config and preparation
    capability once, then calls this object with the Port being wrapped.  The
    returned value is a normal nested :class:`Graph`, so retries remain in the
    parent graph's state and execution boundaries rather than a decorator-owned
    loop.
    """

    config: FailoverPlan[TransformT]
    preparation: AttemptPreparation[RequestT, TransformT]
    plan_binding: FailoverPlanBinding[TransformT] | None = None
    prepare_binding: FailoverPrepareBinding[RequestT, TransformT] | None = None

    @classmethod
    def bind(
        cls,
        config: Config,
        port_id: FailoverPortId,
        /,
        *,
        required: bool = True,
    ) -> Failover[RequestT, ResultT, ReceiptT, HandleT, TransformT] | None:
        """Bind one Port's failover projection from the complete config.

        Optional Port capabilities return ``None`` when no matching projection
        exists, allowing their graph steps to be omitted during assembly.
        Duplicate projections always fail closed.
        """

        config = require_config(config)
        selected = config.bind(FailoverBinding[RequestT, TransformT](port_id, required))
        if selected is None:
            return None
        return cls(
            selected.plan,
            selected.preparation,
            FailoverPlanBinding[TransformT](port_id, required),
            FailoverPrepareBinding[RequestT, TransformT](port_id, required),
        )

    def __post_init__(self) -> None:
        if type(self.config) is not FailoverPlan:
            raise FailoverContractError("failover decorator requires a FailoverPlan config")
        if self.plan_binding is not None:
            if type(self.plan_binding) is not FailoverPlanBinding:
                raise FailoverContractError("failover decorator plan binding must be a FailoverPlanBinding")
            if self.plan_binding.port_id != self.config.port_id:
                raise FailoverContractError("failover decorator plan binding does not match its Port config")
        if self.prepare_binding is not None:
            if type(self.prepare_binding) is not FailoverPrepareBinding:
                raise FailoverContractError("failover decorator prepare binding must be a FailoverPrepareBinding")
            if self.prepare_binding.port_id != self.config.port_id:
                raise FailoverContractError("failover decorator prepare binding does not match its Port config")
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

        graph = Graph[HookGraphValue](_definition_id(self.config.port_id), version=2)
        request_type = cast(type[HookGraphValue], FailoverCall)
        frame_type = cast(type[HookGraphValue], _FailoverFrame)
        result_type = cast(type[HookGraphValue], FailoverResult)
        request = graph.graph_input("request", request_type)

        graph.add_node(
            "observe",
            _ObserveCall[RequestT, ResultT, ReceiptT, HandleT, TransformT](
                self.config,
                self.plan_binding,
            ),
            inputs={"request": request},
            outputs={"frame": frame_type},
        )
        graph.add_node(
            "invoke",
            _InvokeOnce[RequestT, ResultT, ReceiptT, HandleT, TransformT](
                port,
                self.config,
                self.plan_binding,
            ),
            inputs={"frame": graph.node_output("frame")},
            outputs={"frame": frame_type},
        )
        graph.add_node(
            "prepare",
            _PrepareNextAttempt[RequestT, ResultT, ReceiptT, HandleT, TransformT](
                self.preparation,
                self.config,
                self.prepare_binding,
            ),
            inputs={"frame": graph.node_output("invoke", "frame")},
            outputs={"frame": frame_type, "result": result_type},
        )
        graph.add_edge(Graph.START, "observe")
        graph.add_edge("observe", "invoke")
        graph.add_edge("invoke", "prepare")
        graph.add_edge("prepare", ObservationRoute.PREPARE.value, "invoke")
        graph.add_edge("prepare", _FINISH_ROUTE, Graph.END)
        graph.set_outputs({"result": graph.node_output("prepare", "result")})
        return graph


__all__ = ["Failover"]
