import json
from pathlib import Path

import pytest
from bench.pipeline import run_pipeline, verify_completion
from bench.profiles import normalize_config


def stage(tmp_path, kind="fixed-load"):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"cells": ["example"], "experiment_type": kind, "fixed_users": [100, 500, 1000]})
    )
    out = tmp_path / "output"
    out.mkdir()
    (out / "capacity-status.json").write_text(json.dumps({"status": "complete"}))
    item = {
        "status": "fixed_load_complete",
        "fixed_users": [100, 500, 1000],
        "observations": [{"users": n, "outcome": "pass"} for n in [100, 500, 1000]],
    }
    if kind == "capacity":
        item = {"status": "unstable_boundary"}
    (
        out / ("fixed-load-results.json" if kind == "fixed-load" else "capacity-results.json")
    ).write_text(json.dumps({"example": item}))
    trial = out / "trial-0001"
    trial.mkdir()
    (trial / "manifest.json").write_text(
        json.dumps(
            {
                "campaign": normalize_config(json.loads(config.read_text())),
                "nofile": {"soft": 250000, "hard": 250000, "process_samples": 1},
            }
        )
    )
    (trial / "result.json").write_text(json.dumps({"status": "measured", "reset_verified": True}))
    return {
        "name": "example",
        "config": str(config),
        "output": str(out),
        "controller": "example-unit",
        "state": str(tmp_path / "supervisor.json"),
    }


def test_valid_below_target_fixed_load_can_advance(tmp_path):
    s = stage(tmp_path)
    p = Path(s["output"]) / "fixed-load-results.json"
    d = json.loads(p.read_text())
    d["example"]["observations"][1]["outcome"] = "fail"
    p.write_text(json.dumps(d))
    verify_completion(s)


def test_capacity_uncertainty_is_preserved_and_can_advance(tmp_path):
    s = stage(tmp_path, "capacity")
    Path(s["output"], "capacity-status.json").write_text(
        json.dumps({"status": "complete_with_unstable_boundaries"})
    )
    verify_completion(s)


@pytest.mark.parametrize("change", ["attention", "cleanup", "invalid", "missing_population"])
def test_completion_gate_rejects_invalid_or_incomplete_evidence(tmp_path, change):
    s = stage(tmp_path)
    p = Path(s["output"])
    if change == "attention":
        (p / "capacity-status.json").write_text(json.dumps({"status": "needs_attention"}))
    elif change == "cleanup":
        (p / "trial-0001/result.json").write_text(
            json.dumps({"status": "measured", "reset_verified": False})
        )
    else:
        d = json.loads((p / "fixed-load-results.json").read_text())
        d["example"]["observations"][0]["outcome"] = "invalid" if change == "invalid" else "pass"
        if change == "missing_population":
            d["example"]["observations"].pop()
        (p / "fixed-load-results.json").write_text(json.dumps(d))
    with pytest.raises(RuntimeError):
        verify_completion(s)


def test_supervisor_returning_normally_does_not_advance_without_complete(tmp_path, monkeypatch):
    import bench.pipeline as m

    s = stage(tmp_path)
    Path(s["output"], "capacity-status.json").write_text(json.dumps({"status": "needs_attention"}))
    (tmp_path / "queue.json").write_text(json.dumps({"stages": [s]}))
    calls = []
    monkeypatch.setattr(m, "supervise", lambda *a: calls.append("supervised"))
    monkeypatch.setattr(m, "controller_active", lambda _: False)
    with pytest.raises(RuntimeError):
        run_pipeline(tmp_path / "queue.json", tmp_path / "state.json")
    assert calls == ["supervised"]
    assert json.loads((tmp_path / "state.json").read_text())["status"] == "needs_attention"


def test_complete_output_under_different_methodology_is_rejected(tmp_path):
    s = stage(tmp_path)
    p = Path(s["output"]) / "trial-0001/manifest.json"
    d = json.loads(p.read_text())
    d["campaign"]["hold_seconds"] = 120
    p.write_text(json.dumps(d))
    with pytest.raises((RuntimeError, ValueError), match="measurement"):
        verify_completion(s)


