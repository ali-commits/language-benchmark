"""Lightweight host counters, container resource state, and PostgreSQL activity."""

from collections.abc import Mapping
from pydantic import TypeAdapter
from datetime import datetime
from types import TracebackType
from .records import TelemetrySample, HostSample, RdsEvidence, RdsPoint, RdsEvent, checked_record

import json
import threading
import time
from pathlib import Path

import psycopg

from .transport import execute

HOST_PROBE = "import json,pathlib,time; print(json.dumps({'time':time.time(),'stat':pathlib.Path('/proc/stat').read_text(),'meminfo':pathlib.Path('/proc/meminfo').read_text(),'netdev':pathlib.Path('/proc/net/dev').read_text(),'loadavg':pathlib.Path('/proc/loadavg').read_text(),'cpuinfo':pathlib.Path('/proc/cpuinfo').read_text(),'vmstat':pathlib.Path('/proc/vmstat').read_text()}))"


class Collector:
    def __init__(self, hosts: Mapping[str, Mapping[str, str]], dsn: str, path: Path) -> None:
        self.hosts = hosts
        self.dsn = dsn
        self.path = path
        self.stop = threading.Event()
        self.errors: list[str] = []

    def sample(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w") as f:
            while not self.stop.is_set():
                entry: TelemetrySample = {"time": time.time(), "hosts": {}}
                for name, host in self.hosts.items():
                    try:
                        entry["hosts"][name] = checked_record(json.loads(
                            execute(host, ["python3", "-c", HOST_PROBE], timeout=10).stdout
                        ), HostSample)
                        if name == "backend":
                            read = "import pathlib,json,os; print(json.dumps([json.loads(p.read_text()) for p in pathlib.Path(os.environ['RUNTIME']).glob('*.metrics')]))"
                            if host.get("runtime_dir"):
                                entry["worker_pools"] = json.loads(
                                    execute(
                                        host,
                                        ["python3", "-c", read],
                                        {"RUNTIME": host["runtime_dir"]},
                                        timeout=10,
                                    ).stdout
                                )
                            entry["docker"] = execute(
                                host,
                                ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                                timeout=10,
                            ).stdout
                    except Exception as e:
                        self.errors.append(type(e).__name__)
                        entry["hosts"][name] = {"error": type(e).__name__}
                try:
                    with psycopg.connect(self.dsn, connect_timeout=5) as c:
                        entry["database"] = c.execute(
                            "SELECT numbackends,xact_commit,xact_rollback,blks_read,blks_hit,deadlocks,temp_bytes FROM pg_stat_database WHERE datname=current_database()"
                        ).fetchone()
                        entry["waits"] = c.execute(
                            "SELECT wait_event_type,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY wait_event_type"
                        ).fetchall()
                except Exception as e:
                    self.errors.append(type(e).__name__)
                    entry["database_error"] = type(e).__name__
                f.write(json.dumps(entry) + "\n")
                f.flush()
                self.stop.wait(2)

    def __enter__(self) -> "Collector":
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.stop.set()
        self.thread.join(30)
        if self.thread.is_alive():
            self.errors.append("collector shutdown timeout")


def rds_metrics(identifier: str, region: str, start: datetime, end: datetime) -> RdsEvidence:
    import boto3

    cw = boto3.client("cloudwatch", region_name=region)
    rds = boto3.client("rds", region_name=region)
    aligned_start, aligned_end = cloudwatch_window(start, end)
    result: RdsEvidence = {
        "statistics_window": {"start": aligned_start.isoformat(), "end": aligned_end.isoformat()},
        "measurement_window": {"start": start.isoformat(), "end": end.isoformat()},
    }
    for name in [
        "CPUUtilization",
        "DatabaseConnections",
        "FreeableMemory",
        "ReadIOPS",
        "WriteIOPS",
        "ReadLatency",
        "WriteLatency",
        "ReadThroughput",
        "WriteThroughput",
        "DiskQueueDepth",
    ]:
        points = cw.get_metric_statistics(
            Namespace="AWS/RDS",
            MetricName=name,
            Dimensions=[{"Name": "DBInstanceIdentifier", "Value": identifier}],
            StartTime=aligned_start,
            EndTime=aligned_end,
            Period=60,
            Statistics=["Average", "Maximum"],
        )["Datapoints"]
        result.update(checked_record({name: points}, RdsEvidence))
    events = rds.describe_events(
        SourceIdentifier=identifier, SourceType="db-instance", StartTime=start, EndTime=end
    )["Events"]
    instance = rds.describe_db_instances(DBInstanceIdentifier=identifier)["DBInstances"][
        0
    ]
    # Whitelist metadata; AWS response can contain internal service endpoints and secret references.
    result["events"] = checked_record(events, list[RdsEvent])
    result["instance"] = {
        k: instance.get(k)
        for k in [
            "DBInstanceClass",
            "EngineVersion",
            "AvailabilityZone",
            "StorageType",
            "AllocatedStorage",
            "Iops",
            "StorageThroughput",
            "DBParameterGroups",
        ]
    }
    return result


REQUIRED_RDS_METRICS = (
    "CPUUtilization",
    "DatabaseConnections",
    "FreeableMemory",
    "ReadIOPS",
    "WriteIOPS",
    "ReadLatency",
    "WriteLatency",
    "ReadThroughput",
    "WriteThroughput",
    "DiskQueueDepth",
)


def validate_rds_evidence(data: RdsEvidence, start: float, end: float) -> list[str]:
    from datetime import datetime

    reasons = []
    if not start or not end or end <= start:
        return ["RDS measurement interval missing"]

    def timestamp(value: object) -> float:
        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        if not isinstance(value, (int, float)):
            raise TypeError("invalid timestamp")
        return float(value)

    for metric in REQUIRED_RDS_METRICS:
        points = data.get(metric, [])
        if not isinstance(points, list):
            raise TypeError("RDS metric points must be a list")
        valid = []
        for point in points:
            try:
                if not isinstance(point, dict):
                    raise TypeError("invalid RDS point")
                # CloudWatch rounds statistics to minute buckets. Accept the bucket overlapping start.
                t = timestamp(point["Timestamp"])
                if start - 60 <= t <= end and ("Average" in point or "Maximum" in point):
                    valid.append(t)
            except (ValueError, TypeError, KeyError):
                continue
        expected_buckets = max(1, int((end - start) // 60))
        if len(set(valid)) < expected_buckets:
            reasons.append("RDS " + metric + " evidence does not cover measurement interval")
    events = data.get("events", [])
    if not isinstance(events, list):
        raise TypeError("RDS events must be a list")
    for event in events:
        try:
            if not isinstance(event, dict):
                raise TypeError("invalid RDS event")
            inside = start <= timestamp(event["Date"]) <= end
        except (KeyError, TypeError, ValueError):
            reasons.append("RDS event has invalid timestamp")
            continue
        if inside and any(
            word in event.get("Message", "").lower()
            for word in ("failover", "maintenance", "restart", "reboot", "recovery", "unavailable")
        ):
            reasons.append("RDS disruptive event during measurement: " + event.get("Message", ""))
    return reasons


def cloudwatch_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    import math
    from datetime import datetime, timezone

    if end <= start:
        raise ValueError("invalid measurement interval")
    return (
        datetime.fromtimestamp(math.floor(start.timestamp() / 60) * 60, timezone.utc),
        datetime.fromtimestamp(math.ceil(end.timestamp() / 60) * 60, timezone.utc),
    )
