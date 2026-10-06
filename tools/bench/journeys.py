"""Credentials, phase-aware metrics, and response-based concurrent-user classification."""

import json
import os
from datetime import datetime
from pathlib import Path

from .report import metric_lines, percentile


def prepare_journey_data(credentials_path: Path, users: int, output_path: Path) -> dict:
    credentials = json.loads(credentials_path.read_text())
    selected = credentials[:users]
    if (
        users < 1
        or len(selected) != users
        or len({a["username"].lower() for a in selected}) != users
    ):
        raise ValueError("one distinct account is required per concurrent virtual user")
    if any(not a.get("password") for a in selected):
        raise ValueError("real login credentials required")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"actors": selected}, f)
    return {"actors": users}


def latency_summary(values: list[float]) -> dict:
    if not values:
        return {"duration_samples": 0}
    import statistics

    return {
        "duration_samples": len(values),
        "mean": statistics.fmean(values),
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "p999": percentile(values, 0.999),
    }


def read_journey_metrics(path: Path, steady_seconds: float | None = None) -> dict:
    """Reduce immutable samples; latency includes failed responses and timeouts.

    Optional configured steady duration is the throughput denominator. Sample timestamp
    extrema are diagnostics only, never substituted for the configured duration.
    """
    import math

    if steady_seconds is not None and (not math.isfinite(steady_seconds) or steady_seconds <= 0):
        raise ValueError("configured steady duration must be finite and positive")
    m = {
        "attempted": 0,
        "succeeded": 0,
        "authenticated_users": 0,
        "active_users_max": 0,
        "timeouts": 0,
        "generator_resource_errors": 0,
        "network_errors": 0,
        "ramp_attempted": 0,
        "ramp_succeeded": 0,
        "settle_attempted": 0,
        "settle_succeeded": 0,
        "http_requests": 0,
        "completed_journeys": 0,
    }
    names = {
        "operations_attempted": "attempted",
        "operations_succeeded": "succeeded",
        "journey_timeouts": "timeouts",
        "journey_network_errors": "network_errors",
        "http_reqs": "http_requests",
        "user_journeys_completed": "completed_journeys",
    }
    buckets = {}
    status = {"incomplete": False}
    auth = {"login_attempted": 0, "login_succeeded": 0}
    activity_start = activity_end = None
    for line in metric_lines(path, status):
        try:
            p = json.loads(line)
            if p.get("type") != "Point":
                continue
            data, metric = p["data"], p["metric"]
            value = data["value"]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("invalid metric value")
            tags = data.get("tags", {})
            phase, operation = tags.get("phase", "unphased"), tags.get("operation", "unknown")
            timestamp = (
                datetime.fromisoformat(data["time"].replace("Z", "+00:00")).timestamp()
                if data.get("time")
                else None
            )
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            status["incomplete"] = True
            continue
        bucket = buckets.setdefault(
            phase,
            {
                "attempted": 0,
                "succeeded": 0,
                "http_requests": 0,
                "timeouts": 0,
                "network_errors": 0,
                "completed_journeys": 0,
                "active_users_max": 0,
                "durations": [],
                "endpoints": {},
            },
        )
        endpoint = bucket["endpoints"].setdefault(
            operation,
            {
                "attempted": 0,
                "succeeded": 0,
                "http_requests": 0,
                "timeouts": 0,
                "network_errors": 0,
                "completed_journeys": 0,
                "durations": [],
            },
        )
        if timestamp is not None:
            bucket["started_epoch"] = min(bucket.get("started_epoch", timestamp), timestamp)
            bucket["finished_epoch"] = max(bucket.get("finished_epoch", timestamp), timestamp)
            if phase in ("ramp", "settle", "steady"):
                activity_start = (
                    timestamp if activity_start is None else min(activity_start, timestamp)
                )
                activity_end = timestamp if activity_end is None else max(activity_end, timestamp)
        if metric == "scenario_started_epoch":
            m["scenario_started_epoch"] = value
        if metric in names:
            bucket[names[metric]] += value
            endpoint[names[metric]] += value
        if metric == "operation_duration":
            bucket["durations"].append(value)
            endpoint["durations"].append(value)
        if metric == "journey_active_users":
            bucket["active_users_max"] = max(bucket["active_users_max"], value)
        if phase != "drain":
            if metric == "generator_resource_errors":
                m["generator_resource_errors"] += value
            if metric == "users_authenticated":
                m["authenticated_users"] += value
            if operation == "login" and metric in ("operations_attempted", "operations_succeeded"):
                auth[
                    "login_attempted" if metric == "operations_attempted" else "login_succeeded"
                ] += value
    for phase, bucket in buckets.items():
        bucket.update(latency_summary(bucket.pop("durations")))
        bucket["success_fraction"] = (
            bucket["succeeded"] / bucket["attempted"] if bucket["attempted"] else None
        )
        for endpoint in bucket["endpoints"].values():
            endpoint.update(latency_summary(endpoint.pop("durations")))
            endpoint["success_fraction"] = (
                endpoint["succeeded"] / endpoint["attempted"] if endpoint["attempted"] else None
            )
    steady = buckets.get("steady", {})
    for key in (
        *names.values(),
        "active_users_max",
        "duration_samples",
        "mean",
        "p50",
        "p95",
        "p99",
        "p999",
        "success_fraction",
    ):
        if key in steady:
            m[key] = steady[key]
    for phase in ("ramp", "settle"):
        for key in ("attempted", "succeeded"):
            m[phase + "_" + key] = buckets.get(phase, {}).get(key, 0)
    if activity_start is not None:
        m.update(activity_started_epoch=activity_start, activity_finished_epoch=activity_end)
    if "started_epoch" in steady:
        m.update(
            steady_started_epoch=steady["started_epoch"],
            steady_finished_epoch=steady["finished_epoch"],
        )
    auth["login_success_fraction"] = (
        auth["login_succeeded"] / auth["login_attempted"] if auth["login_attempted"] else None
    )
    m.update(
        incomplete=status["incomplete"],
        phases=buckets,
        endpoints=steady.get("endpoints", {}),
        authentication=auth,
    )
    if steady_seconds is not None:
        m.update(
            steady_seconds=steady_seconds,
            attempted_operations_s=m["attempted"] / steady_seconds,
            successful_operations_s=m["succeeded"] / steady_seconds,
            http_requests_s=m["http_requests"] / steady_seconds,
        )
        for endpoint in m["endpoints"].values():
            endpoint.update(
                attempted_operations_s=endpoint["attempted"] / steady_seconds,
                successful_operations_s=endpoint["succeeded"] / steady_seconds,
            )
    return m


