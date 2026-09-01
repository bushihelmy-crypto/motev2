"""Deterministic tests for observation values and node instrumentation."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass, field
from typing import cast

import pytest

import mote_kernel.observability as observability_package
from mote_kernel.execution import Graph
from mote_kernel.observability import ObservedNode
from mote_kernel.observability.node import NodeSpanFactory
from mote_kernel.observability.record import (
    ErrorRecord,
    Observation,
    ObservationError,
    ObservationRecord,
    SpanFinished,
    SpanStarted,
    TimingRecord,
    UsageMeasurement,
    UsageRecord,
)
from mote_kernel.observability.span import (
    ObservationAttribute,
    ObservationContractError,
    ObservationValue,
    Span,
    SpanContext,
    SpanId,
    SpanKind,
    SpanStatus,
    TraceId,
)


@dataclass
class RecordingPort:
    observations: list[Observation] = field(default_factory=lambda: list[Observation]())

    def record(self, observation: Observation, /) -> None:
        self.observations.append(observation)


@dataclass(frozen=True)
class RaisingPort:
    error: BaseException

    def record(self, _observation: Observation, /) -> None:
        raise self.error


def _context(span_id: str = "span-1", *, parent: str | None = None) -> SpanContext:
    return SpanContext(
        TraceId("trace-1"),
        SpanId(span_id),
        None if parent is None else SpanId(parent),
    )


def _span(span_id: str = "span-1", *, parent: str | None = None) -> Span:
    return Span(
        _context(span_id, parent=parent),
        "role.node",
        SpanKind.INTERNAL,
        (ObservationAttribute("node_id", "think"),),
    )


async def _echo(value: str) -> str:
    return value


def test_observability_root_exposes_only_the_node_decorator() -> None:
    assert observability_package.__all__ == ["ObservedNode"]
    assert observability_package.ObservedNode is ObservedNode
    assert not hasattr(observability_package, "ObservabilityPort")
    assert not hasattr(observability_package, "Span")
    assert not hasattr(observability_package, "observed_node")
    assert not hasattr(observability_package, "ObservedGraphCommit")


def test_span_values_are_frozen_provider_neutral_and_parented() -> None:
    context = _context("child", parent="parent")
    span = Span(
        context,
        "model.invoke",
        SpanKind.CLIENT,
        (
            ObservationAttribute("model", "provider/model"),
            ObservationAttribute("streaming", True),
        ),
    )

    assert span.context.trace_id == TraceId("trace-1")
    assert span.context.parent_span_id == SpanId("parent")
    assert tuple(SpanKind) == (
        SpanKind.INTERNAL,
        SpanKind.SERVER,
        SpanKind.CLIENT,
        SpanKind.PRODUCER,
        SpanKind.CONSUMER,
    )
    assert tuple(SpanStatus) == (SpanStatus.UNSET, SpanStatus.OK, SpanStatus.ERROR)
    with pytest.raises(FrozenInstanceError):
        span.name = "changed"  # type: ignore[misc]

    invalid_labels = (cast(str, 1), "", " padded", "line\nbreak", "x" * 257)
    for invalid in invalid_labels:
        with pytest.raises(ObservationContractError, match="short"):
            SpanContext(cast(TraceId, invalid), SpanId("span"))
    with pytest.raises(ObservationContractError, match="span id"):
        SpanContext(TraceId("trace"), SpanId(""))
    with pytest.raises(ObservationContractError, match="parent span"):
        SpanContext(TraceId("trace"), SpanId("span"), SpanId(""))
    with pytest.raises(ObservationContractError, match="own parent"):
        SpanContext(TraceId("trace"), SpanId("same"), SpanId("same"))

    with pytest.raises(ObservationContractError, match="attribute name"):
        ObservationAttribute("", "value")
    with pytest.raises(ObservationContractError, match="scalar"):
        ObservationAttribute("value", cast(ObservationValue, object()))
    with pytest.raises(ObservationContractError, match="finite"):
        ObservationAttribute("value", float("nan"))
    with pytest.raises(ObservationContractError, match="bounded"):
        ObservationAttribute("value", "line\nbreak")
    with pytest.raises(ObservationContractError, match="bounded"):
        ObservationAttribute("value", "x" * 4_097)

    with pytest.raises(ObservationContractError, match="SpanContext"):
        Span(cast(SpanContext, object()), "span")
    with pytest.raises(ObservationContractError, match="span name"):
        Span(_context(), "")
    with pytest.raises(ObservationContractError, match="SpanKind"):
        Span(_context(), "span", cast(SpanKind, "internal"))
    with pytest.raises(ObservationContractError, match="tuple"):
        Span(_context(), "span", attributes=cast(tuple[ObservationAttribute, ...], []))
    with pytest.raises(ObservationContractError, match="only ObservationAttribute"):
        Span(_context(), "span", attributes=(cast(ObservationAttribute, object()),))
    duplicate = ObservationAttribute("duplicate", "value")
    with pytest.raises(ObservationContractError, match="unique"):
        Span(_context(), "span", attributes=(duplicate, duplicate))


def test_usage_timing_error_and_lifecycle_records_are_strict() -> None:
    context = _context()
    normalized_error = ObservationError("provider.timeout", "request timed out", handled=True)
    input_usage = UsageMeasurement("input_tokens", 10, "token")
    output_usage = UsageMeasurement("output_tokens", 2.5, "token")
    usage = UsageRecord(context, (input_usage, output_usage))
    timing = TimingRecord(context, "model.latency", 100)
    error = ErrorRecord(context, normalized_error)
    started = SpanStarted(_span())
    finished = SpanFinished(context, SpanStatus.ERROR, 100, normalized_error)

    assert all(isinstance(item, ObservationRecord) for item in (usage, timing, error, started, finished))
    assert usage.measurements == (input_usage, output_usage)
    assert finished.error is normalized_error

    with pytest.raises(ObservationContractError, match="error category"):
        ObservationError("")
    for message in (cast(str, 1), "", " padded", "line\nbreak", "x" * 4_097):
        with pytest.raises(ObservationContractError, match="error message"):
            ObservationError("error", message)
    with pytest.raises(ObservationContractError, match="handled"):
        ObservationError("error", handled=cast(bool, 1))

    with pytest.raises(ObservationContractError, match="usage name"):
        UsageMeasurement("", 1, "request")
    for value in (cast(int | float, True), -1):
        with pytest.raises(ObservationContractError, match="non-negative"):
            UsageMeasurement("requests", value, "request")
    with pytest.raises(ObservationContractError, match="finite"):
        UsageMeasurement("requests", float("inf"), "request")
    with pytest.raises(ObservationContractError, match="usage unit"):
        UsageMeasurement("requests", 1, "")

    with pytest.raises(ObservationContractError, match="usage span"):
        UsageRecord(cast(SpanContext, object()), (input_usage,))
    with pytest.raises(ObservationContractError, match="non-empty tuple"):
        UsageRecord(context, ())
    with pytest.raises(ObservationContractError, match="non-empty tuple"):
        UsageRecord(context, cast(tuple[UsageMeasurement, ...], []))
    with pytest.raises(ObservationContractError, match="only UsageMeasurement"):
        UsageRecord(context, (cast(UsageMeasurement, object()),))
    with pytest.raises(ObservationContractError, match="unique"):
        UsageRecord(context, (input_usage, UsageMeasurement("input_tokens", 20, "token")))

    with pytest.raises(ObservationContractError, match="timing span"):
        TimingRecord(cast(SpanContext, object()), "timing", 1)
    with pytest.raises(ObservationContractError, match="timing name"):
        TimingRecord(context, "", 1)
    with pytest.raises(ObservationContractError, match="duration"):
        TimingRecord(context, "timing", -1)
    with pytest.raises(ObservationContractError, match="error span"):
        ErrorRecord(cast(SpanContext, object()), normalized_error)
    with pytest.raises(ObservationContractError, match="ObservationError"):
        ErrorRecord(context, cast(ObservationError, object()))
    with pytest.raises(ObservationContractError, match="contain a Span"):
        SpanStarted(cast(Span, object()))

    with pytest.raises(ObservationContractError, match="SpanContext"):
        SpanFinished(cast(SpanContext, object()), SpanStatus.OK, 1)
    with pytest.raises(ObservationContractError, match="SpanStatus"):
        SpanFinished(context, cast(SpanStatus, "ok"), 1)
    with pytest.raises(ObservationContractError, match="duration"):
        SpanFinished(context, SpanStatus.OK, cast(int, True))
    with pytest.raises(ObservationContractError, match="ObservationError"):
        SpanFinished(context, SpanStatus.ERROR, 1, cast(ObservationError, object()))
    with pytest.raises(ObservationContractError, match="must carry"):
        SpanFinished(context, SpanStatus.ERROR, 1)
    with pytest.raises(ObservationContractError, match="only an error"):
        SpanFinished(context, SpanStatus.OK, 1, normalized_error)


@pytest.mark.asyncio
async def test_observed_node_emits_one_start_and_finish_and_preserves_result() -> None:
    port = RecordingPort()
    factory_calls = 0

    def span_factory() -> Span:
        nonlocal factory_calls
        factory_calls += 1
        return _span(f"span-{factory_calls}")

    observed = ObservedNode(_echo, port, span_factory)

    assert await observed("first") == "first"
    assert await observed("second") == "second"
    assert factory_calls == 2
    assert tuple(type(item) for item in port.observations) == (
        SpanStarted,
        SpanFinished,
        SpanStarted,
        SpanFinished,
    )
    first_start = cast(SpanStarted, port.observations[0])
    first_finish = cast(SpanFinished, port.observations[1])
    second_start = cast(SpanStarted, port.observations[2])
    second_finish = cast(SpanFinished, port.observations[3])
    assert first_start.span.context == first_finish.span
    assert second_start.span.context == second_finish.span
    assert first_start.span.context != second_start.span.context
    assert first_finish.status is second_finish.status is SpanStatus.OK
    assert first_finish.error is second_finish.error is None
    assert first_finish.duration_ns >= 0


@pytest.mark.asyncio
async def test_observed_node_preserves_exception_and_cancellation_identity() -> None:
    class NodeError(RuntimeError):
        pass

    problem = NodeError("failed")
    failed_port = RecordingPort()

    async def fail(_value: str) -> str:
        raise problem

    with pytest.raises(NodeError) as failed:
        await ObservedNode(fail, failed_port, _span)("input")
    assert failed.value is problem
    assert len(failed_port.observations) == 2
    failed_finish = cast(SpanFinished, failed_port.observations[-1])
    assert failed_finish.status is SpanStatus.ERROR
    assert failed_finish.error == ObservationError("node.exception")

    cancellation = asyncio.CancelledError("node cancelled")
    cancelled_port = RecordingPort()

    async def cancel(_value: str) -> str:
        raise cancellation

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await ObservedNode(cancel, cancelled_port, _span)("input")
    assert cancelled.value is cancellation
    assert len(cancelled_port.observations) == 2
    cancelled_finish = cast(SpanFinished, cancelled_port.observations[-1])
    assert cancelled_finish.status is SpanStatus.ERROR
    assert cancelled_finish.error == ObservationError("node.cancelled")


@pytest.mark.asyncio
async def test_observation_port_failures_do_not_change_node_semantics() -> None:
    for port_error in (RuntimeError("port failed"), asyncio.CancelledError("port cancelled")):
        assert await ObservedNode(_echo, RaisingPort(port_error), _span)("value") == "value"


@pytest.mark.asyncio
async def test_observed_node_rejects_a_malformed_span_factory_before_calling_node() -> None:
    called = False

    async def operation(value: str) -> str:
        nonlocal called
        called = True
        return value

    def malformed_factory() -> Span:
        return cast(Span, object())

    with pytest.raises(ObservationContractError, match="span factory"):
        await ObservedNode(operation, RecordingPort(), malformed_factory)("value")
    assert not called


@pytest.mark.asyncio
async def test_observed_node_composes_with_the_public_graph_facade() -> None:
    port = RecordingPort()
    sequence = 0

    def span_factory() -> Span:
        nonlocal sequence
        sequence += 1
        return _span(f"graph-span-{sequence}")

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=f"observed:{values['value']}")

    graph = Graph[str]("observability.composed")
    graph.add_node(
        "work",
        ObservedNode(operation, port, span_factory),
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.set_outputs({"value": Graph.node_output("work", "value")})

    result = await graph.run(Graph.values(value="input"), run_id="observed-run")

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "observed:input"
    assert tuple(type(item) for item in port.observations) == (SpanStarted, SpanFinished)


def test_span_factory_and_observation_port_are_structural() -> None:
    factory: NodeSpanFactory = _span
    port = RecordingPort()
    port.record(SpanStarted(factory()))
    assert len(port.observations) == 1
