"""Deterministic tests for logging values and both diagnostic decorators."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from dataclasses import FrozenInstanceError, dataclass, field, is_dataclass, replace
from typing import cast

import pytest

import mote_kernel.logging as logging_package
from mote_kernel.execution import Graph
from mote_kernel.logging import LoggedGraphCommit, LoggedNode
from mote_kernel.logging.level import LogLevel
from mote_kernel.logging.node import NodeLogFields
from mote_kernel.logging.record import LogContractError, LogField, LogRecord, LogValue


@dataclass
class RecordingSink:
    records: list[LogRecord] = field(default_factory=lambda: list[LogRecord]())

    def write(self, record: LogRecord, /) -> None:
        self.records.append(record)


@dataclass(frozen=True)
class RaisingSink:
    error: BaseException

    def write(self, _record: LogRecord, /) -> None:
        raise self.error


@dataclass(frozen=True)
class RaisingFields:
    error: BaseException

    def __call__(self) -> tuple[LogField, ...]:
        raise self.error


@dataclass(frozen=True, slots=True)
class DeferredString:
    value: str

    def __await__(self) -> Generator[None, None, str]:
        yield from ()
        return self.value


def _fields(record: LogRecord) -> dict[str, LogValue]:
    return {field.name: field.value for field in record.fields}


async def _echo(value: str) -> str:
    return value


def _awaitable_echo(value: str) -> DeferredString:
    return DeferredString(value)


async def _capture_transitions(
    *,
    nested: bool = False,
    run_id: str = "logging-run",
) -> tuple[Graph.Transition[str], ...]:
    transitions: list[Graph.Transition[str]] = []

    async def capture(transition: Graph.Transition[str], /) -> Graph.State:
        transitions.append(transition)
        return transition.candidate_state

    async def empty(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    if nested:
        child = Graph[str]("logging.child")
        child.add_node("leaf", empty, inputs={}, outputs={})
        child.set_outputs({})
        graph = Graph[str]("logging.parent")
        graph.add_node("child", child, inputs={})
    else:
        graph = Graph[str]("logging.transition")
        graph.add_node("node", empty, inputs={}, outputs={})
    graph.set_outputs({})
    result = await graph.run(Graph.values(), run_id=run_id, commit=capture)
    assert isinstance(result, Graph.CompletedResult)
    return tuple(transitions)


def test_logging_root_exposes_only_the_two_decorators() -> None:
    assert logging_package.__all__ == ["LoggedGraphCommit", "LoggedNode"]
    assert logging_package.LoggedGraphCommit is LoggedGraphCommit
    assert logging_package.LoggedNode is LoggedNode
    assert not hasattr(logging_package, "LogRecord")
    assert not hasattr(logging_package, "LogSinkPort")
    assert not hasattr(logging_package, "logged_node")


def test_logging_decorators_are_non_generic_frozen_slot_configurations() -> None:
    assert is_dataclass(LoggedNode)
    assert is_dataclass(LoggedGraphCommit)
    assert not getattr(LoggedNode, "__parameters__", ())
    assert not getattr(LoggedGraphCommit, "__parameters__", ())
    assert "__dict__" not in LoggedNode.__slots__
    assert "__dict__" not in LoggedGraphCommit.__slots__
    node_config = LoggedNode(RecordingSink())
    commit_config = LoggedGraphCommit(RecordingSink())
    with pytest.raises(FrozenInstanceError):
        node_config.sink = RecordingSink()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        commit_config.sink = RecordingSink()  # type: ignore[misc]


def test_log_values_are_frozen_structured_and_bounded() -> None:
    field_value = LogField("run_id", "run-1")
    record = LogRecord(LogLevel.INFO, "node.finished", "finished", (field_value,))

    assert record.fields == (field_value,)
    assert tuple(LogLevel) == (
        LogLevel.DEBUG,
        LogLevel.INFO,
        LogLevel.WARNING,
        LogLevel.ERROR,
        LogLevel.CRITICAL,
    )
    with pytest.raises(FrozenInstanceError):
        record.event = "changed"  # type: ignore[misc]

    invalid_labels = (cast(str, 1), "", " padded", "line\nbreak", "x" * 129)
    for invalid in invalid_labels:
        with pytest.raises(LogContractError, match="short"):
            LogField(invalid, "value")
    with pytest.raises(LogContractError, match="scalar"):
        LogField("value", cast(LogValue, object()))
    with pytest.raises(LogContractError, match="finite"):
        LogField("value", float("inf"))
    with pytest.raises(LogContractError, match="bounded"):
        LogField("value", "line\nbreak")
    with pytest.raises(LogContractError, match="bounded"):
        LogField("value", "x" * 4_097)
    with pytest.raises(LogContractError, match="level"):
        LogRecord(cast(LogLevel, "info"), "event")
    with pytest.raises(LogContractError, match="log event"):
        LogRecord(LogLevel.INFO, "")
    for message in (cast(str, 1), "", " padded", "line\nbreak", "x" * 4_097):
        with pytest.raises(LogContractError, match="message"):
            LogRecord(LogLevel.INFO, "event", message)
    with pytest.raises(LogContractError, match="tuple"):
        LogRecord(LogLevel.INFO, "event", fields=cast(tuple[LogField, ...], []))
    with pytest.raises(LogContractError, match="only LogField"):
        LogRecord(LogLevel.INFO, "event", fields=(cast(LogField, object()),))
    with pytest.raises(LogContractError, match="unique"):
        LogRecord(LogLevel.INFO, "event", fields=(field_value, field_value))


@pytest.mark.asyncio
async def test_logged_node_uses_one_invocation_field_snapshot_and_preserves_result() -> None:
    sink = RecordingSink()
    context = ["run-1"]
    factory_calls = 0

    def invocation_fields() -> tuple[LogField, ...]:
        nonlocal factory_calls
        factory_calls += 1
        return (LogField("run_id", context[0]),)

    @LoggedNode(
        sink,
        event="role.node",
        fields=(LogField("node_id", "think"),),
        fields_factory=invocation_fields,
    )
    async def operation(value: str) -> str:
        context[0] = "changed-inside-node"
        return f"done:{value}"

    assert await operation("input") == "done:input"
    assert factory_calls == 1
    assert tuple(record.event for record in sink.records) == ("role.node.started", "role.node.finished")
    assert tuple(record.level for record in sink.records) == (LogLevel.DEBUG, LogLevel.INFO)
    assert _fields(sink.records[0]) == {"node_id": "think", "run_id": "run-1"}
    finished = _fields(sink.records[1])
    assert finished["node_id"] == "think"
    assert finished["run_id"] == "run-1"
    assert finished["outcome"] == "ok"
    assert type(finished["duration_ns"]) is int and finished["duration_ns"] >= 0


@pytest.mark.asyncio
async def test_logged_node_preserves_ordinary_exception_and_cancellation() -> None:
    class NodeError(RuntimeError):
        pass

    problem = NodeError("failed")
    failure_sink = RecordingSink()

    async def fail(_value: str) -> str:
        raise problem

    with pytest.raises(NodeError) as failed:
        await LoggedNode(failure_sink)(fail)("input")
    assert failed.value is problem
    assert tuple(record.event for record in failure_sink.records) == ("node.started", "node.failed")
    failure_fields = _fields(failure_sink.records[-1])
    assert failure_fields["outcome"] == "error"
    assert failure_fields["error_type"] == "NodeError"

    cancellation = asyncio.CancelledError("node cancelled")
    cancellation_sink = RecordingSink()

    async def cancel(_value: str) -> str:
        raise cancellation

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await LoggedNode(cancellation_sink)(cancel)("input")
    assert cancelled.value is cancellation
    assert tuple(record.event for record in cancellation_sink.records) == ("node.started", "node.cancelled")
    cancellation_fields = _fields(cancellation_sink.records[-1])
    assert cancellation_fields["outcome"] == "cancelled"
    assert cancellation_fields["error_type"] == "CancelledError"


@pytest.mark.asyncio
async def test_logged_node_accepts_a_general_awaitable() -> None:
    sink = RecordingSink()

    assert await LoggedNode(sink)(_awaitable_echo)("input") == "input"
    assert tuple(record.event for record in sink.records) == ("node.started", "node.finished")


@pytest.mark.asyncio
async def test_node_logging_failures_are_best_effort() -> None:
    for sink_error in (RuntimeError("sink failed"), asyncio.CancelledError("sink cancelled")):
        assert await LoggedNode(RaisingSink(sink_error))(_echo)("value") == "value"

    factory_error = RuntimeError("fields failed")
    sink = RecordingSink()
    logged = LoggedNode(
        sink,
        fields=(LogField("node_id", "node"),),
        fields_factory=RaisingFields(factory_error),
    )(_echo)
    assert await logged("value") == "value"
    assert all(_fields(record)["node_id"] == "node" for record in sink.records)

    cancellation = asyncio.CancelledError("fields cancelled")
    cancellation_calls = 0

    async def counted_echo(value: str) -> str:
        nonlocal cancellation_calls
        cancellation_calls += 1
        return value

    with pytest.raises(asyncio.CancelledError) as fields_cancelled:
        sink = RecordingSink()
        logged = LoggedNode(
            sink,
            fields=(LogField("node_id", "node"),),
            fields_factory=RaisingFields(cancellation),
        )(counted_echo)
        await logged("value")
    assert fields_cancelled.value is cancellation
    assert cancellation_calls == 0

    malformed_sink = RecordingSink()

    def malformed_fields() -> tuple[LogField, ...]:
        return cast(tuple[LogField, ...], [])

    malformed = LoggedNode(malformed_sink, fields_factory=malformed_fields)(_echo)
    assert await malformed("value") == "value"
    assert _fields(malformed_sink.records[0]) == {}

    duplicate_sink = RecordingSink()

    def duplicate_fields() -> tuple[LogField, ...]:
        return (LogField("node_id", "dynamic"),)

    duplicate = LoggedNode(
        duplicate_sink,
        fields=(LogField("node_id", "fixed"),),
        fields_factory=duplicate_fields,
    )(_echo)
    assert await duplicate("value") == "value"
    assert _fields(duplicate_sink.records[0]) == {"node_id": "fixed"}

    reserved_sink = RecordingSink()

    def reserved_fields() -> tuple[LogField, ...]:
        return (LogField("outcome", "caller"),)

    reserved = LoggedNode(reserved_sink, fields_factory=reserved_fields)(_echo)
    assert await reserved("value") == "value"
    assert _fields(reserved_sink.records[0]) == {}
    assert _fields(reserved_sink.records[-1])["outcome"] == "ok"


def test_logged_node_rejects_invalid_static_configuration() -> None:
    sink = RecordingSink()
    with pytest.raises(LogContractError, match="node log event"):
        LoggedNode(sink, event="")
    with pytest.raises(LogContractError, match="node log event"):
        LoggedNode(sink, event="x" * 119)
    with pytest.raises(LogContractError, match="tuple"):
        LoggedNode(sink, fields=cast(tuple[LogField, ...], []))
    with pytest.raises(LogContractError, match="LogField"):
        LoggedNode(sink, fields=(cast(LogField, object()),))
    with pytest.raises(LogContractError, match="unique"):
        LoggedNode(sink, fields=(LogField("node_id", "a"), LogField("node_id", "b")))
    for reserved in ("duration_ns", "error_type", "outcome"):
        with pytest.raises(LogContractError, match="lifecycle"):
            LoggedNode(sink, fields=(LogField(reserved, "reserved"),))


@pytest.mark.asyncio
async def test_logged_commit_reports_exact_return_and_transition_coordinates() -> None:
    transitions = await _capture_transitions(nested=True)
    root = transitions[0]
    nested = next(transition for transition in transitions if transition.scope)
    sink = RecordingSink()

    @LoggedGraphCommit(sink, event="graph.commit")
    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        return transition.candidate_state

    root_result = await commit(root)
    nested_result = await commit(nested)

    assert root_result is root.candidate_state
    assert nested_result is nested.candidate_state
    assert tuple(record.event for record in sink.records) == (
        "graph.commit.started",
        "graph.commit.accepted",
        "graph.commit.started",
        "graph.commit.accepted",
    )
    root_fields = _fields(sink.records[0])
    assert root_fields["run_id"] == str(root.candidate_state.run_id)
    assert root_fields["scope"] is None
    assert root_fields["scope_depth"] == 0
    assert root_fields["command_type"] == type(root.command).__name__
    assert root_fields["previous_revision"] is None
    nested_fields = _fields(sink.records[2])
    assert nested_fields["scope"] == "child"
    assert nested_fields["scope_depth"] == 1
    accepted = _fields(sink.records[-1])
    assert accepted["outcome"] == "accepted"
    assert type(accepted["duration_ns"]) is int


@pytest.mark.asyncio
async def test_logged_commit_calls_inner_once_and_leaves_mismatch_for_graph_owner() -> None:
    transition = (await _capture_transitions())[1]
    calls: list[Graph.Transition[str]] = []

    async def exact(received: Graph.Transition[str], /) -> Graph.State:
        calls.append(received)
        return received.candidate_state

    exact_sink = RecordingSink()
    confirmed = await LoggedGraphCommit(exact_sink)(exact)(transition)
    assert confirmed is transition.candidate_state
    assert calls == [transition]

    async def mismatch(received: Graph.Transition[str], /) -> Graph.State:
        return replace(received.candidate_state, revision=received.candidate_state.revision + 100)

    mismatch_sink = RecordingSink()
    returned = await LoggedGraphCommit(mismatch_sink)(mismatch)(transition)
    assert returned != transition.candidate_state
    assert mismatch_sink.records[-1].event == "commit.mismatch"
    assert mismatch_sink.records[-1].level is LogLevel.ERROR
    assert _fields(mismatch_sink.records[-1])["outcome"] == "mismatch"


@pytest.mark.asyncio
async def test_unrepresentable_transition_fields_do_not_prevent_inner_commit() -> None:
    transition = (await _capture_transitions(run_id="r" * 4_097))[0]
    calls: list[Graph.Transition[str]] = []

    async def exact(received: Graph.Transition[str], /) -> Graph.State:
        calls.append(received)
        return received.candidate_state

    sink = RecordingSink()
    returned = await LoggedGraphCommit(sink)(exact)(transition)

    assert returned is transition.candidate_state
    assert calls == [transition]
    assert tuple(record.event for record in sink.records) == ("commit.started", "commit.accepted")
    assert _fields(sink.records[0]) == {}
    assert _fields(sink.records[-1])["outcome"] == "accepted"


@pytest.mark.asyncio
async def test_cancelled_transition_projection_isolated_from_commit_inner() -> None:
    transition = (await _capture_transitions())[0]
    projection_cancellation = asyncio.CancelledError("projection cancelled")

    class ProjectionCancelledTransition:
        @property
        def previous_state(self) -> Graph.State | None:
            raise projection_cancellation

        @property
        def candidate_state(self) -> Graph.State:
            return transition.candidate_state

        @property
        def scope(self) -> tuple[str, ...]:
            return transition.scope

        @property
        def command(self):
            return transition.command

    projected = cast(Graph.Transition[str], ProjectionCancelledTransition())
    calls = 0

    async def exact(received: Graph.Transition[str], /) -> Graph.State:
        nonlocal calls
        calls += 1
        return received.candidate_state

    sink = RecordingSink()
    returned = await LoggedGraphCommit(sink)(exact)(projected)

    assert returned is transition.candidate_state
    assert calls == 1
    assert tuple(record.event for record in sink.records) == ("commit.started", "commit.accepted")


@pytest.mark.asyncio
async def test_commit_transition_candidate_is_read_once_for_projection_and_classification() -> None:
    transition = (await _capture_transitions())[0]
    projection_cancellation = asyncio.CancelledError("candidate read twice")
    candidate_reads = 0

    class CandidateReadOnceTransition:
        @property
        def previous_state(self) -> Graph.State | None:
            return transition.previous_state

        @property
        def candidate_state(self) -> Graph.State:
            nonlocal candidate_reads
            candidate_reads += 1
            if candidate_reads > 1:
                raise projection_cancellation
            return transition.candidate_state

        @property
        def scope(self) -> tuple[str, ...]:
            return transition.scope

        @property
        def command(self):
            return transition.command

    projected = cast(Graph.Transition[str], CandidateReadOnceTransition())

    async def exact(_received: Graph.Transition[str], /) -> Graph.State:
        return transition.candidate_state

    sink = RecordingSink()
    returned = await LoggedGraphCommit(sink)(exact)(projected)

    assert returned is transition.candidate_state
    assert candidate_reads == 1
    assert tuple(record.event for record in sink.records) == ("commit.started", "commit.accepted")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "projection_error",
    (RuntimeError("candidate projection failed"), asyncio.CancelledError("candidate projection cancelled")),
    ids=("ordinary-error", "cancelled-error"),
)
async def test_commit_candidate_projection_failure_does_not_block_inner(projection_error: BaseException) -> None:
    transition = (await _capture_transitions())[0]
    calls = 0

    class FailedCandidateTransition:
        @property
        def previous_state(self) -> Graph.State | None:
            return transition.previous_state

        @property
        def candidate_state(self) -> Graph.State:
            raise projection_error

        @property
        def scope(self) -> tuple[str, ...]:
            return transition.scope

        @property
        def command(self):
            return transition.command

    projected = cast(Graph.Transition[str], FailedCandidateTransition())

    async def inner(_received: Graph.Transition[str], /) -> Graph.State:
        nonlocal calls
        calls += 1
        return transition.candidate_state

    sink = RecordingSink()
    returned = await LoggedGraphCommit(sink)(inner)(projected)

    assert returned is transition.candidate_state
    assert calls == 1
    assert tuple(record.event for record in sink.records) == ("commit.started", "commit.mismatch")


@pytest.mark.asyncio
async def test_commit_invalid_candidate_projection_does_not_block_inner() -> None:
    transition = (await _capture_transitions())[0]
    calls = 0

    class InvalidCandidateTransition:
        @property
        def previous_state(self) -> Graph.State | None:
            return transition.previous_state

        @property
        def candidate_state(self) -> Graph.State:
            return cast(Graph.State, object())

        @property
        def scope(self) -> tuple[str, ...]:
            return transition.scope

        @property
        def command(self):
            return transition.command

    projected = cast(Graph.Transition[str], InvalidCandidateTransition())

    async def inner(_received: Graph.Transition[str], /) -> Graph.State:
        nonlocal calls
        calls += 1
        return transition.candidate_state

    sink = RecordingSink()
    returned = await LoggedGraphCommit(sink)(inner)(projected)

    assert returned is transition.candidate_state
    assert calls == 1
    assert tuple(record.event for record in sink.records) == ("commit.started", "commit.mismatch")


@pytest.mark.asyncio
async def test_logged_commit_preserves_inner_error_and_cancellation() -> None:
    transition = (await _capture_transitions())[0]

    class CommitError(RuntimeError):
        pass

    problem = CommitError("failed")

    async def fail(_transition: Graph.Transition[str], /) -> Graph.State:
        raise problem

    failed_sink = RecordingSink()
    with pytest.raises(CommitError) as failed:
        await LoggedGraphCommit(failed_sink)(fail)(transition)
    assert failed.value is problem
    assert failed_sink.records[-1].event == "commit.failed"
    assert _fields(failed_sink.records[-1])["error_type"] == "CommitError"

    cancellation = asyncio.CancelledError("cancelled")

    async def cancel(_transition: Graph.Transition[str], /) -> Graph.State:
        raise cancellation

    cancelled_sink = RecordingSink()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await LoggedGraphCommit(cancelled_sink)(cancel)(transition)
    assert cancelled.value is cancellation
    assert cancelled_sink.records[-1].event == "commit.cancelled"
    assert _fields(cancelled_sink.records[-1])["error_type"] == "CancelledError"


@pytest.mark.asyncio
async def test_unrepresentable_error_diagnostics_preserve_wrapped_error_identity() -> None:
    error_type = cast(type[RuntimeError], type("E" * 4_097, (RuntimeError,), {}))
    problem = error_type("failed")

    async def fail_node(_value: str) -> str:
        raise problem

    with pytest.raises(RuntimeError) as node_failure:
        await LoggedNode(RecordingSink())(fail_node)("input")
    assert node_failure.value is problem

    transition = (await _capture_transitions())[0]

    async def fail_commit(_transition: Graph.Transition[str], /) -> Graph.State:
        raise problem

    with pytest.raises(RuntimeError) as commit_failure:
        await LoggedGraphCommit(RecordingSink())(fail_commit)(transition)
    assert commit_failure.value is problem


@pytest.mark.asyncio
async def test_commit_logging_sink_failure_does_not_change_inner_result() -> None:
    transition = (await _capture_transitions())[0]
    calls = 0

    async def exact(received: Graph.Transition[str], /) -> Graph.State:
        nonlocal calls
        calls += 1
        return received.candidate_state

    for sink_error in (RuntimeError("sink failed"), asyncio.CancelledError("sink cancelled")):
        returned = await LoggedGraphCommit(RaisingSink(sink_error))(exact)(transition)
        assert returned is transition.candidate_state
    assert calls == 2

    async def mismatch(received: Graph.Transition[str], /) -> Graph.State:
        return replace(received.candidate_state, revision=received.candidate_state.revision + 1)

    mismatched = await LoggedGraphCommit(RaisingSink(RuntimeError("sink failed")))(mismatch)(transition)
    assert mismatched != transition.candidate_state

    with pytest.raises(LogContractError, match="commit log event"):
        LoggedGraphCommit(RecordingSink(), event="")
    with pytest.raises(LogContractError, match="commit log event"):
        LoggedGraphCommit(RecordingSink(), event="x" * 119)


@pytest.mark.asyncio
async def test_logging_sink_failures_do_not_replace_inner_primary_errors() -> None:
    class NodeError(RuntimeError):
        pass

    node_problem = NodeError("node failed")

    async def fail_node(_value: str) -> str:
        raise node_problem

    with pytest.raises(NodeError) as node_failure:
        await LoggedNode(RaisingSink(asyncio.CancelledError("diagnostic cancelled")))(fail_node)("value")
    assert node_failure.value is node_problem

    transition = (await _capture_transitions())[0]
    commit_problem = NodeError("commit failed")

    async def fail_commit(_transition: Graph.Transition[str], /) -> Graph.State:
        raise commit_problem

    with pytest.raises(NodeError) as commit_failure:
        await LoggedGraphCommit(RaisingSink(asyncio.CancelledError("diagnostic cancelled")))(fail_commit)(transition)
    assert commit_failure.value is commit_problem

    cancellation = asyncio.CancelledError("business cancellation")

    async def cancel_node(_value: str) -> str:
        raise cancellation

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await LoggedNode(RaisingSink(asyncio.CancelledError("diagnostic cancelled")))(cancel_node)("value")
    assert cancelled.value is cancellation


@pytest.mark.asyncio
async def test_both_logging_decorators_compose_with_the_public_graph_facade() -> None:
    sink = RecordingSink()

    @LoggedNode(sink, fields=(LogField("node_id", "work"),))
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=f"logged:{values['value']}")

    graph = Graph[str]("logging.composed")
    graph.add_node(
        "work",
        operation,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.set_outputs({"value": Graph.node_output("work", "value")})

    @LoggedGraphCommit(sink)
    async def persistence_commit(transition: Graph.Transition[str], /) -> Graph.State:
        return transition.candidate_state

    result = await graph.run(
        Graph.values(value="input"),
        run_id="composed-run",
        commit=persistence_commit,
    )

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "logged:input"
    events = tuple(record.event for record in sink.records)
    assert "node.started" in events
    assert "node.finished" in events
    assert "commit.started" in events
    assert "commit.accepted" in events


@pytest.mark.asyncio
async def test_graph_run_without_commit_uses_execution_fallback() -> None:
    sink = RecordingSink()

    @LoggedNode(sink)
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=f"fallback:{values['value']}")

    graph = Graph[str]("logging.fallback")
    graph.add_node(
        "work",
        operation,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.set_outputs({"value": Graph.node_output("work", "value")})

    result = await graph.run(Graph.values(value="input"), run_id="fallback-run", commit=None)

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "fallback:input"
    assert tuple(record.event for record in sink.records) == ("node.started", "node.finished")


def test_node_fields_protocol_is_structural() -> None:
    def fields() -> tuple[LogField, ...]:
        return (LogField("node_id", "node"),)

    structural: NodeLogFields = fields
    assert structural() == (LogField("node_id", "node"),)