def classify_journey(
    metrics: dict,
    users: int,
    reasons: list[str],
    success_fraction: float = 0.99,
    authentication_fraction: float | None = None,
) -> str:
    import math

    authentication_fraction = (
        success_fraction if authentication_fraction is None else authentication_fraction
    )
    if (
        isinstance(success_fraction, bool)
        or not math.isfinite(success_fraction)
        or not 0.90 <= success_fraction <= 1
        or isinstance(authentication_fraction, bool)
        or not math.isfinite(authentication_fraction)
        or not 0 <= authentication_fraction <= 1
    ):
        raise ValueError(
            "operation threshold must be [0.90,1.00], authentication gate [0,1], both finite"
        )
    if (
        reasons
        or journey_flags(metrics)
        or metrics.get("incomplete")
        or not metrics.get("attempted")
    ):
        return "invalid"
    if metrics.get("active_users_max", 0) < users:
        return "invalid"
    if metrics.get("authenticated_users", 0) < users * authentication_fraction:
        return "fail"
    return "pass" if metrics["succeeded"] / metrics["attempted"] >= success_fraction else "fail"


def generator_window(result: dict):
    metrics = result.get("journey_metrics", {})
    start = metrics.get("activity_started_epoch", result.get("execution_started_epoch"))
    end = metrics.get("activity_finished_epoch", result.get("execution_finished_epoch"))
    return (start, end) if start is not None and end is not None else (None, None)


def journey_flags(metrics: dict) -> list[str]:
    return (
        ["generator socket/resource errors observed"]
        if metrics.get("generator_resource_errors")
        else []
    )
