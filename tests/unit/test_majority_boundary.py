import pytest
from bench.majority import majority_search, measurement_identity, validate_decision_handoff


def vote(outcome, number):
    return {"outcome": outcome, "trial_id": "trial-" + str(number), "metrics": {"sample": number}}


def test_split_failed_boundary_gets_third_then_pass_continues_above():
    old = {
        1000: [vote("pass", 0)],
        1937: [vote("pass", 1)],
        2000: [vote("fail", 2), vote("pass", 3)],
    }
    calls = []

    def probe(users):
        calls.append(users)
        return vote("pass" if users == 2000 else "fail", len(calls) + 10)

    result = majority_search(probe, old, start=1000, ceiling=4000, resolution=100)
    assert calls[0] == 2000 and any(n > 2000 for n in calls)
    assert result["passing_users"] >= 2000 and result["failing_users"] > 2000
    assert len(result["decisions"]["2000"]["votes"]) == 3
    assert result["decisions"]["2000"]["outcome"] == "pass"
    assert [x["outcome"] for x in result["decisions"]["2000"]["votes"]] == ["fail", "pass", "pass"]


def test_two_failures_need_no_third_and_restart_never_fourth():
    existing = {1000: [vote("pass", 0)], 1100: [vote("fail", 1), vote("fail", 2)]}
    result = majority_search(
        lambda _: pytest.fail("agreed boundary rerun"), existing, start=1000, resolution=100
    )
    assert result["status"] == "bracket_found"
    split = {1000: [vote("pass", 0)], 1100: [vote("fail", 1), vote("pass", 2), vote("fail", 3)]}
    result = majority_search(
        lambda _: pytest.fail("fourth vote"), split, start=1000, resolution=100
    )
    assert result["failing_users"] == 1100 and len(result["decisions"]["1100"]["votes"]) == 3


def test_invalid_is_not_a_vote_and_raw_metrics_are_not_pooled():
    existing = {1000: [vote("pass", 0)], 1100: [vote("fail", 1), vote("pass", 2)]}
    result = majority_search(lambda _: vote("invalid", 3), existing, start=1000, resolution=100)
    assert result["status"] == "invalid" and len(result["decisions"]["1100"]["votes"]) == 2
    assert all("metrics" not in d for d in result["decisions"].values())


def test_nonmonotonic_counts_are_flagged_without_guessing_ceiling():
    existing = {1000: [vote("fail", 0), vote("fail", 1)], 2000: [vote("pass", 2)]}
    r = majority_search(
        lambda _: pytest.fail("nonmonotonic search continued"), existing, start=1000
    )
    assert r["status"] == "unstable_boundary" and r["non_monotonic_counts"]


def test_audited_decision_handoff_requires_identical_measurement_inputs():
    original = {
        "mode": "aws",
        "ramp_seconds": 300,
        "settle_seconds": 0,
        "hold_seconds": 120,
        "success_fraction": 0.9,
        "authentication_fraction": 0.9,
        "cells": ["java"],
    }
    new = {**original, "boundary_decision_policy": "majority-on-disagreement"}
    with pytest.raises(ValueError, match="handoff"):
        validate_decision_handoff(original, new)
    new["decision_policy_handoff"] = {
        "from": "legacy",
        "to": "majority-on-disagreement",
        "measurement_identity_sha256": measurement_identity(new),
        "reason": "User requested third vote on disagreement",
    }
    validate_decision_handoff(original, new)
    changed = {**new, "hold_seconds": 240}
    with pytest.raises(ValueError):
        validate_decision_handoff(original, changed)


def test_queue_extension_is_only_prefix_append_and_chain_keeps_old_policy_hash():
    old = {
        "cells": ["python", "java"],
        "cell_starting_hints": {"java": {"start_users": 1000}},
        "expected_image_ids": {"java": "old"},
        "ramp_seconds": 300,
        "settle_seconds": 0,
        "hold_seconds": 120,
        "success_fraction": 0.9,
    }
    current = {
        **old,
        "cells": ["python", "java", "csharp"],
        "cell_starting_hints": {**old["cell_starting_hints"], "csharp": {"start_users": 1000}},
        "expected_image_ids": {**old["expected_image_ids"], "csharp": "new"},
        "boundary_decision_policy": "majority-on-disagreement",
    }
    current["decision_policy_handoff"] = {
        "from": "legacy",
        "to": "majority-on-disagreement",
        "measurement_identity_sha256": measurement_identity(old),
        "reason": "requested vote policy",
    }
    current["queue_extension_handoff"] = {
        "from_cells": old["cells"],
        "to_cells": current["cells"],
        "previous_measurement_identity_sha256": measurement_identity(old),
        "new_measurement_identity_sha256": measurement_identity(current),
        "reason": "append verified C#",
    }
    validate_decision_handoff(old, current)
    for change in (
        {"cells": ["java", "python", "csharp"]},
        {"cells": ["java", "csharp"]},
        {"hold_seconds": 240},
        {"expected_image_ids": {"java": "changed", "csharp": "new"}},
    ):
        with pytest.raises(ValueError):
            validate_decision_handoff(old, {**current, **change})


