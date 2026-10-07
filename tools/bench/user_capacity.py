"""Run stateful concurrent-user probes with guarded resets and bracket confirmation."""

import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .capacity import search_capacity
from .control import qualified_cache, recover_hosts, retry_probe
from .evidence import backend_open_file_exhaustion
from .journeys import classify_journey, generator_window, journey_flags
from .majority import (
    MEASUREMENT_KEYS,
    POLICY,
    Consensus,
    majority_search,
    measurement_identity,
    validate_decision_handoff,
)
from .manifest import atomic_json
from .profiles import PROFILES, methodology, normalize_config
from .report import generator_flags
from .runner import campaign_lock, database_campaign_lock, run_trial
from .telemetry import rds_metrics, validate_rds_evidence


def backend_validity_flags(directory: Path) -> list[str]:
    log = directory / "backend.log"
    if log.exists() and backend_open_file_exhaustion(log.read_text(errors="replace")):
        return ["backend file-descriptor exhaustion invalidates capacity evidence"]
    return []


def validate_config(c: dict):
    decision = c.get("boundary_decision_policy", "legacy")
    if decision not in ("legacy", POLICY) or (
        decision == POLICY and c.get("experiment_type") != "capacity"
    ):
        raise ValueError("unknown boundary decision policy")
    if decision == POLICY and c.get("confirmation_policy") != {
        "passing_repeats": 0,
        "failing_repeats": 1,
        "stop_on_contradiction": True,
    }:
        raise ValueError("majority policy requires no passing repeats and one failing confirmation")
    if (
        type(c.get("backend_nofile_limit", 250000)) is not int
        or c.get("backend_nofile_limit", 250000) != 250000
    ):
        raise ValueError("backend_nofile_limit must be the comparable 250000 policy")
    if c.get("experiment_type", "capacity") not in ("capacity", "fixed-load"):
        raise ValueError("unknown experiment_type")
    if c.get("profile", "legacy") not in PROFILES:
        raise ValueError("unknown benchmark profile")
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
        type(c.get("success_fraction")) not in (int, float)
        or not math.isfinite(c["success_fraction"])
        or not 0.9 <= c["success_fraction"] <= 1
        or type(c.get("authentication_fraction", c["success_fraction"])) not in (int, float)
        or not math.isfinite(c.get("authentication_fraction", c["success_fraction"]))
        or not 0 <= c.get("authentication_fraction", c["success_fraction"]) <= 1
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
    if "cell_starting_hints" in c:
        hints = c["cell_starting_hints"]
        if (
            not isinstance(hints, dict)
            or not isinstance(c["cells"], list)
            or any(type(name) is not str or not name for name in c["cells"])
            or len(set(c["cells"])) != len(c["cells"])
            or any(type(name) is not str or name not in c["cells"] for name in hints)
        ):
            raise ValueError("invalid cell_starting_hints cells")
        for hint in hints.values():
            if not isinstance(hint, dict) or not {"start_users"} <= set(hint) <= {
                "start_users",
                "initial_failing_hint",
            }:
                raise ValueError("invalid cell_starting_hints keys")
            start = hint["start_users"]
            if type(start) is not int or not 1 <= start <= c["max_users"]:
                raise ValueError("invalid cell_starting_hints start_users")
            if "initial_failing_hint" in hint and (
                type(hint["initial_failing_hint"]) is not int
                or not start < hint["initial_failing_hint"] <= c["max_users"]
            ):
                raise ValueError("invalid cell_starting_hints initial_failing_hint")
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
    population = c["max_users"]
    if c.get("experiment_type") == "fixed-load":
        users = c.get("fixed_users")
        if (
            not isinstance(users, list)
            or not users
            or any(type(n) is not int or not 1 <= n <= 50000 for n in users)
            or len(set(users)) != len(users)
        ):
            raise ValueError("invalid fixed_users")
        population = max(users)
    fixture = json.loads(Path(c["fixture"]).read_text())
    if fixture["counts"]["users"] < population:
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


