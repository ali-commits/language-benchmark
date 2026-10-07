"""Sampled backend Docker resources; generator/RDS budgets remain separate."""

import json
import math
import re
import statistics
from pathlib import Path

from .records import (
    Campaign,
    GeneratorResources,
    JourneyMetrics,
    RdsResources,
    Resources,
    TrialResult,
)


def parse_memory_bytes(value: str) -> float:
    match = re.fullmatch(r"\s*([\d.]+)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*", value)
    if not match:
        raise ValueError("invalid Docker memory quantity")
    units = {
        "B": 1,
        "kB": 1000,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "TiB": 1024**4,
    }
    return float(match[1]) * units[match[2]]


def phase_windows(
    result: TrialResult, campaign: Campaign, metrics: JourneyMetrics
) -> dict[str, list[float]]:
    """Configured phase boundaries where saved; legacy raw extrema are labeled fallback."""
    start = metrics.get("scenario_started_epoch", result.get("scenario_started_epoch"))
    if start is not None and all(
        k in campaign for k in ("ramp_seconds", "settle_seconds", "hold_seconds")
    ):
        ramp, settle, hold = (
            campaign["ramp_seconds"],
            campaign["settle_seconds"],
            campaign["hold_seconds"],
        )
        return {
            "ramp": [start, start + ramp],
            "settle": [start + ramp, start + ramp + settle],
            "steady": [start + ramp + settle, start + ramp + settle + hold],
        }
    return {
        name: [bucket["started_epoch"], bucket["finished_epoch"]]
        for name, bucket in metrics.get("phases", {}).items()
        if "started_epoch" in bucket and "finished_epoch" in bucket
    }


def summarize_resources(
    path: Path, phases: dict[str, list[float]], allocated_cpus: float = 2
) -> Resources:
    if not math.isfinite(allocated_cpus) or allocated_cpus <= 0:
        raise ValueError("positive allocated CPUs required")
    samples: dict[str, dict[str, list[float]]] = {
        name: {"cpu": [], "memory": []} for name in phases
    }
    result: Resources = {
        "allocated_cpus": allocated_cpus,
        "source": "Docker stats sampled Linux working set",
        "limitation": "Averages/peaks are sampled Docker working-set measurements, not RSS or guaranteed transient maxima; CPU100% uses the full allocated budget. Phase boundaries use the actual k6 scenario anchor with configured stages when available, otherwise raw phase extrema (partial sampling boundaries).",
        "phases": {},
        "malformed_samples": 0,
    }
    if not path.exists():
        result["limitation"] += " Telemetry file unavailable."
        return result
    for line in path.read_text().splitlines():
        try:
            sample = json.loads(line)
            t = float(sample["time"])
            raw = sample.get("docker", "")
            records = (
                [json.loads(x) for x in raw.splitlines() if x.strip()]
                if isinstance(raw, str)
                else raw
            )
            for record in records or []:
                name = record.get("Name", "")
                if not ("-backend-" in name or name == "backend"):
                    continue
                cpu = float(record["CPUPerc"].rstrip("%")) / allocated_cpus
                memory = parse_memory_bytes(record["MemUsage"].split("/")[0]) / 1024**2
                if not all(math.isfinite(v) and v >= 0 for v in (t, cpu, memory)):
                    raise ValueError("invalid Docker resource value")
                for phase, (start, end) in phases.items():
                    if start <= t < end:
                        samples[phase]["cpu"].append(cpu)
                        samples[phase]["memory"].append(memory)
        except (ValueError, TypeError, KeyError):
            result["malformed_samples"] += 1
    for phase, values in samples.items():
        if values["cpu"]:
            result["phases"][phase] = {
                "samples": len(values["cpu"]),
                "cpu_percent_mean": statistics.fmean(values["cpu"]),
                "cpu_percent_peak": max(values["cpu"]),
                "memory_working_set_mib_mean": statistics.fmean(values["memory"]),
                "memory_working_set_mib_peak": max(values["memory"]),
            }
    return result


def summarize_generator_resources(path: Path, phases: dict[str, list[float]]) -> GeneratorResources:
    result: GeneratorResources = {
        "budget": "Independent load-generator host; CPU100% spans its own logical CPUs, not the backend2CPU budget",
        "phases": {},
    }
    if not path.exists():
        return result
    buckets: dict[str, dict[str, list[float]]] = {
        phase: {"cpu": [], "available": []} for phase in phases
    }
    previous = None
    for line in path.read_text().splitlines():
        try:
            sample = json.loads(line)
            host = sample.get("hosts", {}).get("generator", {})
            cpu = next(row for row in host["stat"].splitlines() if row.startswith("cpu "))
            counters = [int(value) for value in cpu.split()[1:9]]
            available = next(
                int(row.split()[1]) / 1024
                for row in host["meminfo"].splitlines()
                if row.startswith("MemAvailable:")
            )
            fraction = None
            if previous is not None:
                total = sum(counters) - sum(previous)
                idle = counters[3] + counters[4] - previous[3] - previous[4]
                if total > 0:
                    fraction = 100 * (1 - idle / total)
            previous = counters
            for phase, (start, end) in phases.items():
                if start <= sample["time"] < end:
                    buckets[phase]["available"].append(available)
                    if fraction is not None:
                        buckets[phase]["cpu"].append(fraction)
        except (ValueError, KeyError, TypeError, StopIteration):
            continue
    for phase, bucket in buckets.items():
        values = {}
        if bucket["cpu"]:
            values.update(
                {
                    "cpu_percent_of_generator_host_mean": statistics.fmean(bucket["cpu"]),
                    "cpu_percent_of_generator_host_peak": max(bucket["cpu"]),
                }
            )
        if bucket["available"]:
            values.update(
                {
                    "memory_available_mib_mean": statistics.fmean(bucket["available"]),
                    "memory_available_mib_min": min(bucket["available"]),
                }
            )
        if values:
            result["phases"][phase] = values
    return result


def summarize_rds_resources(path: Path, start: float, end: float) -> RdsResources:
    from datetime import datetime

    result: RdsResources = {
        "budget": "RDS is an independent managed database instance; its CPU is not backend CPU",
        "limitation": "Minute CloudWatch buckets overlap measurement boundaries; these are sampled database statistics, not backend allocations",
        "metrics": {},
    }
    if not path.exists():
        return result
    data = json.loads(path.read_text())
    result["instance"] = data.get("instance", {})
    for name, points in data.items():
        if not isinstance(points, list) or name == "events":
            continue
        means, peaks = [], []
        for point in points:
            try:
                stamp = point["Timestamp"]
                t = (
                    datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                    if isinstance(stamp, str)
                    else float(stamp)
                )
                if start - 60 <= t <= end:
                    if "Average" in point:
                        means.append(point["Average"])
                    if "Maximum" in point:
                        peaks.append(point["Maximum"])
            except (ValueError, TypeError, KeyError):
                continue
        if means or peaks:
            result["metrics"][name] = {
                "mean": statistics.fmean(means) if means else None,
                "peak": max(peaks) if peaks else None,
                "buckets": max(len(means), len(peaks)),
            }
    return result
