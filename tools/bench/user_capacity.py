"""Run stateful concurrent-user probes with guarded resets and bracket confirmation."""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .capacity import search_capacity
from .control import qualified_cache, recover_hosts, retry_probe
from .journeys import classify_journey, generator_window, journey_flags
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
    if "confirmation_policy" in c:
        policy = c["confirmation_policy"]
        if (
            not isinstance(policy, dict)
            or set(policy) != {"passing_repeats", "failing_repeats", "stop_on_contradiction"}
            or any(
                type(policy.get(k)) is not int or not 0 <= policy[k] <= 10
                for k in ("passing_repeats", "failing_repeats")
            )
            or policy["failing_repeats"] < 1
            or type(policy.get("stop_on_contradiction")) is not bool
        ):
            raise ValueError("invalid confirmation execution policy")
    if c.get("growth_strategy", "adaptive") not in ("adaptive", "geometric"):
        raise ValueError("unknown search growth strategy")
    hint = c.get("initial_failing_hint")
    if hint is not None and (
        type(hint) is not int or not c["start_users"] < hint <= c["max_users"]
    ):
        raise ValueError("invalid measured starting hint")
    if not c.get("database_allowlist", "").startswith("language_benchmark"):
        raise ValueError("dedicated benchmark database required")
    if (
        type(c.get("infrastructure_attempts", 1)) is not int
        or not 1 <= c.get("infrastructure_attempts", 1) <= 3
        or type(c.get("recovery_delay_seconds", 30)) is not int
        or not 0 <= c.get("recovery_delay_seconds", 30) <= 120
    ):
        raise ValueError("invalid bounded recovery settings")
    if type(c.get("auto_build_images", False)) is not bool:
        raise ValueError("auto_build_images must be boolean")
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


def next_start_users(previous: dict | None, default: int) -> int:
    if previous and previous.get("status") in ("bracket_found", "ceiling_reached"):
        return previous.get("passing_users") or default
    return default


def invalid_boundary(result: dict) -> bool:
    return result.get("status") == "invalid" or any(
        item.get("outcome") == "invalid"
        for items in result.get("confirmations", {}).values()
        for item in items
    )


def confirm_boundary(probe, result: dict, policy: dict):
    result["confirmation_policy"] = dict(policy)
    confirmations = result["confirmations"] = {}
    contradictory = False
    for bound, expected in (("passing_users", "pass"), ("failing_users", "fail")):
        users = result[bound]
        repeats = policy["passing_repeats" if bound == "passing_users" else "failing_repeats"]
        if not users or repeats == 0:
            continue
        confirmations[bound] = []
        for _ in range(repeats):
            item = probe(users)
            confirmations[bound].append(item)
            if item["outcome"] == "invalid":
                result["status"] = "invalid"
                return
            if item["outcome"] != expected:
                contradictory = True
                if policy["stop_on_contradiction"]:
                    result["status"] = "unstable_boundary"
                    return
    for bound, expected in (("passing_users", "pass"), ("failing_users", "fail")):
        if any(
            item["outcome"] != expected
            for item in result.get("prior_endpoint_trials", {}).get(bound, [])
        ):
            contradictory = True
    if contradictory:
        result["status"] = "unstable_boundary"


