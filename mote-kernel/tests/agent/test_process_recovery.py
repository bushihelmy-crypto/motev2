import json
import os
import pickle
import signal
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from tests.agent.persistence_fixtures import ProcessCommitRecord
from tests.agent.subprocess_worker import (
    PROCESS_CRASH_EXIT,
    RUNTIME_CRASH_EXIT,
    CrashBoundary,
    Phase,
    ProcessAuthority,
    ProcessPersistence,
    RequestMode,
    Scenario,
)
from tests.execution.persistence_fixtures import STRING_CODEC, linear_graph

from mote_kernel.agent import Agent, AgentCompleted, AgentResume, AgentStart
from mote_kernel.execution import Graph
from mote_kernel.execution.graph_result import GraphInterruptView
from mote_kernel.execution.persistence import GraphPersistenceCommit
from mote_kernel.persistence import NeverCreated
from mote_kernel.session import AgentSession, AgentSessionCodec


@dataclass(frozen=True, slots=True)
class ProcessCase:
    scenario: Scenario
    result: str
    target_scope: tuple[str, ...]
    target_node: str
    before_target_count: int
    after_target_count: int
    before_calls: tuple[str, ...]
    after_calls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CallRecord:
    phase: Phase
    node: str
    details: tuple[str, ...]
    process_id: int


PROCESS_CASES = (
    ProcessCase(
        Scenario.LINEAR,
        "input-first-second",
        (),
        "first",
        0,
        1,
        ("first", "first", "second"),
        ("first", "second"),
    ),
    ProcessCase(
        Scenario.FRONTIER,
        "input-left|input-right",
        (),
        "left",
        0,
        1,
        ("left", "left", "right", "join"),
        ("left", "right", "join"),
    ),
    ProcessCase(
        Scenario.LOOP_JOIN,
        "L2|R2",
        (),
        "loop",
        1,
        2,
        ("initialize", "loop", "loop", "loop", "fanout", "left", "right", "join"),
        ("initialize", "loop", "loop", "fanout", "left", "right", "join"),
    ),
    ProcessCase(
        Scenario.NESTED_CONFIG,
        "input-observed-middle-root",
        ("middle",),
        "leaf",
        0,
        1,
        ("observe", "consume", "middle-tail", "root-tail"),
        ("observe", "consume", "middle-tail", "root-tail"),
    ),
)


def _session_codec() -> AgentSessionCodec[str, str]:
    def encode(hook_state: str, context: str) -> bytes:
        return json.dumps((hook_state, context), separators=(",", ":")).encode()

    def decode(payload: bytes) -> tuple[str, str]:
        decoded: object = json.loads(payload)
        if type(decoded) is not list:
            raise ValueError("process session payload must be a JSON list")
        values = cast(list[object], decoded)
        if len(values) != 2 or any(type(value) is not str for value in values):
            raise ValueError("process session payload must contain two strings")
        return cast(str, values[0]), cast(str, values[1])

    return AgentSessionCodec("process-session", 1, encode, decode)


def _root() -> Path:
    return Path(__file__).parents[2]


def _environment() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(_root() / "src")}


def _command(
    scenario: Scenario,
    phase: Phase,
    boundary: CrashBoundary,
    directory: Path,
    *,
    agent_id: str | None = None,
    request_mode: RequestMode = RequestMode.DEFAULT,
    run_id: str | None = None,
    value: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "tests.agent.subprocess_worker",
        scenario,
        phase,
        boundary,
        str(directory),
    ]
    if request_mode is not RequestMode.DEFAULT:
        if agent_id is None:
            raise ValueError("custom process requests require an Agent identity")
        command.extend((agent_id, request_mode))
        if request_mode in (RequestMode.START, RequestMode.RESUME_EXACT):
            if run_id is None:
                raise ValueError("this process request requires a run ID")
            command.append(run_id)
        if request_mode is RequestMode.START:
            if value is None:
                raise ValueError("a process start requires an input value")
            command.append(value)
    return command