def qualify_trial_result(c, r, directory):
    reasons = list(r.get("reasons", [])) + backend_validity_flags(directory)
    if r.get("status") != "measured" or not r.get("reset_verified"):
        reasons.append("measurement or cleanup incomplete")
    reasons += generator_flags(directory / "telemetry.jsonl", *generator_window(r))
    if c.get("mode") == "aws":
        start, end = r.get("measurement_started_epoch"), r.get("measurement_finished_epoch")
        saved = directory / "rds.json"
        if not start or not end:
            reasons.append("RDS measurement interval missing")
        elif not saved.exists():
            reasons.append("RDS telemetry missing")
        else:
            reasons += validate_rds_evidence(json.loads(saved.read_text()), start, end)
    m = r.get("journey_metrics", {})
    reasons += journey_flags(m)
    outcome = classify_journey(
        m, r["trial"]["users"], reasons, c["success_fraction"], c["authentication_fraction"]
    )
    return {
        "outcome": outcome,
        "lifecycle_status": r.get("status"),
        "reasons": sorted(set(reasons)),
        "metrics": m,
        "trial_id": r["trial"]["id"],
        "methodology": r.get("methodology")
        or (
            methodology(c)
            if "ramp_seconds" in c
            else {
                "success_fraction": c["success_fraction"],
                "authentication_fraction": c["authentication_fraction"],
            }
        ),
    }


def finalize_pending_outcomes(c, root):
    """Finish saved measurements through original validity checks before admitting another vote."""
    for path in sorted(root.glob("*/result.json")):
        target = path.with_name("capacity-outcome.json")
        if target.exists():
            continue
        r = json.loads(path.read_text())
        if r.get("status") != "measured" or r.get("reset_verified") is not True:
            continue
        recovery = {**c, "rds_wait_seconds": min(c.get("rds_wait_seconds", 180), 180)}
        await_rds(recovery, r, path.parent)
        summary = qualify_trial_result(c, r, path.parent)
        summary["finalized_after_restart"] = True
        atomic_json(target, summary)


def next_start_users(previous: dict | None, default: int) -> int:
    if previous and previous.get("status") in ("bracket_found", "ceiling_reached"):
        return previous.get("passing_users") or default
    return default


def cell_starting_points(c, cell, previous, previous_upper):
    hint = c.get("cell_starting_hints", {}).get(cell)
    if hint is not None:
        return hint["start_users"], hint.get("initial_failing_hint")
    return next_start_users(previous, c["start_users"]), previous_upper


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


def validate_resume_resource_policy(stored: dict, c: dict, reusable: bool):
    saved = stored.get("campaign", {}).get("backend_nofile_limit")
    if type(saved) is not int or saved != c["backend_nofile_limit"]:
        raise ValueError("resume resource policy missing or differs: backend_nofile_limit")
    if not reusable:
        return
    actual = (
        stored.get("native_runtime", {}).get("container", {}).get("nofile", stored.get("nofile"))
    )
    if (
        not isinstance(actual, dict)
        or type(actual.get("soft")) is not int
        or type(actual.get("hard")) is not int
        or actual["soft"] != saved
        or actual["hard"] != saved
        or type(actual.get("process_samples")) is not int
        or actual["process_samples"] < 1
    ):
        raise ValueError("resume checkpoint lacks matching actual nofile attestation")


def _completed_trial_references(root):
    references = set()

    def walk(value, found):
        if isinstance(value, dict):
            if (
                value.get("outcome") in ("pass", "fail")
                and isinstance(value.get("trial_id"), str)
                and value["trial_id"]
            ):
                found.add(value["trial_id"])
            for nested in value.values():
                walk(nested, found)
        elif isinstance(value, list):
            for nested in value:
                walk(nested, found)

    for name in ("capacity-results.json", "fixed-load-results.json"):
        path = root / name
        if not path.exists():
            continue
        for result in json.loads(path.read_text()).values():
            found = set()
            walk(result, found)
            if (
                result.get("status")
                in ("bracket_found", "ceiling_reached", "unstable_boundary", "fixed_load_complete")
                and not found
            ):
                raise ValueError("completed checkpoint lacks valid trial references")
            references.update(found)
    return references


