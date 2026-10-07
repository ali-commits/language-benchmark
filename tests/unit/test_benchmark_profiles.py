import copy
import json
from contextlib import nullcontext

import pytest
from bench.user_capacity import normalize_config, run_user_capacity, validate_config


def test_fast_defaults_and_legacy_explicit_values_are_preserved():
    assert normalize_config({})["profile"] == "fast"
    c = normalize_config({})
    assert (c["ramp_seconds"], c["settle_seconds"], c["hold_seconds"]) == (180, 0, 240)
    assert c["success_fraction"] == c["authentication_fraction"] == 0.90
    assert c["confirmation_policy"] == {
        "passing_repeats": 0,
        "failing_repeats": 1,
        "stop_on_contradiction": True,
    }
    raw = {
        "ramp_seconds": 300,
        "settle_seconds": 120,
        "hold_seconds": 300,
        "success_fraction": 0.99,
        "confirmation_repeats": 5,
    }
    original = copy.deepcopy(raw)
    legacy = normalize_config(raw)
    assert raw == original and legacy["profile"] == "legacy"
    assert (
        legacy["success_fraction"] == 0.99 and legacy["confirmation_policy"]["passing_repeats"] == 5
    )
    assert normalize_config({"profile": "fast", "hold_seconds": 60})["hold_seconds"] == 60


def config(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"counts": {"users": 50000}}))
    return normalize_config(
        {
            "mode": "local",
            "cells": ["go-net-http"],
            "fixture": str(fixture),
            "database_allowlist": "language_benchmark_test",
            "experiment_type": "fixed-load",
            "fixed_users": [100, 200],
        }
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("success_fraction", float("nan")),
        ("success_fraction", float("inf")),
        ("authentication_fraction", float("nan")),
        ("success_fraction", 0.89),
        ("authentication_fraction", 1.1),
    ],
)
def test_thresholds_must_be_finite_and_bounded(tmp_path, key, value):
    c = config(tmp_path)
    c[key] = value
    with pytest.raises(ValueError):
        validate_config(c)


