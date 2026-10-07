"""Explicit Linux host, Docker-container, generator and opt-in RDS evidence."""

import json
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import JsonValue
from typing_extensions import TypedDict

from ..records import checked_record
from .records import HostPoint, ProbePoint, RdsConfig
from .targets import execute_host
from .types import Host, ResolvedJob, Sample, SampleScope

HOST_PROBE = """import json,pathlib,time
stat=[int(v) for v in pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
mem={line.split(':')[0]:int(line.split(':')[1].split()[0])*1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
print(json.dumps(dict(epoch=time.time(),cpu=stat,memory_total=mem['MemTotal'],memory_available=mem['MemAvailable'])))
"""
DOCKER_PROBE = """import http.client,socket,json,sys,time
class DockerConnection(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(10);self.sock.connect('/var/run/docker.sock')
c=DockerConnection('localhost');c.request('GET','/containers/'+sys.argv[1]+'/stats?stream=false&one-shot=true');r=c.getresponse()
if r.status!=200:raise RuntimeError('Docker stats unavailable')
d=json.load(r);d['epoch']=time.time();print(json.dumps(d))
"""


@dataclass
class Collector:
    scope: SampleScope
    host: Host
    mode: str
    interval: float
    container_id: str | None = None
    quota: float | None = None
    available: bool = False
    reason: str | None = None
    raw: list[ProbePoint] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    database: dict[str, JsonValue] | None = None

    def probe(self) -> ProbePoint:
        if self.scope == "database":
            if not self.database or self.database.get("provider") != "rds":
                raise ValueError("database collector requires explicit RDS provider")
            import boto3

            config = checked_record(self.database, RdsConfig)
            # A read-only capability request happens only when explicitly opted in.
            boto3.client("cloudwatch", region_name=config["region"]).list_metrics(
                Namespace="AWS/RDS",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": config["identifier"]}],
            )
            return {"epoch": time.time()}
        argv = ["python3", "-c", HOST_PROBE]
        if self.scope == "container":
            if not self.container_id:
                # Preflight verifies socket access; no target exists yet.
                argv = ["docker", "info", "--format", "{{json .ServerVersion}}"]
                execute_host(self.host, argv, timeout=15)
                return {"epoch": time.time()}
            argv = ["python3", "-c", DOCKER_PROBE, self.container_id]
        point = checked_record(
            json.loads(execute_host(self.host, argv, timeout=15).stdout), ProbePoint
        )
        if self.scope != "container":
            checked_record(point, HostPoint)
        return point

    def discover(self) -> "Collector":
        if self.mode == "off":
            self.reason = "disabled by configuration"
            return self
        try:
            self.probe()
            self.available = True
        except Exception as exc:
            self.reason = "capability unavailable: " + type(exc).__name__
            if self.mode == "required":
                raise RuntimeError(f"required {self.scope} collector unavailable") from None
        return self

    def samples(self, origin: float) -> list[Sample]:
        result = []
        for previous, point in zip(self.raw, self.raw[1:]):
            begin, end = previous["epoch"] - origin, point["epoch"] - origin
            if end <= begin or end - begin > max(self.interval * 4, 2):
                continue
            if self.scope == "container":
                if self.quota is None or self.quota <= 0:
                    raise ValueError("container collector requires positive CPU quota")
                before = previous["cpu_stats"]["cpu_usage"]["total_usage"]
                after = point["cpu_stats"]["cpu_usage"]["total_usage"]
                cpu = (after - before) / ((end - begin) * 1e9 * self.quota)
                memory = point.get("memory_stats", {}).get("usage")
                normalization = "container_quota"
                formula = "delta_cpu_nanoseconds / (elapsed_seconds * 1e9 * cpu_quota)"
            else:
                delta = [b - a for a, b in zip(previous["cpu"], point["cpu"])]
                if min(delta) < 0 or sum(delta) <= 0:
                    continue
                cpu = 1 - (delta[3] + delta[4]) / sum(delta)
                memory = point["memory_total"] - point["memory_available"]
                normalization = "all_host_cpus"
                formula = "1 - delta(idle+iowait) / delta(all_cpu_ticks)"
            common = SampleCommon(
                scope=self.scope,
                kind="interval",
                timestamp=begin,
                end_seconds=end,
                collection_interval=end - begin,
                provider_fields={
                    "provider": "docker_engine" if self.scope == "container" else "linux_proc"
                },
            )
            result.append(
                Sample(
                    metric="cpu_used",
                    value=cpu,
                    unit="fraction",
                    normalization=normalization,
                    formula=formula,
                    **common,
                )
            )
            if memory is not None:
                result.append(
                    Sample(
                        metric="memory_used",
                        value=float(memory),
                        unit="bytes",
                        formula="cgroup memory usage bytes"
                        if self.scope == "container"
                        else "MemTotal - MemAvailable",
                        **common,
                    )
                )
        return result


