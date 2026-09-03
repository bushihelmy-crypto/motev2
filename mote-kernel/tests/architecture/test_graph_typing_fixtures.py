import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "typing_negative"


@dataclass(frozen=True, slots=True)
class NegativeTypingCase:
    filename: str
    expected_fragments: tuple[str, ...]


CASES = (
    NegativeTypingCase(
        "logging_node_none_sink.py",
        ('Argument of type "None"', 'parameter "sink"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "logging_node_async_sink.py",
        ("AsyncSink", 'parameter "sink"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "logging_node_non_awaitable.py",
        ("str", "Awaitable", 'parameter "inner"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "logging_node_none_inner.py",
        ('Argument of type "None"', 'parameter "inner"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "observed_node_none_port.py",
        ('Argument of type "None"', 'parameter "port"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "observed_node_none_factory.py",
        ('Argument of type "None"', 'parameter "span_factory"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "observed_node_async_port.py",
        ("AsyncPort", 'parameter "port"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "observed_node_non_awaitable.py",
        ("str", "Awaitable", 'parameter "inner"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "observed_node_none_inner.py",
        ('Argument of type "None"', 'parameter "inner"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "events_port_wrong_invocation.py",
        ("_WrongInvocation", 'parameter "invocation"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "commit_none_sink.py",
        ('Argument of type "None"', 'parameter "sink"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "logging_node_old_positional.py",
        ("Expected 1 positional argument", "reportCallIssue"),
    ),
    NegativeTypingCase(
        "logging_node_old_keyword.py",
        ('No parameter named "inner"', "reportCallIssue"),
    ),
    NegativeTypingCase(
        "logging_node_generic_subscript.py",
        ('Expected no type arguments for class "LoggedNode"', "reportInvalidTypeArguments"),
    ),
    NegativeTypingCase(
        "observed_node_old_positional.py",
        ("Expected 2 positional arguments", "reportCallIssue"),
    ),
    NegativeTypingCase(
        "observed_node_old_keyword.py",
        ('No parameter named "inner"', "reportCallIssue"),
    ),
    NegativeTypingCase(
        "observed_node_generic_subscript.py",
        ('Expected no type arguments for class "ObservedNode"', "reportInvalidTypeArguments"),
    ),
    NegativeTypingCase(
        "commit_old_positional.py",
        ("Expected 1 positional argument", "reportCallIssue"),
    ),
    NegativeTypingCase(
        "commit_old_keyword.py",
        ('No parameter named "inner"', "reportCallIssue"),
    ),
    NegativeTypingCase(
        "commit_generic_subscript.py",
        ('Expected no type arguments for class "LoggedGraphCommit"', "reportInvalidTypeArguments"),
    ),
    NegativeTypingCase(
        "commit_none_inner.py",
        ('Argument of type "None"', 'parameter "inner"', "reportArgumentType"),
    ),
    NegativeTypingCase(
        "constructor_values.py",
        ("Arguments missing for parameters", "_construction", "_seal", "reportCallIssue"),
    ),
    NegativeTypingCase(
        "constructor_success_outcome.py",
        ("Argument missing for parameter", "_seal", "reportCallIssue"),
    ),
    NegativeTypingCase(
        "constructor_continuation.py",
        ("Arguments missing for parameters", "_snapshot", "_seal", "reportCallIssue"),
    ),
    NegativeTypingCase(
        "constructor_completed_result.py",
        (
            "Arguments missing for parameters",
            "state",
            "continuation",
            "outputs",
            "_seal",
            "reportCallIssue",
        ),
    ),
    NegativeTypingCase(
        "invariant_result.py",
        ('"Result[Dog]"', '"Result[Animal]"', "is invariant", "reportReturnType"),
    ),
    NegativeTypingCase(
        "invariant_continuation.py",
        ('"_GraphContinuation[Dog]"', '"_GraphContinuation[Animal]"', "is invariant", "reportReturnType"),
    ),
    NegativeTypingCase(
        "invariant_partial_commit.py",
        ('"_PartialCommitError[Dog]"', '"_PartialCommitError[Animal]"', "is invariant", "reportReturnType"),
    ),
    NegativeTypingCase(
        "invariant_completed_result.py",
        (
            '"_CompletedGraphResult[Dog]"',
            '"_CompletedGraphResult[Animal]"',
            "is invariant",
            "reportReturnType",
        ),
    ),
    NegativeTypingCase(
        "invariant_transition.py",
        (
            '"GraphTransition[UniverseA]"',
            '"GraphTransition[UniverseB]"',
            "is invariant",
            "reportReturnType",
        ),
    ),
    NegativeTypingCase(
        "cross_universe_commit.py",
        (
            'parameter "commit"',
            '"GraphTransition[UniverseB]"',
            '"GraphTransition[UniverseA]"',
            "is invariant",
            "reportArgumentType",
        ),
    ),
    NegativeTypingCase(
        "cross_universe_resume_action.py",
        ("ResumeAction", "UniverseA", "UniverseB", "reportArgumentType"),
    ),
    NegativeTypingCase(
        "invariant_success_result.py",
        (
            '"_GraphSuccessResult[UniverseA]"',
            '"_GraphSuccessResult[UniverseB]"',
            "is invariant",
            "reportReturnType",
        ),
    ),
    NegativeTypingCase(
        "never_success_outcome.py",
        ("SuccessOutcome", "Never", "PipelineValue", "is invariant", "reportArgumentType"),
    ),
    NegativeTypingCase(
        "cross_universe.py",
        ('"_GraphValues[str]" is not assignable to "_GraphValues[int]"',),
    ),
)


def _pyright(filename: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            "pyright",
            "--project",
            str(FIXTURE_ROOT / "pyrightconfig.json"),
            str(FIXTURE_ROOT / filename),
        ),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.filename)
def test_invalid_public_generic_programs_remain_rejected(case: NegativeTypingCase) -> None:
    completed = _pyright(case.filename)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert completed.stdout.count(" - error:") == 1, completed.stdout
    assert "1 error, 0 warnings, 0 informations" in completed.stdout, completed.stdout
    assert all(fragment in completed.stdout for fragment in case.expected_fragments), completed.stdout


def test_factory_inference_is_exact_and_contains_no_unknown() -> None:
    completed = _pyright("factory_inference.py")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Unknown" not in completed.stdout


def test_logging_observability_positive_fixture_is_exact_and_contains_no_unknown() -> None:
    completed = subprocess.run(
        ("pyright", "tests/typing_positive/logging_observability.py"),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Unknown" not in completed.stdout


def test_events_port_positive_fixture_is_exact_and_contains_no_unknown() -> None:
    completed = subprocess.run(
        ("pyright", "tests/typing_positive/events_port.py"),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Unknown" not in completed.stdout
