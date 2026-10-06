import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from bench import capacity_campaign
from bench.user_capacity import (
    cell_starting_points,
    normalize_config,
    run_user_capacity,
    validate_config,
)


def config(tmp_path):
    p = tmp_path / "fixture.json"
    p.write_text(json.dumps({"counts": {"users": 50000}}))
    return normalize_config(
        {
            "mode": "local",
            "profile": "fast",
            "experiment_type": "capacity",
            "cells": ["go-net-http", "php-laravel"],
            "fixture": str(p),
            "database_allowlist": "language_benchmark_test",
            "cell_starting_hints": {
                "go-net-http": {"start_users": 1000},
                "php-laravel": {"start_users": 100, "initial_failing_hint": 500},
            },
        }
    )


def test_explicit_cell_hints_override_prior_stack_and_default_still_reuses():
    c = normalize_config(
        {
            "cell_starting_hints": {
                "go": {"start_users": 1000},
                "php": {"start_users": 100, "initial_failing_hint": 500},
            }
        }
    )
    previous = {"status": "bracket_found", "passing_users": 2200}
    assert cell_starting_points(c, "go", previous, 2300) == (1000, None)
    assert cell_starting_points(c, "php", previous, 2300) == (100, 500)
    assert cell_starting_points(c, "unmapped", previous, 2300) == (2200, 2300)
    del c["cell_starting_hints"]
    assert cell_starting_points(c, "go", previous, 2300) == (2200, 2300)


@pytest.mark.parametrize(
    "hints",
    [
        [],
        {"unknown": {"start_users": 100}},
        {"go-net-http": {}},
        {"go-net-http": {"start_users": True}},
        {"go-net-http": {"start_users": 0}},
        {"go-net-http": {"start_users": 50001}},
        {"go-net-http": {"start_users": 1000, "initial_failing_hint": 999}},
        {"go-net-http": {"start_users": 1000, "initial_failing_hint": 50001}},
        {"go-net-http": {"start_users": 1000, "extra": 2}},
    ],
)
def test_invalid_cell_hints_cannot_run(tmp_path, hints):
    c = config(tmp_path)
    c["cell_starting_hints"] = hints
    with pytest.raises(ValueError, match="cell_starting_hints"):
        validate_config(c)


def test_each_hint_is_passed_to_search_as_a_new_measured_candidate(tmp_path, monkeypatch):
    import bench.user_capacity as module

    c = config(tmp_path)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(c))
    calls = []
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(module, "campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(module, "database_campaign_lock", lambda *_: nullcontext())

    def search(probe, start, step, ceiling, resolution, next_candidate=None, **kw):
        calls.append((start, next_candidate))
        return {"status": "ceiling_reached", "passing_users": 50000, "observations": []}

    monkeypatch.setattr(module, "search_capacity", search)
    run_user_capacity(p, tmp_path / "results")
    assert calls == [(1000, None), (100, 500)]


def test_resume_rejects_changed_cell_hint_mapping_before_reusing(tmp_path):
    c = config(tmp_path)
    root = tmp_path / "results"
    trial = root / "one-0000"
    trial.mkdir(parents=True)
    (trial / "manifest.json").write_text(
        json.dumps({"campaign": c, "trial": {"id": "one-0000", "campaign_id": "one"}})
    )
    changed = {
        **c,
        "cell_starting_hints": {
            "go-net-http": {"start_users": 1100},
            "php-laravel": {"start_users": 100, "initial_failing_hint": 500},
        },
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="cell_starting_hints"):
        run_user_capacity(p, root, resume=True)


def entry_setup(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"experiment_type": "capacity", "cells": ["python", "go"]}))
    output = tmp_path / "results"
    monkeypatch.setattr(capacity_campaign, "validate_config", lambda _: None)
    monkeypatch.setattr(
        capacity_campaign, "load_runtime_cell", lambda _: SimpleNamespace(supported=True)
    )
    return p, output


