"""Bounded backend quiescence checks; never kill jobs or purge caches."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from .records import BaselinePolicy, BaselineSample, BaselineResult, checked_record

import json
import time

from .manifest import atomic_json
from .transport import execute

HOST_PROBE = r"""import json,os,pathlib,re,time
allowed=json.loads(os.environ.get("BASELINE_ALLOWED_IDS","[]"));blocked=[]
servers={"java","node","bun","nginx","social-api","dotnet","php","rr","frankenphp","k6","uvicorn","gunicorn"}
builds={"go","cargo","rustc","gcc","g++","cc","c++","clang","make","cmake","mvn","gradle","msbuild"}
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  if int(p.name)==os.getpid():continue
  name=(p/'comm').read_text().strip();group=(p/'cgroup').read_text()
  if any(identity in group for identity in allowed):continue
  if (p/'stat').read_text().split()[2]=='Z':continue
  bad=name in servers or name in builds or name.startswith('php-fpm')
  if name.startswith('python'):
   command=(p/'cmdline').read_bytes().decode(errors='replace')
   bad=any(term in command for term in ('uvicorn','gunicorn','baseline_api','modern_api','runtimes/python/build.py'))
  if bad:blocked.append({'pid':int(p.name),'name':name})
 except (OSError,ValueError,IndexError):pass
stat=[int(n) for n in pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
mem={r.split(':')[0]:int(r.split(':')[1].strip().split()[0]) for r in pathlib.Path('/proc/meminfo').read_text().splitlines()}
print(json.dumps({'epoch':time.time(),'cpu_counters':stat,'available_memory_mib':mem['MemAvailable']/1024,'unexpected_jobs':blocked}))"""


def sample_backend(host: Mapping[str, str], allowed_ids: Sequence[str], allowed_names: Sequence[str] = ()) -> BaselineSample:
    raw = execute(host, ["docker", "ps", "--no-trunc", "--format", "{{json .}}"], timeout=30).stdout
    containers = [json.loads(line) for line in raw.splitlines() if line.strip()]
    allowed_ids = list(allowed_ids) + [p["ID"] for p in containers if p["Names"] in allowed_names]
    probe = json.loads(
        execute(
            host,
            ["python3", "-c", HOST_PROBE],
            {"BASELINE_ALLOWED_IDS": json.dumps(allowed_ids)},
            timeout=30,
        ).stdout
    )
    probe["containers"] = [{"id": p["ID"], "name": p["Names"]} for p in containers]
    return checked_record(probe, BaselineSample)


def cpu_percent(previous: Sequence[int], current: Sequence[int]) -> float | None:
    delta = [b - a for a, b in zip(previous, current)]
    total = sum(delta)
    return 100 * (1 - (delta[3] + delta[4]) / total) if total > 0 else None


def wait_backend_baseline(
    host: Mapping[str, str], policy: BaselinePolicy, stage: str, output: Path,
    api_ids: Sequence[str] = (), probe: Callable[[], BaselineSample] | None = None,
    sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic
) -> BaselineResult | None:
    if not policy or not policy.get("enabled"):
        return None
    interval = policy.get("interval_seconds", 5)
    required = policy.get("consecutive_samples", 3)
    timeout = policy.get("timeout_seconds", 120)
    if stage not in ("stopped", "ready") or interval != 5 or required != 3 or timeout != 120:
        raise ValueError("unsupported baseline sampling policy")
    allowed_names = policy.get("allowed_telemetry_containers", [])
    if not isinstance(allowed_names, list) or any(
        not isinstance(n, str) or not n or "*" in n for n in allowed_names
    ):
        raise ValueError("exact telemetry container allowlist required")
    cpu_max = policy.get("stopped_cpu_max_percent", 15) if stage == "stopped" else policy.get("ready_cpu_max_percent", 20)
    mem_min = policy.get("stopped_mem_available_mib", 1024) if stage == "stopped" else policy.get("ready_mem_available_mib", 512)
    if cpu_max != (15 if stage == "stopped" else 20) or mem_min != (
        1024 if stage == "stopped" else 512
    ):
        raise ValueError("baseline thresholds cannot be weakened")
    get = probe or (lambda: sample_backend(host, list(api_ids), allowed_names))
    deadline = monotonic() + timeout
    previous = None
    good = 0
    samples: list[dict[str, object]] = []
    while monotonic() < deadline:
        point = get()
        cpu = cpu_percent(previous, point["cpu_counters"]) if previous else None
        previous = point["cpu_counters"]
        unexpected = [
            p
            for p in point.get("containers", [])
            if p.get("name") not in allowed_names
            and not any(identity.startswith(p.get("id", "!")) for identity in api_ids)
        ]
        okay = (
            cpu is not None
            and cpu <= cpu_max
            and point["available_memory_mib"] >= mem_min
            and not unexpected
            and not point.get("unexpected_jobs")
        )
        good = good + 1 if okay else 0
        samples.append(
            {
                "epoch": point["epoch"],
                "cpu_percent": cpu,
                "available_memory_mib": point["available_memory_mib"],
                "unexpected_containers": unexpected,
                "unexpected_jobs": point.get("unexpected_jobs", []),
                "consecutive_clean": good,
            }
        )
        record: BaselineResult = {
            "stage": stage,
            "status": "ready" if good >= required else "waiting",
            "cpu_limit_percent": cpu_max,
            "available_memory_min_mib": mem_min,
            "interval_seconds": interval,
            "required_consecutive": required,
            "samples": samples,
            "definition": "Whole backend host CPU delta across all cores; busy=total-idle-iowait. Memory is kernel MemAvailable headroom; no cache purge/reboot/forced job termination.",
        }
        atomic_json(output, record)
        if good >= required:
            return record
        sleep(interval)
    record["status"] = "timeout"
    atomic_json(output, record)
    raise RuntimeError("backend baseline did not settle; infrastructure evidence invalid")
