import os
import subprocess
import sys
from pathlib import Path


def test_confirmed_values_survive_a_real_exit_and_a_new_agent_process(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    capture = subprocess.run(
        [sys.executable, "-m", "tests.agent.subprocess_worker", "capture", str(tmp_path)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert capture.returncode == 23, capture.stdout + capture.stderr
    recorded = (tmp_path / "calls").read_text().splitlines()
    assert len(recorded) == 1 and recorded[0].startswith("first:")
    assert not (tmp_path / "result").exists()
    for phase in ("recover", "replay"):
        recovered = subprocess.run(
            [sys.executable, "-m", "tests.agent.subprocess_worker", phase, str(tmp_path)],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        assert (tmp_path / "result").read_text() == "input-first-second"
    calls = (tmp_path / "calls").read_text().splitlines()
    assert len(calls) == 2
    assert calls[0].split(":")[0] == "first"
    assert calls[1].split(":")[0] == "second"
    assert calls[0].split(":")[1] != calls[1].split(":")[1]
    assert (tmp_path / "generation").read_text() == "3"