def _run(
    scenario: Scenario,
    phase: Phase,
    boundary: CrashBoundary,
    directory: Path,
    *,
    agent_id: str | None = None,
    request_mode: RequestMode = RequestMode.DEFAULT,
    run_id: str | None = None,
    value: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(
            scenario,
            phase,
            boundary,
            directory,
            agent_id=agent_id,
            request_mode=request_mode,
            run_id=run_id,
            value=value,
        ),
        cwd=_root(),
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _commits(directory: Path) -> tuple[GraphPersistenceCommit[str], ...]:
    decoded: object = pickle.loads((directory / "commits.pickle").read_bytes())
    assert type(decoded) is tuple
    decoded_records = cast(tuple[object, ...], decoded)
    assert all(type(item) is ProcessCommitRecord for item in decoded_records)
    records = cast(tuple[ProcessCommitRecord[str], ...], decoded_records)
    return tuple(record.request for record in records)


def _calls(directory: Path) -> tuple[CallRecord, ...]:
    records: list[CallRecord] = []
    for line in (directory / "calls.log").read_text().splitlines():
        phase, node, *details, process_id = line.split("|")
        records.append(CallRecord(Phase(phase), node, tuple(details), int(process_id)))
    return tuple(records)


def _target_count(commits: tuple[GraphPersistenceCommit[str], ...], case: ProcessCase) -> int:
    return sum(
        record.coordinate.activation.scope_run.scope == case.target_scope
        and record.coordinate.activation.node_id == case.target_node
        for commit in commits
        for record in commit.writes.publications
    )


def _interrupts(directory: Path) -> tuple[GraphInterruptView, ...]:
    decoded: object = pickle.loads((directory / "interrupts.pickle").read_bytes())
    assert type(decoded) is tuple
    decoded_interrupts = cast(tuple[object, ...], decoded)
    assert all(type(interrupt) is GraphInterruptView for interrupt in decoded_interrupts)
    return cast(tuple[GraphInterruptView, ...], decoded_interrupts)


def _assert_completed_process(process: subprocess.CompletedProcess[str]) -> None:
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.parametrize("case", PROCESS_CASES, ids=lambda case: case.scenario)
@pytest.mark.parametrize(
    "boundary",
    (CrashBoundary.BEFORE_WRITE, CrashBoundary.AFTER_WRITE),
    ids=lambda boundary: boundary,
)
def test_combined_topologies_recover_across_real_process_exit(
    tmp_path: Path,
    case: ProcessCase,
    boundary: CrashBoundary,
) -> None:
    capture = _run(case.scenario, Phase.CAPTURE, boundary, tmp_path)
    assert capture.returncode == PROCESS_CRASH_EXIT, capture.stdout + capture.stderr
    captured_commits = _commits(tmp_path)
    expected_target_count = (
        case.before_target_count if boundary is CrashBoundary.BEFORE_WRITE else case.after_target_count
    )
    assert _target_count(captured_commits, case) == expected_target_count
    assert not tuple(tmp_path.glob("*.pending"))

    recovered = _run(case.scenario, Phase.RECOVER, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(recovered)
    assert (tmp_path / "result").read_text() == case.result
    recovered_journal = (tmp_path / "commits.pickle").read_bytes()
    recovered_calls = _calls(tmp_path)
    expected_calls = case.before_calls if boundary is CrashBoundary.BEFORE_WRITE else case.after_calls
    assert Counter(record.node for record in recovered_calls) == Counter(expected_calls)
    assert {record.phase for record in recovered_calls} == {Phase.CAPTURE, Phase.RECOVER}
    assert len({record.process_id for record in recovered_calls}) == 2
    if case.scenario is Scenario.LOOP_JOIN:
        expected_repeated = 2 if boundary is CrashBoundary.BEFORE_WRITE else 1
        loop_calls = Counter((record.node, record.details) for record in recovered_calls)
        assert loop_calls[("loop", ("1",))] == expected_repeated

    replayed = _run(case.scenario, Phase.REPLAY, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(replayed)
    assert (tmp_path / "result").read_text() == case.result
    assert _calls(tmp_path) == recovered_calls
    assert (tmp_path / "commits.pickle").read_bytes() == recovered_journal
    assert (tmp_path / "generation").read_text() == "3"

    if case.scenario is Scenario.NESTED_CONFIG:
        config_records = tuple(line.split("|") for line in (tmp_path / "config.log").read_text().splitlines())
        for phase in (Phase.RECOVER, Phase.REPLAY):
            loaded = {int(parts[2]) for parts in config_records if parts[:2] == ["load", phase]}
            assert loaded == {1, 2}
        assert all(parts[2] != "9" for parts in config_records if parts[0] == "load")
        root_commits = tuple(commit for commit in _commits(tmp_path) if not commit.scope)
        assert root_commits[-1].candidate_state.config_revision == 2


def test_recovery_rejects_a_missing_nested_config_before_nodes_or_writes(tmp_path: Path) -> None:
    capture = _run(
        Scenario.NESTED_CONFIG,
        Phase.CAPTURE,
        CrashBoundary.AFTER_WRITE,
        tmp_path,
    )
    assert capture.returncode == PROCESS_CRASH_EXIT, capture.stdout + capture.stderr
    journal = (tmp_path / "commits.pickle").read_bytes()
    calls = _calls(tmp_path)
    (tmp_path / "configs" / "revision-2.pickle").unlink()

    recovered = _run(Scenario.NESTED_CONFIG, Phase.RECOVER, CrashBoundary.NONE, tmp_path)
    assert recovered.returncode != 0
    assert "the exact process Config snapshot is unavailable" in recovered.stderr
    assert (tmp_path / "commits.pickle").read_bytes() == journal
    assert _calls(tmp_path) == calls
    assert not (tmp_path / "result").exists()


@pytest.mark.parametrize(
    "boundary",
    (CrashBoundary.BEFORE_WRITE, CrashBoundary.AFTER_WRITE),
    ids=lambda boundary: boundary,
)
def test_child_creation_uses_cross_process_negative_evidence_only_when_absent(
    tmp_path: Path,
    boundary: CrashBoundary,
) -> None:
    capture = _run(Scenario.CHILD_START, Phase.CAPTURE, boundary, tmp_path)
    assert capture.returncode == PROCESS_CRASH_EXIT, capture.stdout + capture.stderr
    child_starts = tuple(
        commit for commit in _commits(tmp_path) if commit.scope == ("child",) and commit.expected_revision is None
    )
    assert len(child_starts) == int(boundary is CrashBoundary.AFTER_WRITE)

    recovered = _run(Scenario.CHILD_START, Phase.RECOVER, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(recovered)
    assert (tmp_path / "result").read_text() == "input-first-second"
    calls = _calls(tmp_path)
    assert Counter(record.node for record in calls) == Counter(("first", "second"))
    assert {record.phase for record in calls} == {Phase.RECOVER}
    if boundary is CrashBoundary.BEFORE_WRITE:
        persistence_records = tuple(line.split("|") for line in (tmp_path / "persistence.log").read_text().splitlines())
        assert any(parts[:3] == ["load", Phase.RECOVER, "1"] for parts in persistence_records)
    journal = (tmp_path / "commits.pickle").read_bytes()

    replay = _run(Scenario.CHILD_START, Phase.REPLAY, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(replay)
    assert _calls(tmp_path) == calls
    assert (tmp_path / "commits.pickle").read_bytes() == journal
    assert (tmp_path / "generation").read_text() == "3"


def test_nested_interrupts_are_consumed_once_across_new_agent_processes(tmp_path: Path) -> None:
    captured = _run(Scenario.INTERRUPT_FAMILY, Phase.CAPTURE, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(captured)
    original = _interrupts(tmp_path)
    assert {interrupt.scope for interrupt in original} == {("left",), ("right",)}
    right = next(interrupt for interrupt in original if interrupt.scope == ("right",))
    assert not (tmp_path / "result").exists()

    partial = _run(Scenario.INTERRUPT_FAMILY, Phase.PARTIAL, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(partial)
    remaining = _interrupts(tmp_path)
    assert remaining == (right,)
    assert not (tmp_path / "result").exists()

    recovered = _run(Scenario.INTERRUPT_FAMILY, Phase.RECOVER, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(recovered)
    assert (tmp_path / "result").read_text() == "left|right"
    calls = _calls(tmp_path)
    assert Counter(record.phase for record in calls) == Counter(
        (Phase.CAPTURE, Phase.CAPTURE, Phase.PARTIAL, Phase.RECOVER)
    )
    assert Counter((record.node, record.details) for record in calls) == Counter(
        (("ask", ("question",)), ("ask", ("question",)), ("ask", ("left",)), ("ask", ("right",)))
    )
    assert len({record.process_id for record in calls}) == 3
    journal = (tmp_path / "commits.pickle").read_bytes()

    replay = _run(Scenario.INTERRUPT_FAMILY, Phase.REPLAY, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(replay)
    assert _calls(tmp_path) == calls
    assert (tmp_path / "commits.pickle").read_bytes() == journal
    assert (tmp_path / "generation").read_text() == "4"


@pytest.mark.parametrize(
    ("corruption", "message"),
    (
        (b"not a pickle", "the process test journal is malformed"),
        (pickle.dumps((NeverCreated(),)), "the process test journal must contain exact keyed Graph commit records"),
    ),
    ids=("invalid-serialization", "wrong-record"),
)
def test_process_journal_is_readmitted_before_graph_assembly(
    tmp_path: Path,
    corruption: bytes,
    message: str,
) -> None:
    capture = _run(Scenario.LINEAR, Phase.CAPTURE, CrashBoundary.AFTER_WRITE, tmp_path)
    assert capture.returncode == PROCESS_CRASH_EXIT, capture.stdout + capture.stderr
    calls = _calls(tmp_path)
    (tmp_path / "commits.pickle").write_bytes(corruption)

    recovered = _run(Scenario.LINEAR, Phase.RECOVER, CrashBoundary.NONE, tmp_path)
    assert recovered.returncode != 0
    assert message in recovered.stderr
    assert (tmp_path / "commits.pickle").read_bytes() == corruption
    assert _calls(tmp_path) == calls
    assert not (tmp_path / "result").exists()


def test_new_process_fences_a_live_old_authority_before_its_write(tmp_path: Path) -> None:
    stale = subprocess.Popen(
        _command(Scenario.STALE_AUTHORITY, Phase.STALE, CrashBoundary.NONE, tmp_path),
        cwd=_root(),
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not (tmp_path / "stale-ready").exists() and stale.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)

    successor = _run(Scenario.STALE_AUTHORITY, Phase.RECOVER, CrashBoundary.NONE, tmp_path)
    journal = (tmp_path / "commits.pickle").read_bytes() if (tmp_path / "commits.pickle").exists() else b""
    stale.send_signal(signal.SIGUSR1)
    try:
        stale_stdout, stale_stderr = stale.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        stale.kill()
        stale_stdout, stale_stderr = stale.communicate()
        pytest.fail(stale_stdout + stale_stderr or "stale authority process did not terminate")

    assert (tmp_path / "stale-ready").exists(), stale_stdout + stale_stderr
    _assert_completed_process(successor)
    assert stale.returncode == 0, stale_stdout + stale_stderr
    assert (tmp_path / "stale-rejected").read_text() == "rejected"
    assert (tmp_path / "result").read_text() == "input-owned"
    assert (tmp_path / "commits.pickle").read_bytes() == journal
    calls = _calls(tmp_path)
    assert Counter(record.phase for record in calls) == Counter((Phase.STALE, Phase.RECOVER))
    assert len({record.process_id for record in calls}) == 2

    replay = _run(Scenario.STALE_AUTHORITY, Phase.REPLAY, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(replay)
    assert _calls(tmp_path) == calls
    assert (tmp_path / "commits.pickle").read_bytes() == journal
    assert (tmp_path / "generation").read_text() == "3"


def test_runtime_crash_remains_outside_kernel_commit_accounting(tmp_path: Path) -> None:
    capture = _run(Scenario.RUNTIME_BOUNDARY, Phase.CAPTURE, CrashBoundary.NONE, tmp_path)
    assert capture.returncode == RUNTIME_CRASH_EXIT, capture.stdout + capture.stderr
    captured = _commits(tmp_path)
    assert not any(commit.writes.publications for commit in captured)
    runtime_before = (tmp_path / "runtime.log").read_text().splitlines()
    assert len(runtime_before) == 1 and runtime_before[0].split("|")[:2] == [Phase.CAPTURE, "invoke"]

    recovered = _run(Scenario.RUNTIME_BOUNDARY, Phase.RECOVER, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(recovered)
    assert (tmp_path / "result").read_text() == "input-runtime"
    confirmed = _commits(tmp_path)
    assert sum(len(commit.writes.publications) for commit in confirmed) == 1
    journal = (tmp_path / "commits.pickle").read_bytes()
    persistence_operations = tuple(
        line.split("|", 1)[0] for line in (tmp_path / "persistence.log").read_text().splitlines()
    )
    assert set(persistence_operations) == {"load", "commit"}
    assert "reconcile" not in persistence_operations
    runtime_after = (tmp_path / "runtime.log").read_text().splitlines()
    assert [line.split("|")[0] for line in runtime_after] == [Phase.CAPTURE, Phase.RECOVER]
    assert len({line.rsplit("|", 1)[-1] for line in runtime_after}) == 2

    replay = _run(Scenario.RUNTIME_BOUNDARY, Phase.REPLAY, CrashBoundary.NONE, tmp_path)
    _assert_completed_process(replay)
    assert (tmp_path / "runtime.log").read_text().splitlines() == runtime_after
    assert (tmp_path / "commits.pickle").read_bytes() == journal


def test_process_journal_isolates_agents_runs_and_sessions_across_processes(tmp_path: Path) -> None:
    scenario = Scenario.FAMILY_ISOLATION

    for agent_id, run_id, value in (
        ("x", "run-old", "x-old"),
        ("x", "run-new", "x-new"),
        ("y", "run-a", "y-only"),
    ):
        started = _run(
            scenario,
            Phase.CAPTURE,
            CrashBoundary.NONE,
            tmp_path,
            agent_id=agent_id,
            request_mode=RequestMode.START,
            run_id=run_id,
            value=value,
        )
        _assert_completed_process(started)
        assert (tmp_path / "result").read_text() == f"{run_id}|{value}-first-second|{value}-hook|{value}-context"

    latest_x = _run(
        scenario,
        Phase.RECOVER,
        CrashBoundary.NONE,
        tmp_path,
        agent_id="x",
        request_mode=RequestMode.RESUME_LATEST,
    )
    _assert_completed_process(latest_x)
    assert (tmp_path / "result").read_text() == "run-new|x-new-first-second|x-new-hook|x-new-context"

    latest_y = _run(
        scenario,
        Phase.RECOVER,
        CrashBoundary.NONE,
        tmp_path,
        agent_id="y",
        request_mode=RequestMode.RESUME_LATEST,
    )
    _assert_completed_process(latest_y)
    assert (tmp_path / "result").read_text() == "run-a|y-only-first-second|y-only-hook|y-only-context"

    historical_x = _run(
        scenario,
        Phase.REPLAY,
        CrashBoundary.NONE,
        tmp_path,
        agent_id="x",
        request_mode=RequestMode.RESUME_EXACT,
        run_id="run-old",
    )
    _assert_completed_process(historical_x)
    assert (tmp_path / "result").read_text() == "run-old|x-old-first-second|x-old-hook|x-old-context"

    decoded: object = pickle.loads((tmp_path / "commits.pickle").read_bytes())
    assert type(decoded) is tuple
    records = cast(tuple[ProcessCommitRecord[str], ...], decoded)
    roots = tuple(
        record for record in records if not record.request.scope and record.request.candidate_state.revision == 0
    )
    assert all(record.root_head is not None and record.root_head.key == record.key for record in roots)
    assert {(record.key.agent_id, record.root_head.generation) for record in roots if record.root_head is not None} == {
        ("x", 1),
        ("x", 2),
        ("y", 1),
    }
    assert {record.key.agent_id for record in records} == {"x", "y"}
    assert {record.key.run_id for record in records if record.key.agent_id == "x" and not record.request.scope} == {
        "run-old",
        "run-new",
    }
    assert {record.key.run_id for record in records if record.key.agent_id == "y" and not record.request.scope} == {
        "run-a",
    }


def test_process_journal_latest_uses_the_generation_chain_not_append_order(tmp_path: Path) -> None:
    scenario = Scenario.FAMILY_ISOLATION
    for run_id, value in (("run-old", "old"), ("run-new", "new")):
        started = _run(
            scenario,
            Phase.CAPTURE,
            CrashBoundary.NONE,
            tmp_path,
            agent_id="x",
            request_mode=RequestMode.START,
            run_id=run_id,
            value=value,
        )
        _assert_completed_process(started)

    decoded: object = pickle.loads((tmp_path / "commits.pickle").read_bytes())
    assert type(decoded) is tuple
    decoded_records = cast(tuple[object, ...], decoded)
    assert all(type(item) is ProcessCommitRecord for item in decoded_records)
    records = cast(tuple[ProcessCommitRecord[str], ...], decoded_records)
    old_records = tuple(record for record in records if record.key.run_id == "run-old")
    new_records = tuple(record for record in records if record.key.run_id == "run-new")
    assert old_records and new_records
    (tmp_path / "commits.pickle").write_bytes(pickle.dumps((*new_records, *old_records)))

    latest = _run(
        scenario,
        Phase.RECOVER,
        CrashBoundary.NONE,
        tmp_path,
        agent_id="x",
        request_mode=RequestMode.RESUME_LATEST,
    )
    _assert_completed_process(latest)
    assert (tmp_path / "result").read_text() == "run-new|new-first-second|new-hook|new-context"


@pytest.mark.asyncio
async def test_process_journal_keeps_multiple_families_in_one_persistence_instance(tmp_path: Path) -> None:
    phase = Phase.CAPTURE
    authority = ProcessAuthority(tmp_path, phase)
    persistence = ProcessPersistence(authority, tmp_path, Scenario.FAMILY_ISOLATION, phase, CrashBoundary.NONE)
    calls: list[str] = []
    agent = Agent[str, str, str](
        "x",
        lambda _config: linear_graph(calls),
        STRING_CODEC,
        persistence,
        authority,
        session_codec=_session_codec(),
    )

    await agent.run(AgentStart("old", Graph.values(value="old"), AgentSession("old-hook", "old-context")))
    await agent.run(AgentStart("new", Graph.values(value="new"), AgentSession("new-hook", "new-context")))

    latest = await agent.run(AgentResume())
    historical = await agent.run(AgentResume("old"))

    assert isinstance(latest, AgentCompleted)
    assert isinstance(historical, AgentCompleted)
    assert latest.run_id == "new"
    assert historical.run_id == "old"
    assert latest.session == AgentSession("new-hook", "new-context")
    assert historical.session == AgentSession("old-hook", "old-context")