@pytest.mark.parametrize("initial_hint", [None, 20000])
def test_fixed_load_invokes_guarded_trials_once_without_search_or_confirmation(
    tmp_path, monkeypatch, initial_hint
):
    import bench.user_capacity as module

    c = config(tmp_path)
    c["cells"] = ["go-net-http", "rust-axum"]
    c["initial_failing_hint"] = initial_hint
    path = tmp_path / "config.json"
    path.write_text(json.dumps(c))
    calls = []
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(module, "campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(module, "database_campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(
        module, "search_capacity", lambda *_args, **_kwargs: pytest.fail("fixed load searched")
    )
    monkeypatch.setattr(module, "confirm_boundary", lambda *_: pytest.fail("fixed load confirmed"))
    monkeypatch.setattr(module, "generator_flags", lambda *_: [])
    monkeypatch.setattr(module, "journey_flags", lambda *_: [])
    monkeypatch.setattr(module, "await_rds", lambda *_: None)
    monkeypatch.setattr(
        module, "classify_journey", lambda *args, **kw: "fail" if args[1] == 200 else "pass"
    )

    def trial(cfg, trial, directory):
        calls.append((trial["cell"], trial["users"]))
        directory.mkdir()
        return {"status": "measured", "reset_verified": True, "reasons": [], "journey_metrics": {}}

    monkeypatch.setattr(module, "run_trial", trial)
    result = run_user_capacity(path, tmp_path / "results")
    assert calls == [
        ("go-net-http", 100),
        ("go-net-http", 200),
        ("rust-axum", 100),
        ("rust-axum", 200),
    ]
    assert all(r["status"] == "fixed_load_complete" for r in result.values())
    assert (tmp_path / "results/fixed-load-results.json").exists()
    assert (
        json.loads((tmp_path / "results/capacity-status.json").read_text())["status"] == "complete"
    )


def test_backend_open_files_evidence_is_invalid_not_a_boundary(tmp_path):
    from bench.user_capacity import backend_validity_flags

    (tmp_path / "backend.log").write_text("accept failed: Too many open files (os error 24)")
    assert backend_validity_flags(tmp_path)


@pytest.mark.parametrize(
    "key,value",
    [
        ("profile", "fast"),
        ("experiment_type", "fixed-load"),
        ("authentication_fraction", 0.95),
        ("mode", "aws"),
        ("fixed_users", [200]),
    ],
)
def test_resume_refuses_method_change_before_reusing_evidence(tmp_path, key, value):
    c = config(tmp_path)
    c["profile"] = "legacy"
    root = tmp_path / "results"
    trial = root / "original-0000"
    trial.mkdir(parents=True)
    (trial / "manifest.json").write_text(
        json.dumps({"campaign": c, "trial": {"campaign_id": "original"}})
    )
    modified = dict(c)
    if key == "experiment_type":
        value = "capacity"
    modified[key] = value
    p = tmp_path / "config.json"
    p.write_text(json.dumps(modified))
    with pytest.raises(ValueError, match="resume configuration differs"):
        run_user_capacity(p, root, resume=True)


def test_overridden_profile_reports_effective_custom_timing():
    from bench.profiles import methodology

    c = normalize_config(
        {"profile": "fast", "ramp_seconds": 300, "settle_seconds": 120, "hold_seconds": 300}
    )
    meta = methodology(c)
    assert meta["profile"] == "custom" and meta["requested_profile"] == "fast"
    assert meta["configured_trial_seconds"] == 720 and meta["steady_duration_seconds"] == 300


@pytest.mark.parametrize("kind", ["python", "native"])
def test_resume_resource_policy_requires_saved_identity_and_actual_limits(kind):
    from bench.user_capacity import validate_resume_resource_policy

    c = normalize_config({})
    proof = {"soft": 250000, "hard": 250000, "process_samples": 2}
    stored = {"campaign": dict(c)}
    if kind == "python":
        stored["nofile"] = proof
    else:
        stored["native_runtime"] = {"container": {"nofile": proof}}
    validate_resume_resource_policy(stored, c, reusable=True)
    missing = dict(stored)
    missing["campaign"] = {k: v for k, v in c.items() if k != "backend_nofile_limit"}
    with pytest.raises(ValueError, match="resource policy"):
        validate_resume_resource_policy(missing, c, reusable=False)
    absent = {"campaign": dict(c)}
    with pytest.raises(ValueError, match="actual nofile"):
        validate_resume_resource_policy(absent, c, reusable=True)
    bad = {"campaign": dict(c), "nofile": {"soft": 2048, "hard": 250000, "process_samples": 1}}
    with pytest.raises(ValueError, match="actual nofile"):
        validate_resume_resource_policy(bad, c, reusable=True)
    validate_resume_resource_policy(absent, c, reusable=False)


@pytest.mark.parametrize(
    "text", ["Too many open files", "os error 24", "[Errno 24]", "errno:24", "EMFILE"]
)
def test_shared_backend_open_file_detector(text):
    from bench.evidence import backend_open_file_exhaustion

    assert backend_open_file_exhaustion(text)
    assert not backend_open_file_exhaustion("regular backend overload; login deadline exceeded")


def test_resume_partial_new_policy_manifest_is_rerun_but_old_marker_refused(tmp_path, monkeypatch):
    import bench.user_capacity as module

    c = config(tmp_path)
    c["cells"] = ["go-net-http"]
    c["fixed_users"] = [100]
    p = tmp_path / "config.json"
    p.write_text(json.dumps(c))
    root = tmp_path / "results"
    partial = root / "nonce-0000"
    partial.mkdir(parents=True)
    saved = {
        "campaign": c,
        "trial": {"id": "nonce-0000", "cell": "go-net-http", "campaign_id": "nonce"},
    }
    (partial / "manifest.json").write_text(json.dumps(saved))
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(module, "campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(module, "database_campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(module, "generator_flags", lambda *_: [])
    monkeypatch.setattr(module, "journey_flags", lambda *_: [])
    monkeypatch.setattr(module, "await_rds", lambda *_: None)
    monkeypatch.setattr(module, "classify_journey", lambda *_: "pass")
    calls = []

    def trial(cfg, t, d):
        calls.append(t["id"])
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "campaign": cfg,
                    "trial": t,
                    "nofile": {"soft": 250000, "hard": 250000, "process_samples": 2},
                }
            )
        )
        return {"status": "measured", "reset_verified": True, "reasons": [], "journey_metrics": {}}

    monkeypatch.setattr(module, "run_trial", trial)
    result = run_user_capacity(p, root, resume=True)
    assert calls == ["nonce-0001"] and result["go-net-http"]["status"] == "fixed_load_complete"
    saved["campaign"] = {k: v for k, v in c.items() if k != "backend_nofile_limit"}
    (partial / "manifest.json").write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="resource policy"):
        run_user_capacity(p, root, resume=True)


