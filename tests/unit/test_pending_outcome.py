import json

import pytest
from bench.control import qualified_cache
from bench.majority import majority_search
from bench.user_capacity import finalize_pending_outcomes


def write(root, number, outcome=None):
    d = root / f"nonce-{number:04d}"
    d.mkdir(parents=True)
    trial = {"id": d.name, "cell": "java", "users": 2000}
    (d / "result.json").write_text(
        json.dumps(
            {
                "status": "measured",
                "reset_verified": True,
                "trial": trial,
                "reasons": [],
                "journey_metrics": {
                    "attempted": 100,
                    "succeeded": 100,
                    "active_users_max": 2000,
                    "authenticated_users": 2000,
                },
            }
        )
    )
    if outcome:
        (d / "capacity-outcome.json").write_text(
            json.dumps({"outcome": outcome, "trial_id": d.name, "metrics": {"original": number}})
        )
    return d


def test_cache_fails_closed_when_measured_third_vote_is_not_finalized(tmp_path):
    write(tmp_path, 0, "fail")
    write(tmp_path, 1, "pass")
    write(tmp_path, 2)
    with pytest.raises(RuntimeError, match="awaits qualification"):
        qualified_cache(tmp_path)


def test_crash_after_third_result_is_finalized_without_fourth_measurement(tmp_path, monkeypatch):
    import bench.user_capacity as module

    write(tmp_path, 0, "fail")
    write(tmp_path, 1, "pass")
    third = write(tmp_path, 2)
    checks = []
    monkeypatch.setattr(module, "await_rds", lambda *args: checks.append("rds"))
    monkeypatch.setattr(module, "generator_flags", lambda *args: checks.append("generator") or [])
    monkeypatch.setattr(module, "journey_flags", lambda *args: checks.append("journey") or [])
    finalize_pending_outcomes({"success_fraction": 0.9, "authentication_fraction": 0.9}, tmp_path)
    result = json.loads((third / "capacity-outcome.json").read_text())
    assert result["outcome"] == "pass" and result["finalized_after_restart"]
    assert checks == ["rds", "generator", "journey"]
    votes = list(qualified_cache(tmp_path)[("java", 2000)])
    r = majority_search(
        lambda _: pytest.fail("fourth measurement"), {2000: votes}, start=2000, ceiling=2000
    )
    assert r["status"] == "ceiling_reached" and len(r["decisions"]["2000"]["votes"]) == 3


def test_recovery_does_not_fabricate_validity_when_telemetry_is_missing(tmp_path, monkeypatch):
    import bench.user_capacity as module

    d = write(tmp_path, 0)
    monkeypatch.setattr(module, "await_rds", lambda *_: None)
    monkeypatch.setattr(module, "generator_flags", lambda *_: ["host evidence unavailable"])
    finalize_pending_outcomes({"success_fraction": 0.9, "authentication_fraction": 0.9}, tmp_path)
    assert json.loads((d / "capacity-outcome.json").read_text())["outcome"] == "invalid"
    assert not qualified_cache(tmp_path)


def test_recovery_requires_saved_rds_coverage_in_aws_mode(tmp_path, monkeypatch):
    import bench.user_capacity as module

    d = write(tmp_path, 0)
    monkeypatch.setattr(module, "await_rds", lambda *_: None)
    monkeypatch.setattr(module, "generator_flags", lambda *_: [])
    finalize_pending_outcomes(
        {"mode": "aws", "success_fraction": 0.9, "authentication_fraction": 0.9}, tmp_path
    )
    result = json.loads((d / "capacity-outcome.json").read_text())
    assert (
        result["outcome"] == "invalid" and "RDS measurement interval missing" in result["reasons"]
    )
