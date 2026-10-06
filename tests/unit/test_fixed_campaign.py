import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bench import fixed_campaign


def setup(tmp_path, monkeypatch):
    cfg = {
        "experiment_type": "fixed-load",
        "cells": ["python", "go"],
        "fixed_users": [100, 500, 1000],
    }
    path = tmp_path / "fixed.json"
    path.write_text(json.dumps(cfg))
    output = tmp_path / "results"
    monkeypatch.setattr(fixed_campaign, "validate_config", lambda _: None)
    monkeypatch.setattr(
        fixed_campaign, "load_runtime_cell", lambda name: SimpleNamespace(supported=True)
    )
    return path, output


def test_mixed_fixed_campaign_first_start_and_valid_policy_failure_are_successful(
    tmp_path, monkeypatch, capsys
):
    path, output = setup(tmp_path, monkeypatch)
    calls = []

    def run(config, destination, **kw):
        calls.append((config, destination, kw))
        return {
            name: {"status": "fixed_load_complete", "observations": [{"outcome": "fail"}]}
            for name in ("python", "go")
        }

    monkeypatch.setattr(fixed_campaign, "run_user_capacity", run)
    assert fixed_campaign.main(["--config", str(path), "--output", str(output)]) == 0
    assert calls[0][2] == {"resume": False, "retry_invalid": True}
    assert "fixed_load_complete" in capsys.readouterr().out


def test_fixed_campaign_resume_delegates_preserved_manifests_and_invalid_exit_is_nonzero(
    tmp_path, monkeypatch
):
    path, output = setup(tmp_path, monkeypatch)
    trial = output / "one"
    trial.mkdir(parents=True)
    (trial / "manifest.json").write_text("{}")

    def run(*args, **kw):
        assert kw == {"resume": True, "retry_invalid": True}
        return {"python": {"status": "invalid"}}

    monkeypatch.setattr(fixed_campaign, "run_user_capacity", run)
    assert fixed_campaign.main(["--config", str(path), "--output", str(output)]) == 1
    assert (trial / "manifest.json").exists()


def test_fixed_campaign_preserves_pre_manifest_crash_before_fresh_start(tmp_path, monkeypatch):
    path, output = setup(tmp_path, monkeypatch)
    output.mkdir()
    (output / "capacity-status.json").write_text('{"status":"testing"}')

    def run(*args, **kw):
        assert not output.exists() and not kw["resume"]
        return {name: {"status": "fixed_load_complete"} for name in ("python", "go")}

    monkeypatch.setattr(fixed_campaign, "run_user_capacity", run)
    assert fixed_campaign.main(["--config", str(path), "--output", str(output)]) == 0
    archived = list(tmp_path.glob("results-premeasurement-*"))
    assert len(archived) == 1 and (archived[0] / "capacity-status.json").exists()


def test_fixed_campaign_rejects_capacity_and_orphan_results_without_touching_them(
    tmp_path, monkeypatch
):
    path, output = setup(tmp_path, monkeypatch)
    cfg = json.loads(path.read_text())
    cfg["experiment_type"] = "capacity"
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match="fixed-load"):
        fixed_campaign.main(["--config", str(path), "--output", str(output)])
    cfg["experiment_type"] = "fixed-load"
    path.write_text(json.dumps(cfg))
    output.mkdir()
    (output / "fixed-load-results.json").write_text("{}")
    with pytest.raises(RuntimeError, match="original manifests"):
        fixed_campaign.main(["--config", str(path), "--output", str(output)])
    assert (output / "fixed-load-results.json").exists()


def test_fixed_units_have_their_own_controller_config_output_and_state():
    controller = Path("infrastructure/aws/language-benchmark-fixed-load.service").read_text()
    supervisor = Path("infrastructure/aws/language-benchmark-fixed-supervisor.service").read_text()
    assert "bench.fixed_campaign" in controller and "--resume" not in controller
    for text in (controller, supervisor):
        assert (
            "artifacts/aws/fixed-load.json" in text
            and "artifacts/results/fixed-load-20261006" in text
        )
        assert "KillSignal=SIGINT" in text and "KillMode=control-group" in text
    assert "--existing-unit language-benchmark-fixed-load" in supervisor
    assert "--state artifacts/aws/fixed-supervisor-status.json" in supervisor
    assert "Restart=no" in controller and "StartLimitBurst=3" in supervisor


def test_mixed_native_runtime_requires_verified_image_digest(tmp_path, monkeypatch):
    from bench.models import NativeCell

    path, output = setup(tmp_path, monkeypatch)
    c = json.loads(path.read_text())
    c["cells"] = ["python", "go-net-http"]
    path.write_text(json.dumps(c))
    native = NativeCell(**json.loads(Path("runtimes/go/lock.json").read_text())["cells"][0])
    monkeypatch.setattr(
        fixed_campaign,
        "load_runtime_cell",
        lambda name: native if name == native.id else SimpleNamespace(supported=True),
    )
    with pytest.raises(ValueError, match="expected_image_ids"):
        fixed_campaign.main(["--config", str(path), "--output", str(output)])
    assert not output.exists()


def test_module_cli_exit_is_nonzero_for_invalid_evidence(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    path, output = setup(tmp_path, monkeypatch)
    script = """import runpy,sys
from types import SimpleNamespace
import bench.user_capacity as c
import bench.native_runtime as n
c.validate_config=lambda _:None
c.run_user_capacity=lambda *a,**k:{'python':{'status':'invalid'}}
n.load_runtime_cell=lambda _:SimpleNamespace(supported=True)
sys.argv=['fixed_campaign','--config',sys.argv[1],'--output',sys.argv[2]]
runpy.run_module('bench.fixed_campaign',run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(output)],
        env={**os.environ, "PYTHONPATH": "tools"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and '"status": "invalid"' in result.stdout