def collectors_for(job: ResolvedJob) -> tuple[Collector, ...]:
    policy = job.environment.telemetry
    required = {
        c.scope for c in [*job.criteria, *(c for group in job.objectives.values() for c in group)]
    }
    result = []
    sources: list[tuple[SampleScope, str, Host]] = [
        ("backend_host", policy.host, job.environment.backend),
        ("container", policy.container, job.environment.backend),
        ("generator", policy.generator, job.environment.generator),
        ("database", policy.database, job.environment.backend),
    ]
    for scope, mode, host in sources:
        if scope in required:
            mode = "required"
        result.append(
            Collector(
                scope,
                host,
                mode,
                policy.interval,
                quota=job.target.resources.cpus,
                database=job.environment.database,
            )
        )
    return tuple(result)


class Collection:
    def __init__(self, collectors: Sequence[Collector], directory: Path | None = None) -> None:
        self.directory = directory
        self.collectors = collectors
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        def loop(collector: Collector) -> None:
            while not self.stop.is_set():
                started = time.monotonic()
                try:
                    point = collector.probe()
                    if self.directory is not None:
                        from .evidence import immutable_json

                        immutable_json(
                            self.directory
                            / "raw"
                            / collector.scope
                            / f"{len(collector.raw):08d}.json",
                            point,
                        )
                    collector.raw.append(point)
                except Exception as exc:
                    collector.errors.append(type(exc).__name__)
                self.stop.wait(max(0.01, collector.interval - (time.monotonic() - started)))

        for collector in self.collectors:
            if collector.available and collector.scope != "database":
                thread = threading.Thread(target=loop, args=(collector,), daemon=True)
                self.threads.append(thread)
                thread.start()

    def finish(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(20)
            if thread.is_alive():
                raise RuntimeError("collector did not stop")


def rds_samples(collector: Collector, origin: float, end: float) -> list[Sample]:
    """CloudWatch averages are explicit minute intervals, including edge overlap."""
    import math
    from datetime import datetime, timezone

    import boto3

    config = checked_record(collector.database, RdsConfig)
    client = boto3.client("cloudwatch", region_name=config["region"])
    result = []
    for metric, name, unit, factor in [
        ("CPUUtilization", "cpu_used", "fraction", 0.01),
        ("FreeableMemory", "memory_available", "bytes", 1.0),
        ("DatabaseConnections", "connections", "count", 1.0),
    ]:
        data = client.get_metric_statistics(
            Namespace="AWS/RDS",
            MetricName=metric,
            Dimensions=[{"Name": "DBInstanceIdentifier", "Value": config["identifier"]}],
            StartTime=datetime.fromtimestamp(math.floor(origin / 60) * 60, timezone.utc),
            EndTime=datetime.fromtimestamp(math.ceil(end / 60) * 60, timezone.utc),
            Period=60,
            Statistics=["Average"],
        )
        for point in data["Datapoints"]:
            start = point["Timestamp"].timestamp() - origin
            result.append(
                Sample(
                    metric=name,
                    scope="database",
                    kind="interval",
                    timestamp=start,
                    end_seconds=start + 60,
                    value=float(point["Average"]) * factor,
                    unit=unit,
                    collection_interval=60.0,
                    normalization="rds_instance" if name == "cpu_used" else None,
                    formula="CloudWatch 60-second Average" + (" / 100" if factor == 0.01 else ""),
                    provider_fields={
                        "provider": "aws_cloudwatch",
                        "metric": metric,
                        "period_seconds": 60,
                        "statistic": "Average",
                    },
                )
            )
    return result


def health_reasons(collector: Collector, samples: Sequence[Sample], cpu_max: float) -> list[str]:
    reasons = []
    if collector.mode == "required" and (collector.errors or not samples):
        reasons.append("missing required " + collector.scope + " evidence")
    if collector.scope == "generator" and any(
        s.metric == "cpu_used" and s.value is not None and s.value >= cpu_max for s in samples
    ):
        reasons.append("load generator saturated")
    return reasons


class SampleCommon(TypedDict):
    scope: SampleScope
    kind: Literal["interval"]
    timestamp: float
    end_seconds: float
    collection_interval: float
    provider_fields: dict[str, JsonValue]
