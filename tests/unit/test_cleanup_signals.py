import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("stage", ["measure", "stop", "reset"])
def test_signals_during_any_stage_persist_verified_cleanup(tmp_path, stage):
    code = """import json,os,signal,sys,time
from pathlib import Path
from bench.runner import trial_lifecycle
r=Path(sys.argv[1]);target=sys.argv[2];count=0

def pause(stage):
 if stage==target:
  (r/'ready').write_text(stage)
  time.sleep(.5)

def stop():
 global count
 count+=1
 if count>1:pause('stop')

def reset():
 if count>1:pause('reset')

def measure():pause('measure')
def persist(result):(r/'result.json').write_text(json.dumps(result))
trial_lifecycle(stop,reset,measure,persist=persist)
"""
    script = tmp_path / "run.py"
    script.write_text(code)
    p = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path), stage],
        env={**os.environ, "PYTHONPATH": str(Path("tools").resolve())},
    )
    try:
        until = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists()
        p.send_signal(signal.SIGINT)
        if stage != "measure":
            time.sleep(0.05)
            p.send_signal(signal.SIGTERM)
            time.sleep(0.05)
            p.send_signal(signal.SIGINT)
        assert p.wait(timeout=5) == 0
        data = json.loads((tmp_path / "result.json").read_text())
        assert data["status"] == "interrupted" and data["reset_verified"]
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
