import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

CONTROLLER = """import json,signal,subprocess,sys,time
from pathlib import Path
from bench.runner import trial_lifecycle
root=Path(sys.argv[1]); child=None

def stop():
 if child is not None:
  if child.poll() is not None:raise RuntimeError('cleanup helper was signaled before reset')
  time.sleep(.2)

def reset():
 if child is not None:
  (root/'reset.json').write_text(json.dumps({'helper_alive_during_reset':child.poll() is None}))

def measure():
 global child
 child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
 (root/'ready').write_text(str(child.pid))
 while True:time.sleep(.1)

state=trial_lifecycle(stop,reset,measure)
(root/'result.json').write_text(json.dumps(state))
if child is not None:child.terminate();child.wait(timeout=5)
"""


def wait_file(path, process=None, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process is not None and process.poll() is not None:
            pytest.fail("controller exited before readiness")
        time.sleep(0.02)
    pytest.fail("controller did not publish readiness")


def test_direct_main_sigint_runs_real_lifecycle_cleanup_while_helper_survives(tmp_path):
    script = tmp_path / "controller.py"
    script.write_text(CONTROLLER)
    process = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path)],
        start_new_session=True,
        env={**os.environ, "PYTHONPATH": str(Path("tools").resolve())},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child = None
    try:
        wait_file(tmp_path / "ready", process)
        child = int((tmp_path / "ready").read_text())
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, (out, err)
        assert json.loads((tmp_path / "reset.json").read_text())["helper_alive_during_reset"]
        state = json.loads((tmp_path / "result.json").read_text())
        assert state["status"] == "interrupted" and state["reset_verified"]
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_user_systemd_mixed_stop_has_actual_cleanup_proof(tmp_path):
    probe = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True)
    if probe.returncode:
        pytest.skip("local user systemd manager unavailable")
    script = tmp_path / "controller.py"
    script.write_text(CONTROLLER)
    unit = "lb-ramp5-stop-" + uuid.uuid4().hex[:12]
    try:
        subprocess.run(
            [
                "systemd-run",
                "--user",
                "--quiet",
                "--unit",
                unit,
                "--property=Type=exec",
                "--property=KillMode=mixed",
                "--property=KillSignal=SIGINT",
                "--property=TimeoutStopSec=20",
                "--setenv=PYTHONPATH=" + str(Path("tools").resolve()),
                sys.executable,
                str(script),
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
        )
        wait_file(tmp_path / "ready")
        subprocess.run(
            ["systemctl", "--user", "stop", unit], check=True, capture_output=True, timeout=30
        )
        assert json.loads((tmp_path / "reset.json").read_text())["helper_alive_during_reset"]
        state = json.loads((tmp_path / "result.json").read_text())
        assert state["status"] == "interrupted" and state["reset_verified"]
    finally:
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=30)
        subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True)


def test_new_ramp5_units_sync_then_exec_python_and_signal_only_main():
    for name in ("capacity-ramp5", "ramp5-supervisor"):
        source = Path("infrastructure/aws/language-benchmark-" + name + ".service").read_text()
        assert "uv sync --frozen" in source
        start = next(line for line in source.splitlines() if line.startswith("ExecStart="))
        assert "exec /home/rocky/language-benchmark/.venv/bin/python -m bench." in start
        assert "uv run" not in start and "KillMode=mixed" in source
        assert "KillSignal=SIGINT" in source and "TimeoutStopSec=600" in source
        assert "artifacts/aws/capacity-ramp5.json" in source
        assert "artifacts/results/capacity90-ramp5-20261006" in source
    supervisor = Path("infrastructure/aws/language-benchmark-ramp5-supervisor.service").read_text()
    assert "--state artifacts/aws/ramp5-supervisor-status.json" in supervisor
    assert "--existing-unit language-benchmark-capacity-ramp5" in supervisor