def test_capacity_entry_first_start_resume_and_invalid_exit(tmp_path, monkeypatch):
    p, out = entry_setup(tmp_path, monkeypatch)
    calls = []

    def run(*a, **kw):
        calls.append(kw)
        return {"python": {"status": "bracket_found"}, "go": {"status": "unstable_boundary"}}

    monkeypatch.setattr(capacity_campaign, "run_user_capacity", run)
    assert capacity_campaign.main(["--config", str(p), "--output", str(out)]) == 0
    assert calls == [{"resume": False, "retry_invalid": True}]
    t = out / "one"
    t.mkdir(parents=True)
    (t / "manifest.json").write_text("{}")
    monkeypatch.setattr(
        capacity_campaign, "run_user_capacity", lambda *a, **kw: {"python": {"status": "invalid"}}
    )
    assert capacity_campaign.main(["--config", str(p), "--output", str(out)]) == 1


def test_capacity_entry_archives_only_unmeasured_crashes_and_rejects_fixedmode(
    tmp_path, monkeypatch
):
    p, out = entry_setup(tmp_path, monkeypatch)
    out.mkdir()
    (out / "capacity-status.json").write_text("{}")
    monkeypatch.setattr(capacity_campaign, "run_user_capacity", lambda *a, **kw: {})
    assert capacity_campaign.main(["--config", str(p), "--output", str(out)]) == 1
    assert len(list(tmp_path.glob("results-premeasurement-*"))) == 1
    c = json.loads(p.read_text())
    c["experiment_type"] = "fixed-load"
    p.write_text(json.dumps(c))
    with pytest.raises(ValueError, match="capacity"):
        capacity_campaign.main(["--config", str(p), "--output", str(out)])


def test_fast_units_are_isolated_from_fixed_outputs():
    c = Path("infrastructure/aws/language-benchmark-fast-capacity.service").read_text()
    s = Path("infrastructure/aws/language-benchmark-fast-supervisor.service").read_text()
    assert "bench.capacity_campaign" in c and "--resume" not in c
    for text in (c, s):
        assert (
            "artifacts/aws/fast-capacity.json" in text
            and "artifacts/results/capacity90-20261006" in text
        )
    assert (
        "--state artifacts/aws/fast-supervisor-status.json" in s
        and "--existing-unit language-benchmark-fast-capacity" in s
    )


@pytest.mark.parametrize(
    "old_method", [{"experiment_type": "fixed-load"}, {"success_fraction": 0.99}]
)
def test_capacity_resume_never_reuses_fixed_or_99_percent_probes(tmp_path, old_method):
    c = config(tmp_path)
    root = tmp_path / "results"
    trial = root / "nonce-0000"
    trial.mkdir(parents=True)
    old = {**c, **old_method}
    (trial / "manifest.json").write_text(
        json.dumps({"campaign": old, "trial": {"id": "nonce-0000", "campaign_id": "nonce"}})
    )
    p = tmp_path / "config.json"
    p.write_text(json.dumps(c))
    with pytest.raises(ValueError, match="resume configuration differs"):
        run_user_capacity(p, root, resume=True)


def test_capacity_module_cli_exit_is_nonzero_for_invalid_evidence(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    p, out = entry_setup(tmp_path, monkeypatch)
    script = """import runpy,sys
from types import SimpleNamespace
import bench.user_capacity as c
import bench.native_runtime as n
c.validate_config=lambda _:None
c.run_user_capacity=lambda *a,**k:{'python':{'status':'invalid'}}
n.load_runtime_cell=lambda _:SimpleNamespace(supported=True)
sys.argv=['capacity_campaign','--config',sys.argv[1],'--output',sys.argv[2]]
runpy.run_module('bench.capacity_campaign',run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(p), str(out)],
        env={**os.environ, "PYTHONPATH": "tools"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and '"status": "invalid"' in result.stdout
