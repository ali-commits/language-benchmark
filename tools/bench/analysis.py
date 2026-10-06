"""Threshold analysis of observed valid populations, never an exact capacity estimate."""

import json
import math
from collections import defaultdict
from pathlib import Path

from .journeys import classify_journey

DEFAULT_THRESHOLDS = (0.90, 0.95, 0.99, 0.999)


def validate_threshold(value) -> float:
    if isinstance(value, bool):
        raise ValueError("threshold must be a finite fraction in [0.90,1.00]")
    result = float(value)
    if not math.isfinite(result) or not 0.90 <= result <= 1:
        raise ValueError("threshold must be a finite fraction in [0.90,1.00]")
    return result


def authentication_gate(value) -> float:
    result = float(value)
    if isinstance(value, bool) or not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError("authentication gate must be a finite fraction in [0,1]")
    return result


def methodology(config: dict) -> dict:
    keys = (
        "profile",
        "experiment_type",
        "ramp_seconds",
        "settle_seconds",
        "hold_seconds",
        "success_fraction",
        "authentication_fraction",
        "timeout_seconds",
        "wait_min",
        "wait_max",
        "mode",
        "backend_nofile_limit",
        "classification_version",
    )
    method = {k: config[k] for k in keys if k in config}
    timing = tuple(config.get(k) for k in ("ramp_seconds", "settle_seconds", "hold_seconds"))
    if "profile" in config:
        method["configured_profile"] = config["profile"]
    method["profile"] = (
        "fast"
        if timing == (180, 0, 240)
        else "legacy"
        if timing == (300, 120, 300)
        else "custom"
        if all(v is not None for v in timing)
        else "historical-unspecified"
    )
    method.setdefault("experiment_type", "capacity")
    method.setdefault("authentication_fraction", config.get("success_fraction", 0.99))
    method["description"] = (
        "Configured methodology retained"
        if "hold_seconds" in config
        else "Historical summary: configured timings not stored; throughput denominator unavailable"
    )
    return method


def backend_log_flags(artifact: Path) -> list[str]:
    path = artifact / "backend.log"
    if not path.exists():
        return []
    from .evidence import backend_open_file_exhaustion

    return (
        ["backend open-file exhaustion observed; infrastructure evidence invalid"]
        if backend_open_file_exhaustion(path.read_text(errors="replace"))
        else []
    )


def _compact_observation(item: dict, cell: str, source: Path, users=None) -> dict:
    metrics = item.get("metrics", {})
    users = item.get("users", users)
    trial = item.get("trial_id", "")
    artifact = Path(item.get("artifact", str(source.parent / trial)))
    reasons = list(item.get("reasons", [])) + backend_log_flags(artifact)
    outcome = item.get("outcome", item.get("qualification", "unknown"))
    valid = (
        outcome in ("pass", "fail")
        and not reasons
        and not metrics.get("incomplete")
        and not metrics.get("generator_resource_errors")
        and item.get("lifecycle_status", "measured") == "measured"
    )
    method = methodology(item.get("methodology", {}))
    seconds = method.get("hold_seconds")
    enriched = dict(metrics)
    if seconds:
        enriched.update(
            steady_seconds=seconds,
            attempted_operations_s=metrics.get("attempted", 0) / seconds,
            successful_operations_s=metrics.get("succeeded", 0) / seconds,
        )
    return {
        "id": trial,
        "campaign": item.get(
            "campaign", trial.rsplit("-", 1)[0] if "-" in trial else source.parent.name
        ),
        "cell": cell,
        "users": users,
        "valid": valid,
        "reasons": reasons,
        "metrics": enriched,
        "methodology": method,
        "resources": item.get("resources", {}),
        "validity_basis": "Recorded immutable campaign qualification; raw/log telemetry may not be locally available",
        "artifact": str(artifact),
    }


def _summary_observation(row: dict) -> dict:
    return {
        "id": row["id"],
        "campaign": row["campaign"],
        "cell": row["cell"],
        "users": row.get("target_users"),
        "valid": row["qualification"] in ("pass", "fail"),
        "reasons": row["reasons"],
        "metrics": row["metrics"],
        "methodology": row.get("methodology", methodology({})),
        "resources": row.get("resources", {}),
        "external_resources": row.get("external_resources", {}),
        "validity_basis": "Canonical raw artifact, lifecycle, generator and telemetry qualification",
        "artifact": row.get("artifact"),
    }