def test_campaign_handoff_reopens_java_reuses_raw_votes_and_does_not_redo_python(
    tmp_path, monkeypatch
):
    import json
    from contextlib import nullcontext

    from bench import user_capacity as module
    from bench.profiles import normalize_config

    c = normalize_config(
        {
            "mode": "local",
            "profile": "fast",
            "cells": ["python", "java"],
            "ramp_seconds": 300,
            "settle_seconds": 0,
            "hold_seconds": 120,
            "success_fraction": 0.9,
            "growth_strategy": "geometric",
            "max_users": 4000,
            "start_users": 1000,
        }
    )
    root = tmp_path / "results"
    root.mkdir()
    items = [
        ("python", 1000, "pass"),
        ("python", 1100, "fail"),
        ("python", 1100, "fail"),
        ("java", 1000, "pass"),
        ("java", 1937, "pass"),
        ("java", 2000, "fail"),
        ("java", 2000, "pass"),
    ]
    for idx, (name, users, outcome) in enumerate(items):
        d = root / f"nonce-{idx:04d}"
        d.mkdir()
        trial = {"id": d.name, "campaign_id": "nonce", "cell": name, "users": users}
        m = {
            "campaign": c,
            "trial": trial,
            "nofile": {"soft": 250000, "hard": 250000, "process_samples": 2},
        }
        (d / "manifest.json").write_text(json.dumps(m))
        (d / "result.json").write_text(
            json.dumps({"status": "measured", "reset_verified": True, "trial": trial})
        )
        (d / "capacity-outcome.json").write_text(
            json.dumps({"outcome": outcome, "trial_id": d.name, "metrics": {"raw_ordinal": idx}})
        )
    original = {p: str(p.read_text()) for p in root.glob("*/*.json")}
    python = {
        "status": "bracket_found",
        "passing_users": 1000,
        "failing_users": 1100,
        "observations": [{"outcome": "pass", "trial_id": "nonce-0000"}],
    }
    java = {
        "status": "unstable_boundary",
        "passing_users": 1937,
        "failing_users": 2000,
        "observations": [
            {"outcome": "fail", "trial_id": "nonce-0005"},
            {"outcome": "pass", "trial_id": "nonce-0006"},
        ],
    }
    (root / "capacity-results.json").write_text(json.dumps({"python": python, "java": java}))
    c = {
        **c,
        "boundary_decision_policy": "majority-on-disagreement",
        "decision_policy_handoff": {
            "from": "legacy",
            "to": "majority-on-disagreement",
            "measurement_identity_sha256": measurement_identity(c),
            "reason": "user requested majority",
        },
    }
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(c))
    calls = []
    monkeypatch.setattr(module, "validate_config", lambda _: None)
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(module, "campaign_lock", lambda _: nullcontext())
    monkeypatch.setattr(module, "database_campaign_lock", lambda _: nullcontext())

    def fresh(cfg, trial, d):
        assert trial["cell"] == "java"
        calls.append(trial["users"])
        d.mkdir()
        return {
            "status": "measured",
            "reset_verified": True,
            "reasons": [],
            "journey_metrics": {"answer": "pass" if trial["users"] == 2000 else "fail"},
        }

    monkeypatch.setattr(module, "run_trial", fresh)
    monkeypatch.setattr(module, "await_rds", lambda *_: None)
    monkeypatch.setattr(module, "generator_flags", lambda *_: [])
    monkeypatch.setattr(module, "journey_flags", lambda *_: [])
    monkeypatch.setattr(module, "classify_journey", lambda metric, *_: metric["answer"])
    result = module.run_user_capacity(cfg, root, resume=True)
    assert result["python"] == python and calls[0] == 2000 and any(n > 2000 for n in calls)
    assert result["java"]["passing_users"] >= 2000 and result["java"]["failing_users"] > 2000
    assert all(p.read_text() == value for p, value in original.items())
    assert (root / "decision-policy-handoff.json").exists() and (
        root / "boundary-decisions.json"
    ).exists()