def run_user_capacity(
    config_path: Path, output_dir: Path, resume: bool = False, retry_invalid: bool = False
) -> dict:
    c = json.loads(config_path.read_text())
    validate_config(c)
    c.update(
        operation="user-journey",
        warmup_seconds=0,
        measure_seconds=c["ramp_seconds"] + c["settle_seconds"] + c["hold_seconds"],
    )
    c.setdefault(
        "confirmation_policy",
        {
            "passing_repeats": c.get("confirmation_repeats", 5),
            "failing_repeats": c.get("confirmation_repeats", 5),
            "stop_on_contradiction": False,
        },
    )
    c["campaign_instance"] = uuid.uuid4().hex
    if resume:
        manifests = sorted(output_dir.glob("*/manifest.json"))
        if not manifests:
            raise ValueError("resume requires original trial manifests")
        original = json.loads(manifests[0].read_text())
        for key in (
            "cells",
            "start_users",
            "step_users",
            "max_users",
            "resolution_users",
            "ramp_seconds",
            "settle_seconds",
            "hold_seconds",
            "wait_min",
            "wait_max",
            "timeout_seconds",
            "success_fraction",
            "confirmation_repeats",
            "seed",
            "fixture",
            "expected_image_ids",
        ):
            if original["campaign"].get(key) != c.get(key):
                raise ValueError("resume configuration differs: " + key)
        from .models import NativeCell
        from .native_runtime import load_runtime_cell, registry_metadata

        for manifest_path in manifests:
            stored = json.loads(manifest_path.read_text())
            lock = stored.get("runtime_lock")
            if lock:
                runtime = load_runtime_cell(stored["trial"]["cell"])
                if not isinstance(runtime, NativeCell) or lock != registry_metadata(runtime):
                    raise ValueError("resume native runtime registry differs from measured binary")
        c["campaign_instance"] = original["trial"]["campaign_id"]
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
    results = {}
    ordinal = 0
    previous = None
    previous_upper = c.get("initial_failing_hint")
    cached = qualified_cache(output_dir) if resume else {}
    prior_trials = {key: list(queue) for key, queue in cached.items()}
    with (
        campaign_lock(Path("artifacts/campaign.lock")),
        database_campaign_lock(os.environ["DATABASE_URL"]),
    ):
        if resume:
            results_file = output_dir / "capacity-results.json"
            results = json.loads(results_file.read_text()) if results_file.exists() else {}
            if any(invalid_boundary(r) for r in results.values()):
                if not retry_invalid:
                    raise ValueError("cannot resume invalid evidence without resolving its cause")
                results = {cell: r for cell, r in results.items() if not invalid_boundary(r)}
            ordinal = 1 + max(
                int(json.loads(p.read_text())["trial"]["id"].rsplit("-", 1)[1])
                for p in output_dir.glob("*/manifest.json")
            )
        for cell in c["cells"]:
            if cell in results:
                if results[cell]["status"] in ("bracket_found", "ceiling_reached"):
                    previous = results[cell]
                previous_upper = results[cell].get("failing_users")
                continue
            start_users = next_start_users(previous, c["start_users"])

            def probe_once(users):
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
                reasons += journey_flags(m)
                outcome = classify_journey(m, users, reasons, c["success_fraction"])
                summary = {
                    "outcome": outcome,
                    "lifecycle_status": r.get("status"),
                    "reasons": sorted(set(reasons)),
                    "metrics": m,
                    "trial_id": trial["id"],
                }
                atomic_json(directory / "capacity-outcome.json", summary)
                return summary

            def probe(users):
                queue = cached.get((cell, users))
                if queue:
                    return {**queue.popleft(), "reused_after_restart": True}
                return retry_probe(
                    probe_once,
                    users,
                    lambda: recover_hosts(c),
                    attempts=c.get("infrastructure_attempts", 1),
                    delay=c.get("recovery_delay_seconds", 30),
                )

            result = search_capacity(
                probe,
                start_users,
                c["step_users"],
                c["max_users"],
                c["resolution_users"],
                next_candidate=previous_upper,
                **({"growth_strategy": c["growth_strategy"]} if "growth_strategy" in c else {}),
            )
            # Search is exploratory; repeated endpoints determine whether the bracket is stable.
            if result["status"] == "bracket_found":
                result["prior_endpoint_trials"] = {
                    bound: prior_trials.get((cell, result[bound]), [])
                    for bound in ("passing_users", "failing_users")
                }
                confirm_boundary(probe, result, c["confirmation_policy"])
            result["starting_users"] = start_users
            results[cell] = result
            if result["status"] in ("bracket_found", "ceiling_reached"):
                previous = result
            previous_upper = result.get("failing_users")
            atomic_json(output_dir / "capacity-results.json", results)
            if invalid_boundary(result):
                break
    atomic_json(
        output_dir / "capacity-status.json",
        {
            "status": "complete"
            if all(r["status"] in ("bracket_found", "ceiling_reached") for r in results.values())
            else "complete_with_unstable_boundaries"
            if len(results) == len(c["cells"])
            and not any(invalid_boundary(r) for r in results.values())
            else "needs_attention",
            "updated_epoch": time.time(),
        },
    )
    return results