def load_observations(path: Path) -> list[dict]:
    from .report import read_trial_summary

    path = Path(path)
    if path.is_dir():
        return [
            _summary_observation(read_trial_summary(p)) for p in sorted(path.rglob("manifest.json"))
        ]
    if path.name == "manifest.json":
        return [_summary_observation(read_trial_summary(path))]
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "trials" in data:
        return [_summary_observation(row) for row in data["trials"]]
    if not isinstance(data, dict):
        raise ValueError("analysis input must contain trial or capacity results")
    rows, seen = [], set()
    for cell, result in data.items():
        if not isinstance(result, dict) or "observations" not in result:
            continue
        items = [(item, None) for item in result["observations"]]
        for name, repeats in result.get("confirmations", {}).items():
            items += [(item, result.get(name)) for item in repeats]
        for name, repeats in result.get("prior_endpoint_trials", {}).items():
            items += [(item, result.get(name)) for item in repeats]
        for item, users in items:
            row = _compact_observation(item, cell, path, users)
            key = (cell, row["id"])
            if row["id"] and key in seen:
                continue
            seen.add(key)
            rows.append(row)
    if not rows:
        raise ValueError("no recognizable observations in analysis input")
    return rows


def analyze_observations(rows: list[dict], thresholds=None, authentication_threshold=None) -> dict:
    thresholds = [
        validate_threshold(value)
        for value in (DEFAULT_THRESHOLDS if thresholds is None else thresholds)
    ]
    if not thresholds:
        raise ValueError("at least one threshold is required")
    if authentication_threshold is not None:
        authentication_threshold = authentication_gate(authentication_threshold)
    groups = defaultdict(list)
    for row in rows:
        if not row.get("users"):
            continue
        method = row.get("methodology", methodology({}))
        key = (row.get("campaign", "unknown"), row["cell"], json.dumps(method, sort_keys=True))
        groups[key].append(row)
    bounds = []
    invalid_ids = set()
    for (campaign, cell, method_json), samples in groups.items():
        method = json.loads(method_json)
        gate = (
            authentication_threshold
            if authentication_threshold is not None
            else authentication_gate(
                method.get("authentication_fraction", method.get("success_fraction", 0.99))
            )
        )
        for threshold in thresholds:
            passed, failed = [], []
            outcomes = defaultdict(set)
            for index, row in enumerate(samples):
                reasons = list(row.get("reasons", []))
                if not row.get("valid", True):
                    reasons.append("recorded evidence invalid")
                outcome = classify_journey(
                    row["metrics"], row["users"], reasons, threshold, authentication_fraction=gate
                )
                if outcome == "invalid":
                    invalid_ids.add((campaign, cell, row.get("id") or index))
                    continue
                outcomes[row["users"]].add(outcome)
                (passed if outcome == "pass" else failed).append(row["users"])
            high, low = max(passed, default=None), min(failed, default=None)
            counts = sorted(outcomes)
            mixed = sorted(n for n, values in outcomes.items() if len(values) > 1)
            bounds.append(
                {
                    "campaign": campaign,
                    "cell": cell,
                    "methodology": method,
                    "threshold": threshold,
                    "authentication_gate": gate,
                    "highest_observed_passing_users": high,
                    "lowest_observed_failing_users": low,
                    "passing_observations": len(passed),
                    "failing_observations": len(failed),
                    "mixed_users": mixed,
                    "contradictory": bool(mixed)
                    or (high is not None and low is not None and high >= low),
                    "unsampled_ranges": [
                        [a + 1, b - 1] for a, b in zip(counts, counts[1:]) if b - a > 1
                    ],
                    "interpretation": "Observed population bounds only; no interpolation or exact capacity inferred",
                }
            )
    return {
        "method": "Reclassification at operation thresholds with separate recorded authentication gates. Highest/lowest observed bounds are not exact capacity; a lower-threshold guided search does not locate higher-threshold boundaries. Invalid evidence remains excluded and campaign/methodology identities are never pooled.",
        "thresholds": thresholds,
        "invalid_observations": len(invalid_ids),
        "observations": rows,
        "bounds": bounds,
    }


def human_summary(report: dict) -> str:
    lines = [
        report["method"],
        "",
        "Campaign / cell | target | observed pass | observed fail | consistency",
    ]
    for row in report["bounds"]:
        lines.append(
            f"{row['campaign']} / {row['cell']} | {row['threshold']:.3%} | {row['highest_observed_passing_users']} | {row['lowest_observed_failing_users']} | {'CONTRADICTORY' if row['contradictory'] else 'observed only'}"
        )
    lines.append(f"Invalid observations excluded: {report['invalid_observations']}")
    return "\n".join(lines)
