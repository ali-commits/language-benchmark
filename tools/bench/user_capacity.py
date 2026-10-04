"""Run stateful concurrent-user probes with guarded resets and bracket confirmation."""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .capacity import search_capacity
from .journeys import classify_journey, generator_window
from .manifest import atomic_json
from .report import generator_flags
from .runner import campaign_lock, database_campaign_lock, run_trial
from .telemetry import rds_metrics, validate_rds_evidence


def validate_config(c: dict):
    if c.get("mode") not in ("local", "aws") or not c.get("cells"):
        raise ValueError("mode and runtime cells required")
    for key in (
        "start_users",
        "step_users",
        "max_users",
        "resolution_users",
        "ramp_seconds",
        "hold_seconds",
        "timeout_seconds",
    ):
        if type(c.get(key)) is not int or c[key] < 1:
            raise ValueError("invalid " + key)
    if c["start_users"] > c["max_users"] or c["max_users"] > 50000:
        raise ValueError("search exceeds the fixed 50,000-account fixture")
    if (
        not 0 <= c.get("settle_seconds", -1) <= 600
        or not 0 <= c.get("wait_min", -1) <= c.get("wait_max", 0) <= 300
    ):
        raise ValueError("invalid waits/settling")
    if (
        c["ramp_seconds"] + c["settle_seconds"] + c["hold_seconds"] > 3600
        or c["timeout_seconds"] > 30
    ):
        raise ValueError("trial duration exceeds token/runtime bounds")
    if (
        not 0.9 <= c.get("success_fraction", 0) <= 1
        or not 1 <= c.get("confirmation_repeats", 0) <= 10
    ):
        raise ValueError("invalid response target/confirmations")
    if not c.get("database_allowlist", "").startswith("language_benchmark"):
        raise ValueError("dedicated benchmark database required")
    fixture = json.loads(Path(c["fixture"]).read_text())
    if fixture["counts"]["users"] < c["max_users"]:
        raise ValueError("fixture cannot provide enough unique accounts")


def await_rds(c: dict, result: dict, directory: Path):
    if (
        c["mode"] != "aws"
        or result.get("status") != "measured"
        or not result.get("measurement_started_epoch")
        or not result.get("measurement_finished_epoch")
    ):
        return
    start = result["measurement_started_epoch"]
    end = result["measurement_finished_epoch"]
    deadline = time.monotonic() + c.get("rds_wait_seconds", 180)
    while True:
        try:
            data = rds_metrics(
                c["rds_identifier"],
                c["region"],
                datetime.fromtimestamp(start, timezone.utc),
                datetime.fromtimestamp(end, timezone.utc),
            )
            problems = validate_rds_evidence(data, start, end)
            atomic_json(directory / "rds.json", data)
            result["reasons"] = [
                r for r in result.get("reasons", []) if not r.startswith("RDS ")
            ] + problems
            atomic_json(directory / "result.json", result)
            if not problems:
                return
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return
        time.sleep(30)


def run_user_capacity(config_path: Path, output_dir: Path) -> dict:
    c = json.loads(config_path.read_text())
    validate_config(c)
    c.update(
        operation="user-journey",
        warmup_seconds=0,
        measure_seconds=c["ramp_seconds"] + c["settle_seconds"] + c["hold_seconds"],
    )
    c["campaign_instance"] = uuid.uuid4().hex
    output_dir.mkdir(parents=True, exist_ok=False)
    results = {}
    ordinal = 0
    with (
        campaign_lock(Path("artifacts/campaign.lock")),
        database_campaign_lock(os.environ["DATABASE_URL"]),
    ):
        for cell in c["cells"]:

            def probe(users):
                nonlocal ordinal
                trial = {
                    "id": c["campaign_instance"][:12] + f"-{ordinal:04d}",
                    "campaign_id": c["campaign_instance"],
                    "cell": cell,
                    "users": users,
                    "rate": 0,
                    "seed": c.get("seed", 20261004),
                    "repetition": 0,
                }
                ordinal += 1
                directory = output_dir / trial["id"]
                atomic_json(
                    output_dir / "capacity-status.json",
                    {
                        "status": "testing",
                        "cell": cell,
                        "target_users": users,
                        "probe": ordinal,
                        "updated_epoch": time.time(),
                    },
                )
                r = run_trial(c, trial, directory)
                await_rds(c, r, directory)
                reasons = list(r.get("reasons", []))
                if r.get("status") != "measured" or not r.get("reset_verified"):
                    reasons.append("measurement or cleanup incomplete")
                reasons += generator_flags(
                    directory / "telemetry.jsonl",
                    *generator_window(r),
                )
                m = r.get("journey_metrics", {})
                outcome = classify_journey(m, users, reasons, c["success_fraction"])
                summary = {
                    "outcome": outcome,
                    "reasons": sorted(set(reasons)),
                    "metrics": m,
                    "trial_id": trial["id"],
                }
                atomic_json(directory / "capacity-outcome.json", summary)
                return summary

            result = search_capacity(
                probe, c["start_users"], c["step_users"], c["max_users"], c["resolution_users"]
            )
            # Search is exploratory; repeated endpoints determine whether the bracket is stable.
            if result["status"] == "bracket_found":
                confirmations = {}
                for bound in ("passing_users", "failing_users"):
                    users = result[bound]
                    if not users:
                        continue
                    confirmations[bound] = []
                    for _ in range(c["confirmation_repeats"]):
                        item = probe(users)
                        confirmations[bound].append(item)
                        if item["outcome"] == "invalid":
                            break
                    if any(item["outcome"] == "invalid" for item in confirmations[bound]):
                        break
                result["confirmations"] = confirmations
                stable = all(
                    r["outcome"] == "pass" for r in confirmations.get("passing_users", [])
                ) and all(r["outcome"] == "fail" for r in confirmations.get("failing_users", []))
                if not stable:
                    result["status"] = "unstable_or_invalid_boundary"
            results[cell] = result
            atomic_json(output_dir / "capacity-results.json", results)
            if result["status"] in ("invalid", "unstable_or_invalid_boundary"):
                break
    atomic_json(
        output_dir / "capacity-status.json",
        {
            "status": "complete"
            if all(r["status"] in ("bracket_found", "ceiling_reached") for r in results.values())
            else "needs_attention",
            "updated_epoch": time.time(),
        },
    )
    return results
