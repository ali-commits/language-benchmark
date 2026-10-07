"""Interpret workload logs and validate measured hold occupancy."""
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

from typing_extensions import TypedDict

from ..records import MetricData, MetricPoint, checked_record
from .types import OccupancyTolerance, ResolvedStage, Sample


class EventMetadata(TypedDict, total=False):
    type: str
    load_start_epoch_ms: float
    stages: list[dict[str, object]]

class WorkloadEvent(EventMetadata, total=False):
    sample: dict[str, object]
    operation_id: str
    journey_id: str


@dataclass
class Events:
    samples: list[Sample]
    metadata: EventMetadata
    counts: dict[str, int]
    reasons: list[str]


def parse_events(raw: str) -> Events:
    samples: list[Sample] = []
    metadata: EventMetadata = {}
    reasons: list[str] = []
    native_vus: list[MetricData] = []
    starts: dict[str, set[str]] = {"operation": set(), "journey": set()}
    ends: dict[str, set[str]] = {"operation": set(), "journey": set()}
    for line in raw.splitlines():
        if not line.startswith("BENCH_EVENT "):
            if line.startswith("{"):
                try:
                    point = checked_record(json.loads(line), MetricPoint)
                    if point.get("type") == "Point" and point.get("metric") == "vus":
                        native_vus.append(point["data"])
                except (ValueError, KeyError, TypeError):
                    reasons.append("malformed native k6 metric")
            continue
        try:
            event = checked_record(json.loads(line[len("BENCH_EVENT ") :]), WorkloadEvent)
            kind = event["type"]
            if kind == "metadata":
                if metadata:
                    raise ValueError("duplicate metadata")
                metadata = checked_record(event, EventMetadata)
            elif kind == "sample":
                sample = Sample.model_validate(event["sample"])
                if any(
                    value is not None and not math.isfinite(value)
                    for value in (
                        sample.timestamp,
                        sample.value,
                        sample.numerator,
                        sample.denominator,
                        sample.end_seconds,
                        sample.max_gap_seconds,
                    )
                ):
                    raise ValueError("nonfinite workload sample")
                if sample.kind == "rate" and (
                    sample.numerator is None
                    or sample.denominator is None
                    or sample.denominator <= 0
                    or not 0 <= sample.numerator <= sample.denominator
                ):
                    raise ValueError("invalid workload rate")
                samples.append(sample)
            elif kind == "journey_error":
                reasons.append("workload journey error")
            elif kind in ("operation_start", "operation_end", "journey_start", "journey_end"):
                category, state = kind.split("_")
                destination = starts if state == "start" else ends
                identity = event["operation_id"] if category == "operation" else event["journey_id"]
                if identity in destination[category]:
                    raise ValueError("duplicate event identity")
                destination[category].add(identity)
        except (ValueError, KeyError, TypeError):
            reasons.append("malformed workload event")
    if metadata.get("load_start_epoch_ms") is not None:
        from datetime import datetime

        for native_point in native_vus:
            try:
                timestamp = (
                    datetime.fromisoformat(native_point["time"].replace("Z", "+00:00")).timestamp()
                    - metadata["load_start_epoch_ms"] / 1000
                )
                value = float(native_point["value"])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("invalid native VUs")
                samples.append(
                    Sample(
                        metric="active_users",
                        scope="generator",
                        kind="gauge",
                        timestamp=timestamp,
                        value=value,
                        unit="count",
                        max_gap_seconds=1.5,
                        collection_interval=1.0,
                        provider_fields={"provider": "k6_native_vus"},
                    )
                )
            except (ValueError, KeyError, TypeError):
                reasons.append("malformed native occupancy gauge")
    counts = {}
    for category in starts:
        counts["started_" + category + "s"] = len(starts[category])
        counts["completed_" + category + "s"] = len(ends[category])
        counts["interrupted_" + category + "s"] = len(starts[category] - ends[category])
        if ends[category] - starts[category]:
            reasons.append("completion without matching start")
    if not metadata or not isinstance(metadata.get("load_start_epoch_ms"), (int, float)):
        reasons.append("missing load origin metadata")
    if not starts["operation"] or not ends["operation"]:
        reasons.append("no completed workload operations")
    return Events(samples, metadata, counts, list(dict.fromkeys(reasons)))


def verify_occupancy(samples: Sequence[Sample], stages: Sequence[ResolvedStage], tolerance: OccupancyTolerance) -> list[str]:
    """Require actual hold gauges with coverage and bounds; never pass an empty hold."""
    observations = sorted(
        (s for s in samples if s.scope == "generator" and s.metric == "active_users"),
        key=lambda s: s.timestamp,
    )
    reasons = []
    for stage in stages:
        if stage.from_users != stage.to_users:
            continue
        begin, end = stage.start_seconds + tolerance.grace, stage.end_seconds
        if begin >= end:
            reasons.append(f"occupancy grace consumes hold {stage.id}")
            continue
        covered = 0.0
        for i, sample in enumerate(observations):
            gap = sample.max_gap_seconds
            if (
                sample.kind != "gauge"
                or gap is None
                or gap <= 0
                or sample.value is None
                or not math.isfinite(sample.value)
            ):
                reasons.append("invalid occupancy gauge")
                continue
            stop = min(
                sample.timestamp + gap,
                observations[i + 1].timestamp if i + 1 < len(observations) else end,
            )
            overlap = max(0.0, min(end, stop) - max(begin, sample.timestamp))
            if overlap:
                covered += overlap
                if abs(sample.value - stage.to_users) > max(
                    1e-9, stage.to_users * tolerance.fraction
                ):
                    reasons.append(f"hold occupancy outside tolerance: {stage.id}")
        if covered / (end - begin) < 0.95:
            reasons.append(f"insufficient occupancy coverage: {stage.id}")
    return list(dict.fromkeys(reasons))