@pytest.mark.parametrize("experiment", ["capacity", "fixed-load"])
@pytest.mark.parametrize("initial_hint", [None, 20000])
def test_resume_completed_cell_ignores_failed_preflight_without_actual_proof(
    tmp_path, monkeypatch, experiment, initial_hint
):
    import bench.user_capacity as module

    c = config(tmp_path)
    c["cells"] = ["go-net-http"]
    c["experiment_type"] = experiment
    c["initial_failing_hint"] = initial_hint
    p = tmp_path / "config.json"
    p.write_text(json.dumps(c))
    root = tmp_path / "results"
    for number, status in [(0, "failed"), (1, "measured")]:
        d = root / f"nonce-{number:04d}"
        d.mkdir(parents=True)
        t = {"id": d.name, "cell": "go-net-http", "campaign_id": "nonce", "users": 100}
        m = {"campaign": c, "trial": t}
        if status == "measured":
            m["native_runtime"] = {
                "container": {"nofile": {"soft": 250000, "hard": 250000, "process_samples": 2}}
            }
        (d / "manifest.json").write_text(json.dumps(m))
        (d / "result.json").write_text(
            json.dumps({"status": status, "reset_verified": True, "trial": t})
        )
    complete = {
        "status": "fixed_load_complete" if experiment == "fixed-load" else "bracket_found",
        "passing_users": 100,
        "failing_users": 200,
        "observations": [{"users": 100, "outcome": "pass", "trial_id": "nonce-0001"}],
    }
    filename = "fixed-load-results.json" if experiment == "fixed-load" else "capacity-results.json"
    (root / filename).write_text(json.dumps({"go-net-http": complete}))
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(module, "campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(module, "database_campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(module, "run_trial", lambda *_: pytest.fail("completed cell rerun"))
    assert run_user_capacity(p, root, resume=True)["go-net-http"] == complete


def test_completed_summary_without_valid_trial_references_is_rejected(tmp_path):
    from bench.user_capacity import _completed_trial_references

    (tmp_path / "capacity-results.json").write_text(
        json.dumps({"go": {"status": "bracket_found", "passing_users": 100, "failing_users": 200}})
    )
    with pytest.raises(ValueError, match="valid trial references"):
        _completed_trial_references(tmp_path)


def test_null_global_hint_passes_semantic_and_historical_wire_boundaries(tmp_path):
    from bench.records import Campaign, TrialManifest, checked_record

    c = config(tmp_path)
    c["initial_failing_hint"] = None
    validate_config(c)
    assert checked_record(c, Campaign) is c
    manifest = {"campaign": c, "trial": {"id": "original-0000"}}
    assert checked_record(manifest, TrialManifest) is manifest
    c["cell_starting_hints"] = {"go-net-http": {"start_users": 100, "initial_failing_hint": None}}
    with pytest.raises(ValueError, match="cell_starting_hints initial_failing_hint"):
        validate_config(c)


@pytest.mark.parametrize("endpoint_present", [True, False])
def test_majority_resume_rejects_bracket_without_failing_endpoint(
    tmp_path, monkeypatch, endpoint_present
):
    import bench.user_capacity as module

    c = config(tmp_path)
    c["experiment_type"] = "capacity"
    c["boundary_decision_policy"] = "majority-on-disagreement"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(c))
    root = tmp_path / "results"
    directory = root / "nonce-0000"
    directory.mkdir(parents=True)
    trial = {"id": directory.name, "cell": "go-net-http", "campaign_id": "nonce", "users": 100}
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "campaign": c,
                "trial": trial,
                "nofile": {"soft": 250000, "hard": 250000, "process_samples": 2},
            }
        )
    )
    (directory / "result.json").write_text(
        json.dumps(
            {
                "status": "measured",
                "reset_verified": True,
                "trial": trial,
            }
        )
    )
    outcome = {"outcome": "pass", "trial_id": directory.name, "users": 100}
    (directory / "capacity-outcome.json").write_text(json.dumps(outcome))
    saved_result = {"status": "bracket_found", "passing_users": 100, "observations": [outcome]}
    if endpoint_present:
        saved_result["failing_users"] = None
    results_path = root / "capacity-results.json"
    results_path.write_text(json.dumps({"go-net-http": saved_result}))
    original_results = results_path.read_bytes()
    status_path = root / "capacity-status.json"
    status_path.write_text(json.dumps({"status": "needs_attention"}))
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(module, "campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(module, "database_campaign_lock", lambda *_: nullcontext())
    monkeypatch.setattr(
        module, "run_trial", lambda *_: pytest.fail("inconsistent checkpoint reused")
    )

    with pytest.raises(ValueError, match="bracket.*failing endpoint"):
        run_user_capacity(config_path, root, resume=True)
    assert results_path.read_bytes() == original_results
    assert json.loads(status_path.read_text())["status"] == "needs_attention"
