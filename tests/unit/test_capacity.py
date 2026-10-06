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
        "backend_nofile_limit": 250000,
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
        json.dumps(
            {
                "campaign": config,
                "trial": {"id": "abc-0003", "campaign_id": "abc", "cell": "old"},
                "nofile": {"soft": 250000, "hard": 250000, "process_samples": 2},
            }
        )
    )
    previous = {
        "status": "bracket_found",
        "passing_users": 23000,
        "failing_users": 23080,
        "observations": [{"outcome": "pass", "trial_id": "abc-0003", "users": 23000}],
    }
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


def test_fast_confirmation_retests_only_failure_once():
    from bench.user_capacity import confirm_boundary

    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "fail"}

    policy = {"passing_repeats": 0, "failing_repeats": 1, "stop_on_contradiction": True}
    result = {"status": "bracket_found", "passing_users": 2000, "failing_users": 2075}
    confirm_boundary(probe, result, policy)
    assert calls == [2075]
    assert "passing_users" not in result["confirmations"]
    assert result["status"] == "bracket_found"
    assert result["confirmation_policy"] == policy


def test_failure_retest_that_passes_marks_boundary_unstable():
    from bench.user_capacity import confirm_boundary

    result = {"status": "bracket_found", "passing_users": 2000, "failing_users": 2075}
    confirm_boundary(
        lambda _: {"outcome": "pass"},
        result,
        {"passing_repeats": 0, "failing_repeats": 1, "stop_on_contradiction": True},
    )
    assert result["status"] == "unstable_boundary"


def test_fast_confirmation_keeps_invalid_evidence_distinct():
    from bench.user_capacity import confirm_boundary

    result = {"status": "bracket_found", "passing_users": 2000, "failing_users": 2075}
    confirm_boundary(
        lambda _: {"outcome": "invalid"},
        result,
        {"passing_repeats": 0, "failing_repeats": 1, "stop_on_contradiction": True},
    )
    assert result["status"] == "invalid"


def test_faster_policy_does_not_hide_existing_contradictory_trials():
    from bench.user_capacity import confirm_boundary

    result = {
        "status": "bracket_found",
        "passing_users": 2000,
        "failing_users": 2075,
        "prior_endpoint_trials": {"passing_users": [{"outcome": "pass"}, {"outcome": "fail"}]},
    }
    confirm_boundary(
        lambda _: {"outcome": "fail"},
        result,
        {"passing_repeats": 0, "failing_repeats": 1, "stop_on_contradiction": True},
    )
    assert result["status"] == "unstable_boundary"


def test_geometric_growth_uses_measured_hint_then_doubles_for_unfamiliar_stack():
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "pass" if users <= 18000 else "fail"}

    result = search_capacity(probe, start=1309, next_candidate=1409, growth_strategy="geometric")
    assert calls[:6] == [1309, 1409, 2818, 5636, 11272, 22544]
    assert result["passing_users"] <= 18000 < result["failing_users"]
    assert result["failing_users"] - result["passing_users"] <= 100
    assert len(calls) == len(set(calls))


def test_other_runtime_hint_is_not_assumed_to_pass():
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "pass" if users <= 750 else "fail"}

    result = search_capacity(probe, start=1309, next_candidate=1409, growth_strategy="geometric")
    assert calls[:2] == [1309, 654]
    assert 1409 not in calls
    assert result["passing_users"] <= 750 < result["failing_users"]


def test_resume_refuses_changed_native_binary_before_reusing_measurements(tmp_path, monkeypatch):
    import json

    from bench import user_capacity

    config = {
        "mode": "local",
        "backend_nofile_limit": 250000,
        "cells": ["go-net-http"],
        "start_users": 1309,
        "step_users": 10000,
        "max_users": 50000,
        "resolution_users": 100,
        "ramp_seconds": 300,
        "settle_seconds": 120,
        "hold_seconds": 300,
        "expected_image_ids": {"go-net-http": "sha256:" + "a" * 64},
    }
    original = {**config, "expected_image_ids": {"go-net-http": "sha256:" + "b" * 64}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    output = tmp_path / "results"
    trial = output / "nonce-0000"
    trial.mkdir(parents=True)
    (trial / "manifest.json").write_text(
        json.dumps({"campaign": original, "trial": {"id": "nonce-0000", "campaign_id": "nonce"}})
    )
    monkeypatch.setattr(user_capacity, "validate_config", lambda _: None)
    with pytest.raises(ValueError, match="expected_image_ids"):
        user_capacity.run_user_capacity(path, output, resume=True)
