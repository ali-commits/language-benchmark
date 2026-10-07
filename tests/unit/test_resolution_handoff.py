import json
from contextlib import nullcontext

import pytest
from bench.majority import measurement_identity, validate_decision_handoff
from bench.profiles import normalize_config


def config():
    return normalize_config(
        {
            "mode": "local",
            "profile": "fast",
            "cells": ["python", "java"],
            "ramp_seconds": 300,
            "settle_seconds": 0,
            "hold_seconds": 120,
            "success_fraction": 0.9,
            "growth_strategy": "geometric",
            "start_users": 1000,
            "max_users": 4000,
            "boundary_decision_policy": "majority-on-disagreement",
        }
    )


def refine(c):
    new = {**c, "resolution_users": 50}
    new["resolution_handoff"] = {
        "from": 100,
        "to": 50,
        "measurement_identity_sha256": measurement_identity(c),
        "reason": "user requested gaps at most 50",
    }
    return new


def test_resolution_only_handoff_preserves_every_other_measurement_input():
    old = config()
    new = refine(old)
    assert validate_decision_handoff(old, new)["resolution_users"] == 100
    assert validate_decision_handoff(new, new)["resolution_users"] == 50
    for change in (
        {"hold_seconds": 240},
        {"success_fraction": 0.99},
        {"resolution_users": 25},
        {"seed": 999},
        {"cells": ["java", "python"]},
    ):
        with pytest.raises(ValueError):
            validate_decision_handoff(old, {**new, **change})
    bad = {
        **new,
        "resolution_handoff": {**new["resolution_handoff"], "measurement_identity_sha256": "lie"},
    }
    with pytest.raises(ValueError):
        validate_decision_handoff(old, bad)


def test_resolution_composes_with_original_queue_and_decision_receipts():
    original_cells = ["python", "java", "php", "go", "rust", "node", "bun"]
    old = {
        **config(),
        "boundary_decision_policy": "legacy",
        "cells": original_cells,
        "expected_image_ids": {"java": "old"},
    }
    current = {
        **old,
        "boundary_decision_policy": "majority-on-disagreement",
        "cells": [*original_cells, "csharp"],
        "expected_image_ids": {"java": "old", "csharp": "new"},
    }
    current["decision_policy_handoff"] = {
        "from": "legacy",
        "to": "majority-on-disagreement",
        "measurement_identity_sha256": measurement_identity(old),
        "reason": "original majority change",
    }
    current["queue_extension_handoff"] = {
        "from_cells": old["cells"],
        "to_cells": current["cells"],
        "previous_measurement_identity_sha256": measurement_identity(old),
        "new_measurement_identity_sha256": measurement_identity(current),
        "reason": "append verified C#",
    }
    new = refine(current)
    projected = validate_decision_handoff(old, new)
    assert projected["cells"] == old["cells"] and projected["resolution_users"] == 100
    assert validate_decision_handoff(current, new)["resolution_users"] == 100
    assert validate_decision_handoff(new, new)["resolution_users"] == 50
    for change in (
        {"expected_image_ids": {"java": "changed", "csharp": "new"}},
        {"authentication_fraction": 0.99},
        {"ramp_seconds": 180},
    ):
        # Even a new resolution hash cannot authorize changing earlier controls.
        changed = {**current, **change}
        with pytest.raises(ValueError):
            validate_decision_handoff(old, refine(changed))


@pytest.mark.parametrize(
    "receipt", [None, {}, {"from": True, "to": 50}, {"from": 100, "to": 50, "reason": ""}]
)
def test_refinement_requires_explicit_valid_resolution_receipt(receipt):
    old = config()
    with pytest.raises(ValueError, match="resolution change requires audited"):
        validate_decision_handoff(old, {**refine(old), "resolution_handoff": receipt})


def test_resolution_receipt_cannot_authorize_widening_or_other_resolution():
    current = refine(config())
    for resolution in (100, 25):
        with pytest.raises(ValueError):
            validate_decision_handoff(current, {**current, "resolution_users": resolution})


@pytest.mark.parametrize("new_outcomes", [("pass",), ("fail", "fail"), ("fail", "pass", "pass")])
def test_refinement_reopens_only_wide_bracket_and_reuses_agreeing_failure_votes(
    tmp_path, monkeypatch, new_outcomes
):
    from bench import user_capacity as module

    old = config()
    root = tmp_path / "results"
    root.mkdir()
    records = [
        ("python", 1000, "pass"),
        ("python", 1050, "fail"),
        ("python", 1050, "fail"),
        ("java", 1937, "pass"),
        ("java", 2000, "fail"),
        ("java", 2000, "fail"),
    ]
    for idx, (cell, users, outcome) in enumerate(records):
        d = root / f"nonce-{idx:04d}"
        d.mkdir()
        trial = {"id": d.name, "cell": cell, "users": users, "campaign_id": "nonce"}
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "campaign": old,
                    "trial": trial,
                    "nofile": {"soft": 250000, "hard": 250000, "process_samples": 2},
                }
            )
        )
        (d / "result.json").write_text(
            json.dumps({"status": "measured", "reset_verified": True, "trial": trial})
        )
        (d / "capacity-outcome.json").write_text(
            json.dumps({"outcome": outcome, "trial_id": d.name})
        )
    completed = {
        cell: {
            "status": "bracket_found",
            "passing_users": low,
            "failing_users": high,
            "resolution": 100,
            "observations": [{"outcome": "pass", "trial_id": identity}],
        }
        for cell, low, high, identity in [
            ("python", 1000, 1050, "nonce-0000"),
            ("java", 1937, 2000, "nonce-0003"),
        ]
    }
    (root / "capacity-results.json").write_text(json.dumps(completed))
    new = refine(old)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(new))
    calls = []
    remaining = iter(new_outcomes)
    monkeypatch.setattr(module, "validate_config", lambda _: None)
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(module, "campaign_lock", lambda _: nullcontext())
    monkeypatch.setattr(module, "database_campaign_lock", lambda _: nullcontext())

    def trial(c, t, d):
        calls.append((t["cell"], t["users"]))
        d.mkdir()
        result = {
            "status": "measured",
            "reset_verified": True,
            "trial": t,
            "reasons": [],
            "journey_metrics": {},
        }
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "campaign": c,
                    "trial": t,
                    "nofile": {"soft": 250000, "hard": 250000, "process_samples": 2},
                }
            )
        )
        (d / "result.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr(module, "run_trial", trial)
    monkeypatch.setattr(module, "await_rds", lambda *_: None)
    monkeypatch.setattr(module, "generator_flags", lambda *_: [])
    monkeypatch.setattr(module, "journey_flags", lambda *_: [])
    monkeypatch.setattr(module, "classify_journey", lambda *_: next(remaining))
    result = module.run_user_capacity(p, root, resume=True)
    assert calls == [("java", 1968)] * len(new_outcomes)
    assert result["python"] == completed["python"]
    assert result["java"]["failing_users"] - result["java"]["passing_users"] <= 50
    assert len(result["java"]["decisions"]["2000"]["votes"]) == 2
    assert (root / "resolution-policy-handoff.json").exists()
    # Old100 and new50 original manifests coexist; the completed refined bracket
    # must survive another controller restart without any further request.
    assert module.run_user_capacity(p, root, resume=True) == result
    assert len(calls) == len(new_outcomes)