def test_stage_config_changed_while_observing_is_rejected(tmp_path, monkeypatch):
    import bench.pipeline as m

    s = stage(tmp_path)
    s["observe_supervisor"] = "external"
    q = tmp_path / "queue.json"
    q.write_text(json.dumps({"stages": [s]}))
    polls = iter([True, False, False])
    monkeypatch.setattr(m, "controller_active", lambda _: next(polls))

    def edit(_):
        p = Path(s["config"])
        d = json.loads(p.read_text())
        d["hold_seconds"] = 120
        p.write_text(json.dumps(d))

    with pytest.raises(RuntimeError, match="configuration changed"):
        run_pipeline(q, tmp_path / "state.json", sleep=edit)
    assert json.loads((tmp_path / "state.json").read_text())["status"] == "needs_attention"


def test_restart_skips_idle_check_for_completed_earlier_stage(tmp_path, monkeypatch):
    import hashlib

    import bench.pipeline as m

    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first = stage(first_dir)
    first["name"] = "first"
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = stage(second_dir)
    second["name"] = "second"
    q = tmp_path / "queue.json"
    q.write_text(json.dumps({"stages": [first, second]}))
    identity = hashlib.sha256(
        q.read_bytes() + Path(first["config"]).read_bytes() + Path(second["config"]).read_bytes()
    ).hexdigest()
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "identity": identity,
                "completed_stages": ["first"],
                "stage": "second",
                "status": "running_controller",
            }
        )
    )
    calls = []
    monkeypatch.setattr(m, "verify_hosts_idle", lambda s: calls.append(s["name"]))
    run_pipeline(q, state)
    assert calls == ["second"]
    assert json.loads(state.read_text())["completed_stages"] == ["first", "second"]


def test_interrupt_during_past_stage_validation_stops_owned_unfinished_controller(
    tmp_path, monkeypatch
):
    import hashlib

    import bench.pipeline as m

    a = tmp_path / "a"
    a.mkdir()
    first = stage(a)
    first.update(name="first", observe_supervisor="external")
    b = tmp_path / "b"
    b.mkdir()
    second = stage(b)
    second.update(name="second", controller="owned-controller")
    q = tmp_path / "queue.json"
    q.write_text(json.dumps({"stages": [first, second]}))
    identity = hashlib.sha256(
        q.read_bytes() + Path(first["config"]).read_bytes() + Path(second["config"]).read_bytes()
    ).hexdigest()
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"identity": identity, "completed_stages": ["first"], "stage": "second"})
    )

    def interrupt(_):
        raise KeyboardInterrupt

    calls = []
    monkeypatch.setattr(m, "verify_completion", interrupt)
    monkeypatch.setattr(m.subprocess, "run", lambda args, **kw: calls.append(args))
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(q, state)
    assert calls == [["sudo", "systemctl", "stop", "owned-controller"]]
    assert json.loads(state.read_text())["stage"] == "second"
    assert json.loads(state.read_text())["completed_stages"] == ["first"]


def test_complete_capacity_bracket_cannot_skip_requested_refinement(tmp_path):
    s = stage(tmp_path, "capacity")
    cfg = Path(s["config"])
    d = json.loads(cfg.read_text())
    d["resolution_users"] = 50
    cfg.write_text(json.dumps(d))
    manifest = Path(s["output"]) / "trial-0001/manifest.json"
    m = json.loads(manifest.read_text())
    m["campaign"]["resolution_users"] = 50
    manifest.write_text(json.dumps(m))
    output = Path(s["output"]) / "capacity-results.json"
    output.write_text(
        json.dumps(
            {"example": {"status": "bracket_found", "passing_users": 1000, "failing_users": 1062}}
        )
    )
    with pytest.raises(RuntimeError, match="resolution"):
        verify_completion(s)


def test_refined_completion_accepts_audited_old_resolution_evidence(tmp_path):
    from bench.majority import measurement_identity

    s = stage(tmp_path, "capacity")
    cfg = Path(s["config"])
    old = json.loads(cfg.read_text())
    old["resolution_users"] = 100
    mf = Path(s["output"]) / "trial-0001/manifest.json"
    m = json.loads(mf.read_text())
    m["campaign"] = normalize_config(old)
    mf.write_text(json.dumps(m))
    new = {
        **old,
        "resolution_users": 50,
        "resolution_handoff": {
            "from": 100,
            "to": 50,
            "measurement_identity_sha256": measurement_identity(old),
            "reason": "explicituserrefinement",
        },
    }
    cfg.write_text(json.dumps(new))
    (Path(s["output"]) / "capacity-results.json").write_text(
        json.dumps(
            {"example": {"status": "bracket_found", "passing_users": 1031, "failing_users": 1062}}
        )
    )
    verify_completion(s)
