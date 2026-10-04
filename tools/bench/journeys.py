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


def read_journey_metrics(path: Path) -> dict:
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
    status = {"incomplete": False}
    durations = []
    times = []
    activity_times = []
    for line in metric_lines(path, status):
        try:
            p = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            status["incomplete"] = True
            continue
        if p.get("type") != "Point":
            continue
        d = p["data"]
        metric = p["metric"]
        value = d["value"]
        phase = d.get("tags", {}).get("phase")
        if phase in ("ramp", "settle", "steady") and d.get("time"):
            activity_times.append(
                datetime.fromisoformat(d["time"].replace("Z", "+00:00")).timestamp()
            )
        if metric == "generator_resource_errors" and phase != "drain":
            m["generator_resource_errors"] += value
        if metric == "users_authenticated" and phase != "drain":
            m["authenticated_users"] += value
        if phase in ("ramp", "settle"):
            if metric == "operations_attempted":
                m[phase + "_attempted"] += value
            if metric == "operations_succeeded":
                m[phase + "_succeeded"] += value
        if phase != "steady":
            continue
        if d.get("time"):
            times.append(datetime.fromisoformat(d["time"].replace("Z", "+00:00")).timestamp())
        if metric == "http_reqs":
            m["http_requests"] += value
        if metric == "user_journeys_completed":
            m["completed_journeys"] += value
        if metric == "operations_attempted":
            m["attempted"] += value
        if metric == "operations_succeeded":
            m["succeeded"] += value
        if metric == "journey_network_errors":
            m["network_errors"] += value
        if metric == "journey_timeouts":
            m["timeouts"] += value
        if metric == "journey_active_users":
            m["active_users_max"] = max(m["active_users_max"], value)
        if metric == "operation_duration":
            durations.append(value)
    if activity_times:
        m.update(
            activity_started_epoch=min(activity_times), activity_finished_epoch=max(activity_times)
        )
    m["incomplete"] = status["incomplete"]
    if durations:
        m.update(
            p50=percentile(durations, 0.5),
            p95=percentile(durations, 0.95),
            p99=percentile(durations, 0.99),
        )
    if times:
        m.update(steady_started_epoch=min(times), steady_finished_epoch=max(times))
    return m


def classify_journey(
    metrics: dict, users: int, reasons: list[str], success_fraction: float = 0.99
) -> str:
    if (
        reasons
        or journey_flags(metrics)
        or metrics.get("incomplete")
        or not metrics.get("attempted")
    ):
        return "invalid"
    if metrics.get("active_users_max", 0) < users:
        return "invalid"
    if metrics.get("authenticated_users", 0) < users * success_fraction:
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