def run_user_capacity(
    config_path: Path, output_dir: Path, resume: bool = False, retry_invalid: bool = False
) -> dict:
    c = normalize_config(json.loads(config_path.read_text()))
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
        references = _completed_trial_references(output_dir)
        known_ids = {json.loads(path.read_text()).get("trial", {}).get("id") for path in manifests}
        if not references <= known_ids:
            raise ValueError("completed checkpoint references missing original trial manifests")
        method_keys = MEASUREMENT_KEYS
        for manifest_path in manifests:
            stored = json.loads(manifest_path.read_text())
            result_path = manifest_path.with_name("result.json")
            measured = json.loads(result_path.read_text()) if result_path.exists() else {}
            reusable = (
                measured.get("status") == "measured" and measured.get("reset_verified") is True
            ) or stored.get("trial", {}).get("id") in references
            validate_resume_resource_policy(stored, c, reusable)
            target_method = validate_decision_handoff(stored["campaign"], c)
            stored_method = normalize_config(stored["campaign"])
            for key in method_keys:
                if stored_method.get(key) != target_method.get(key):
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
    majority = c.get("boundary_decision_policy") == POLICY
    if majority and c.get("decision_policy_handoff"):
        audit = output_dir / "decision-policy-handoff.json"
        if not audit.exists():
            atomic_json(
                audit,
                {
                    "receipt": c["decision_policy_handoff"],
                    "measurement_identity_sha256": measurement_identity(c),
                    "applied_epoch": time.time(),
                },
            )
    if c.get("queue_extension_handoff"):
        audit = output_dir / "queue-extension-handoff.json"
        if not audit.exists():
            atomic_json(
                audit, {"receipt": c["queue_extension_handoff"], "applied_epoch": time.time()}
            )
    if c.get("resolution_handoff"):
        audit = output_dir / "resolution-policy-handoff.json"
        if not audit.exists():
            atomic_json(
                audit,
                {
                    "receipt": c["resolution_handoff"],
                    "measurement_identity_sha256": measurement_identity(c),
                    "applied_epoch": time.time(),
                },
            )
    results = {}
    ordinal = 0
    previous = None
    previous_upper = c.get("initial_failing_hint")
    if resume:
        finalize_pending_outcomes(c, output_dir)
    cached = qualified_cache(output_dir) if resume else {}
    for key, queue in cached.items():
        cached[key] = type(queue)(
            item
            for item in queue
            if not backend_validity_flags(output_dir / item.get("trial_id", "missing"))
        )
    prior_trials = {key: list(queue) for key, queue in cached.items()}
    results_filename = (
        "fixed-load-results.json"
        if c["experiment_type"] == "fixed-load"
        else "capacity-results.json"
    )
    with (
        campaign_lock(Path("artifacts/campaign.lock")),
        database_campaign_lock(os.environ["DATABASE_URL"]),
    ):
        if resume:
            results_file = output_dir / results_filename
            results = json.loads(results_file.read_text()) if results_file.exists() else {}
            for path in output_dir.glob("*/result.json"):
                if backend_validity_flags(path.parent):
                    bad_cell = json.loads(path.read_text()).get("trial", {}).get("cell")
                    if bad_cell in results:
                        results[bad_cell] = {
                            **results[bad_cell],
                            "status": "invalid",
                            "reasons": backend_validity_flags(path.parent),
                        }
            if any(invalid_boundary(r) for r in results.values()):
                if not retry_invalid:
                    raise ValueError("cannot resume invalid evidence without resolving its cause")
                results = {cell: r for cell, r in results.items() if not invalid_boundary(r)}
            ordinal = 1 + max(
                int(json.loads(p.read_text())["trial"]["id"].rsplit("-", 1)[1])
                for p in output_dir.glob("*/manifest.json")
            )
        if majority:
            results = {
                cell: r
                for cell, r in results.items()
                if r.get("status") != "unstable_boundary"
                and not (
                    r.get("status") == "bracket_found"
                    and r["failing_users"] - r["passing_users"] > c["resolution_users"]
                )
            }
        ledger = {
            "policy": POLICY,
            "measurement_identity_sha256": measurement_identity(c),
            "cells": {},
        }
        if majority:
            for name in {name for name, _ in prior_trials}:
                prior = {
                    users: items
                    for (current, users), items in prior_trials.items()
                    if current == name
                }
                ledger["cells"][name] = Consensus(None, prior, None).decisions()
            atomic_json(output_dir / "boundary-decisions.json", ledger)
        for cell in c["cells"]:
            if cell in results:
                if results[cell]["status"] in ("bracket_found", "ceiling_reached"):
                    previous = results[cell]
                previous_upper = results[cell].get("failing_users")
                continue
            start_users, initial_upper = cell_starting_points(c, cell, previous, previous_upper)

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
                summary = qualify_trial_result(c, {**r, "trial": trial}, directory)
                atomic_json(directory / "capacity-outcome.json", summary)
                return summary

            def fresh_probe(users):
                return retry_probe(
                    probe_once,
                    users,
                    lambda: recover_hosts(c),
                    attempts=c.get("infrastructure_attempts", 1),
                    delay=c.get("recovery_delay_seconds", 30),
                )

            def probe(users):
                queue = cached.get((cell, users))
                if queue:
                    return {**queue.popleft(), "reused_after_restart": True}
                return fresh_probe(users)

            if c["experiment_type"] == "fixed-load":
                observations = []
                for population in c["fixed_users"]:
                    item = probe(population)
                    observations.append({"users": population, **item})
                    if item["outcome"] == "invalid":
                        break
                result = {
                    "status": "invalid"
                    if any(o["outcome"] == "invalid" for o in observations)
                    else "fixed_load_complete",
                    "experiment_type": "fixed-load",
                    "fixed_users": c["fixed_users"],
                    "observations": observations,
                }
                results[cell] = result
            elif majority:

                def persist_decisions(decisions):
                    ledger["cells"][cell] = decisions
                    ledger["updated_epoch"] = time.time()
                    atomic_json(output_dir / "boundary-decisions.json", ledger)

                votes = {
                    users: items for (name, users), items in prior_trials.items() if name == cell
                }
                result = majority_search(
                    fresh_probe,
                    votes,
                    start_users,
                    c["step_users"],
                    c["max_users"],
                    c["resolution_users"],
                    next_candidate=initial_upper,
                    growth_strategy=c.get("growth_strategy", "adaptive"),
                    persist=persist_decisions,
                )
                result["starting_users"] = start_users
                results[cell] = result
                if result["status"] in ("bracket_found", "ceiling_reached"):
                    previous = result
                previous_upper = result.get("failing_users")
            else:
                result = search_capacity(
                    probe,
                    start_users,
                    c["step_users"],
                    c["max_users"],
                    c["resolution_users"],
                    next_candidate=initial_upper,
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
            atomic_json(output_dir / results_filename, results)
            if invalid_boundary(result):
                break
    atomic_json(
        output_dir / "capacity-status.json",
        {
            "status": "complete"
            if all(
                r["status"] in ("bracket_found", "ceiling_reached", "fixed_load_complete")
                for r in results.values()
            )
            else "complete_with_unstable_boundaries"
            if len(results) == len(c["cells"])
            and not any(invalid_boundary(r) for r in results.values())
            else "needs_attention",
            "updated_epoch": time.time(),
        },
    )
    return results
