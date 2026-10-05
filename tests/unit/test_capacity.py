import pytest
from bench.capacity import search_capacity


def test_ten_thousand_steps_then_binary_search():
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "pass" if users <= 23400 else "fail"}

    r = search_capacity(probe, start=10000, step=10000, ceiling=50000, resolution=100)
    assert calls[:3] == [10000, 20000, 30000]
    assert r["passing_users"] <= 23400 < r["failing_users"]
    assert r["failing_users"] - r["passing_users"] <= 100


def test_first_level_failure_searches_zero_to_ten_thousand():
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "pass" if users <= 1200 else "fail"}

    r = search_capacity(probe)
    assert calls[:2] == [10000, 5000]
    assert r["passing_users"] <= 1200 < r["failing_users"]


def test_generator_failure_never_becomes_backend_capacity():
    r = search_capacity(
        lambda users: {"outcome": "invalid", "reasons": ["generator out of memory"]}
    )
    assert r["status"] == "invalid" and r["failing_users"] is None


def test_fixture_ceiling_is_a_lower_bound_not_a_failure():
    r = search_capacity(lambda users: {"outcome": "pass"}, ceiling=50000)
    assert r["passing_users"] == 50000 and r["failing_users"] is None
    assert r["status"] == "ceiling_reached"


def test_next_runtime_retests_previous_success_and_can_search_downward():
    from bench.user_capacity import next_start_users

    start = next_start_users({"status": "bracket_found", "passing_users": 23437}, 10000)
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "pass" if users <= 19000 else "fail"}

    result = search_capacity(probe, start=start)
    assert calls[0] == 23437
    assert result["passing_users"] <= 19000 < result["failing_users"]


def test_only_valid_positive_previous_success_seeds_next_runtime():
    from bench.user_capacity import next_start_users

    assert next_start_users(None, 10000) == 10000
    assert next_start_users({"status": "invalid", "passing_users": 20000}, 10000) == 10000
    assert next_start_users({"status": "bracket_found", "passing_users": 0}, 10000) == 10000
    assert next_start_users({"status": "ceiling_reached", "passing_users": 50000}, 10000) == 50000


@pytest.mark.parametrize("unstable", [False, True])
def test_resume_preserves_completed_runtime_and_starts_next_at_its_success(
    tmp_path, monkeypatch, unstable
):
    import json
    from contextlib import nullcontext

    from bench import user_capacity as module

    config = {
        "mode": "local",
        "cells": ["old", "unstable", "new"] if unstable else ["old", "new"],
        "start_users": 10000,
        "step_users": 10000,
        "max_users": 50000,
        "resolution_users": 100,
        "ramp_seconds": 1,
        "settle_seconds": 1,
        "hold_seconds": 1,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    output = tmp_path / "results"
    trial_dir = output / "abc-0003"
    trial_dir.mkdir(parents=True)
    (trial_dir / "manifest.json").write_text(
        json.dumps({"campaign": config, "trial": {"id": "abc-0003", "campaign_id": "abc"}})
    )
    previous = {"status": "bracket_found", "passing_users": 23000, "failing_users": 23080}
    completed = {"old": previous}
    if unstable:
        completed["unstable"] = {
            "status": "unstable_or_invalid_boundary",
            "passing_users": 23500,
            "failing_users": 23600,
            "confirmations": {"passing_users": [{"outcome": "pass"}, {"outcome": "fail"}]},
        }
    (output / "capacity-results.json").write_text(json.dumps(completed))
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(module, "validate_config", lambda _: None)
    monkeypatch.setattr(module, "campaign_lock", lambda _: nullcontext())
    monkeypatch.setattr(module, "database_campaign_lock", lambda _: nullcontext())
    calls = []

    def search(probe, start, step, ceiling, resolution, next_candidate=None):
        calls.append(start)
        assert next_candidate == (23600 if unstable else 23080)
        return {"status": "ceiling_reached", "passing_users": ceiling}

    monkeypatch.setattr(module, "search_capacity", search)
    result = module.run_user_capacity(path, output, resume=True)
    assert calls == [23000]
    assert result["old"] == previous
    assert result["new"]["starting_users"] == 23000


def test_previous_boundary_is_retested_and_growth_is_adaptive():
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "pass" if users <= 2900 else "fail"}

    r = search_capacity(probe, start=2031, next_candidate=2265)
    assert calls[:4] == [2031, 2265, 2733, 3669]
    assert r["passing_users"] <= 2900 < r["failing_users"]
    assert r["failing_users"] - r["passing_users"] <= 100


def test_invalid_repeat_evidence_stops_but_unstable_backend_repeats_do_not():
    from bench.user_capacity import invalid_boundary

    r = {
        "status": "unstable_or_invalid_boundary",
        "confirmations": {"passing_users": [{"outcome": "pass"}, {"outcome": "fail"}]},
    }
    assert not invalid_boundary(r)
    r["confirmations"]["passing_users"].append({"outcome": "invalid"})
    assert invalid_boundary(r)
