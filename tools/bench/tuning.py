"""Explicit machine profiles; legacy locks and defaults remain authoritative."""

import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

from typing_extensions import TypedDict

from .models import NativeCell, RuntimeCell
from .records import NativeContainer, checked_record

TUNED_RESOURCES = {"cpus": 4, "memory": 6442450944, "swap": 0, "nofile": 250000}
BASE_CELLS = (
    "python-modern-3.14.8-standard-jit0-gil1-async",
    "go-net-http",
    "rust-axum",
    "java-spring-boot",
    "csharp-aspnet-core",
    "node-express",
    "bun-express",
    "php-laravel",
    "php-laravel-octane",
)


class ProfileEntry(TypedDict):
    base_cell: str
    id: str
    image: str
    environment: dict[str, str]
    build_args: dict[str, str]


class ProfileRegistry(TypedDict):
    schema_version: int
    resources: dict[str, int]
    source_locks: dict[str, str]
    cells: list[ProfileEntry]


@dataclass(frozen=True)
class TunedProfile:
    base_cell: str
    cell: RuntimeCell | NativeCell
    resources: dict[str, int]
    environment: dict[str, str]
    build_args: dict[str, str]


def _controls(base: str) -> tuple[dict[str, str], dict[str, str]]:
    language = base.split("-")[0]
    environment = {"HASH_CONCURRENCY": "4"}
    build = environment.copy()
    if language == "python":
        environment = {
            "APP_VARIANT": "modern",
            "ADAPTER": "async",
            "WORKERS": "4",
            "HASH_CONCURRENCY": "1",
        }
        build = {
            "APP": "modern",
            "WORKERS": "4",
            "HASH_CONCURRENCY": "1",
            "CONNECTIONS_PER_WORKER": "5",
        }
    elif language in ("node", "bun"):
        environment = {"WORKERS": "4"}
        build = {"WORKERS": "4", "CONNECTIONS_PER_WORKER": "5"}
        if language == "node":
            environment["NODE_OPTIONS"] = "--max-old-space-size=1024"
    elif language == "go":
        environment.update(GOMAXPROCS="4", GOMEMLIMIT="4GiB")
    elif language == "rust":
        environment["CPU_PARALLELISM"] = "4"
        build["CPU_PARALLELISM"] = "4"
    elif language == "java":
        environment["JAVA_TOOL_OPTIONS"] = "-XX:MaxRAMPercentage=65 -XX:ActiveProcessorCount=4"
    elif language == "csharp":
        environment.update(DOTNET_PROCESSOR_COUNT="4", DOTNET_GCHeapHardLimitPercent="0x41")
    return environment, build


def load_tuned_profiles(
    registry_root: str | Path = Path("runtimes"), *, path: Path | None = None
) -> list[TunedProfile]:
    from .native_runtime import load_runtime_cell

    root = Path(registry_root)
    document = checked_record(
        json.loads((path or root / "profiles/tuned4.json").read_text()), ProfileRegistry
    )
    if (
        set(document) != set(ProfileRegistry.__annotations__)
        or document["schema_version"] != 1
        or document["resources"] != TUNED_RESOURCES
    ):
        raise ValueError("uncontrolled tuned profile budget/schema")
    expected_paths = {
        "python/lock.json",
        "go/lock.json",
        "rust/lock.json",
        "java/lock.json",
        "csharp/lock.json",
        "node/lock.json",
        "bun/lock.json",
        "php/lock.json",
        "php-octane/lock.json",
    }
    if set(document["source_locks"]) != expected_paths or any(
        hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest
        for relative, digest in document["source_locks"].items()
    ):
        raise ValueError("tuned original runtime lock fingerprint mismatch")
    entries = document["cells"]
    if len(entries) != len(BASE_CELLS) or {entry["base_cell"] for entry in entries} != set(
        BASE_CELLS
    ):
        raise ValueError("tuned profiles require nine unique frozen base cells")
    profiles = []
    for entry in entries:
        if set(entry) != set(ProfileEntry.__annotations__):
            raise ValueError("tuned registry contains uncontrolled version/topology fields")
        original = load_runtime_cell(entry["base_cell"], root)
        environment, build_args = _controls(original.id)
        if (
            entry["id"] != original.id + "-tuned4"
            or entry["image"] != original.image + "-tuned4"
            or entry["environment"] != environment
            or entry["build_args"] != build_args
        ):
            raise ValueError("tuned identity/version/configuration mismatch")
        workers = 4 if original.id.startswith(("python", "node", "bun")) else original.workers
        if isinstance(original, NativeCell):
            cell: RuntimeCell | NativeCell = replace(
                original,
                id=entry["id"],
                image=entry["image"],
                profile="tuned4",
                workers=workers,
                connections_per_worker=20 // workers,
                topology=f"tuned4; workers{workers}; pool20; aggregate hash4",
            )
        else:
            cell = replace(
                original, id=entry["id"], image=entry["image"], profile="tuned4", workers=workers
            )
        profiles.append(
            TunedProfile(original.id, cell, document["resources"].copy(), environment, build_args)
        )
    return profiles


def profile_for_cell(
    cell: RuntimeCell | NativeCell, registry_root: str | Path = Path("runtimes")
) -> TunedProfile | None:
    if not cell.profile:
        return None
    for profile in load_tuned_profiles(registry_root):
        if cell == profile.cell:
            return profile
    raise ValueError("cell differs from explicit tuned registry")


class TuningReceipt(TypedDict):
    schema_version: int
    run_id: str
    pid: int
    language: str
    workers_total: int
    connections_per_worker: int
    pool_budget: int
    cpu_parallelism: int | None
    hash_concurrency: int
    hash_scope: str


class ReceiptFile(TypedDict):
    filename: str
    modified_at: float
    receipt: TuningReceipt


class ServingProcess(TypedDict):
    pid: int
    command: str
    started_at: float


def _serving(profile: TunedProfile, command: str) -> bool:
    base = profile.base_cell
    words = command.split()
    if not words:
        return False
    executable = Path(words[0]).name
    if base.startswith(("go-", "rust-")):
        return executable == "social-api"
    if base.startswith("java-"):
        return executable == "java" and words[1:] == ["-jar", "/app/api.jar"]
    if base.startswith("csharp-"):
        return executable == "dotnet" and words[1:] == ["BenchmarkApi.dll"]
    if base.startswith(("node-", "bun-")):
        return executable in ("node", "bun") and any(
            word.endswith("/src/worker.js") for word in words[1:]
        )
    if base.startswith("python-"):
        return (
            executable.startswith("python")
            and "multiprocessing.spawn" in command
            and "spawn_main" in command
        )
    if base == "php-laravel-octane":
        return executable == "php" and "/app/vendor/bin/roadrunner-worker" in words
    return command.startswith("php-fpm: pool www")


def validate_tuning_receipts(
    profile: TunedProfile,
    rows: list[ReceiptFile],
    processes: list[ServingProcess],
    run_id: str,
    container_started_at: float,
) -> list[TuningReceipt]:
    """Compare receipts with independent /proc process count, liveness and start times."""
    serving = {
        process["pid"]: process for process in processes if _serving(profile, process["command"])
    }
    if (
        not run_id
        or not math.isfinite(container_started_at)
        or any(p["pid"] <= 0 or not math.isfinite(p["started_at"]) for p in processes)
        or len(serving) != profile.cell.workers
        or len({p["pid"] for p in processes}) != len(processes)
    ):
        raise ValueError("tuned serving process count mismatch")
    language = "python" if isinstance(profile.cell, RuntimeCell) else profile.cell.language
    scope = (
        "worker"
        if language in ("python", "node", "bun")
        else "global"
        if language == "php"
        else "process"
    )
    expected = dict(
        schema_version=1,
        run_id=run_id,
        language=language,
        workers_total=profile.cell.workers,
        connections_per_worker=20 // profile.cell.workers,
        pool_budget=20,
        cpu_parallelism=4 if scope == "process" else None,
        hash_concurrency=1 if scope == "worker" else 4,
        hash_scope=scope,
    )
    receipts = []
    for row in rows:
        receipt = checked_record(row["receipt"], TuningReceipt)
        if set(receipt) != set(TuningReceipt.__annotations__):
            raise ValueError("tuned receipt contains unapproved fields")
        pid = receipt["pid"]
        # Request recycling can leave dead PHP worker files. Live workers still
        # require current nonce, filename, start-time and exact serving PID proof.
        if pid not in serving:
            if language == "php" and not any(p["pid"] == pid for p in processes):
                continue
            raise ValueError("tuned receipt PID is not a live serving process")
        filename = f"{pid}.json" if language == "python" else f"tuning-{pid}.json"
        if (
            row["filename"] != filename
            or not math.isfinite(row["modified_at"])
            or row["modified_at"] < max(container_started_at, serving[pid]["started_at"]) - 1
        ):
            raise ValueError("stale tuned receipt file/process provenance")
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("tuned receipt topology/configuration mismatch")
        receipts.append(receipt)
    if len(receipts) != profile.cell.workers or {r["pid"] for r in receipts} != set(serving):
        raise ValueError("missing or duplicate tuned worker receipts")
    if sum(r["connections_per_worker"] for r in receipts) != 20:
        raise ValueError("tuned aggregate database budget mismatch")
    hashes = (
        sum(r["hash_concurrency"] for r in receipts)
        if scope == "worker"
        else receipts[0]["hash_concurrency"]
    )
    if hashes != 4:
        raise ValueError("tuned aggregate hash budget mismatch")
    return receipts


def validate_tuned_container(data: "NativeContainer", profile: TunedProfile, run_id: str) -> None:
    if not data.get("id") or data.get("running") is not True or data.get("run_id") != run_id:
        raise ValueError("tuned container identity is stale or invalid")
    resources = profile.resources
    if [data.get(k) for k in ("nano_cpus", "memory", "memory_swap")] != [
        resources["cpus"] * 1000000000,
        resources["memory"],
        resources["memory"],
    ]:
        raise ValueError("tuned container must enforce four CPUs, six GiB and no swap")
    limits = [item for item in data.get("ulimits", []) if item.get("Name") == "nofile"]
    if (
        len(limits) != 1
        or limits[0].get("Soft") != resources["nofile"]
        or limits[0].get("Hard") != resources["nofile"]
    ):
        raise ValueError("tuned container must enforce nofile=250000:250000")


# This probe runs inside the inspected container. No environment, credentials or
# host PID namespace is read. Linux cgroup-v2 limits independently prove quota.
PROCESS_PROBE = r"""
set -eu
printf 'C\t%s\t%s\t%s\n' "$(date +%s)" "$(cut -d ' ' -f 1 /proc/uptime)" "$(getconf CLK_TCK)"
printf 'Q\t%s\n' "$(cat /sys/fs/cgroup/cpu.max)"
printf 'M\t%s\n' "$(cat /sys/fs/cgroup/memory.max)"
printf 'S\t%s\n' "$(cat /sys/fs/cgroup/memory.swap.max)"
for directory in /proc/[0-9]*; do
  [ -r "$directory/stat" ] && [ -r "$directory/cmdline" ] || continue
  state=$(cat "$directory/stat" 2>/dev/null) || continue
  tail=${state##*) }
  set -- $tail
  start=${20}
  command=$(tr '\000\t\n' '   ' < "$directory/cmdline" 2>/dev/null) || continue
  printf 'P\t%s\t%s\t%s\n' "${directory##*/}" "$start" "$command"
done
for file in /tmp/bench-runtime/*.json; do
  [ -f "$file" ] || continue
  printf 'R\t%s\t%s\t' "${file##*/}" "$(stat -c %Y "$file")"
  tr '\n' ' ' < "$file"
  printf '\n'
done
"""


def parse_process_probe(
    text: str, profile: TunedProfile
) -> tuple[list[ReceiptFile], list[ServingProcess]]:
    fields = [line.split("\t", 3) for line in text.splitlines()]
    clock = [row for row in fields if row[0] == "C"]
    quota = [row for row in fields if row[0] == "Q"]
    memory = [row for row in fields if row[0] == "M"]
    swap = [row for row in fields if row[0] == "S"]
    if any(len(rows) != 1 for rows in (clock, quota, memory, swap)):
        raise ValueError("missing/duplicate cgroup process proof")
    try:
        sampled, uptime, ticks = map(float, clock[0][1:])
        cpu_quota, period = map(int, quota[0][1].split())
        if (
            not all(math.isfinite(value) for value in (sampled, uptime, ticks))
            or ticks <= 0
            or period <= 0
            or cpu_quota / period != 4
            or int(memory[0][1]) != 6442450944
            or int(swap[0][1]) != 0
        ):
            raise ValueError("tuned live cgroup limits mismatch")
        processes = [
            ServingProcess(
                pid=int(row[1]), started_at=sampled - uptime + int(row[2]) / ticks, command=row[3]
            )
            for row in fields
            if row[0] == "P"
        ]
        files = []
        for row in fields:
            if row[0] != "R":
                continue
            # Python readiness records embed tuning; other runtimes publish a
            # separate tuning file. Express <PID>.json files are not receipts.
            if not isinstance(profile.cell, RuntimeCell) and not row[1].startswith("tuning-"):
                continue
            data = json.loads(row[3])
            value = data.get("tuning") if isinstance(profile.cell, RuntimeCell) else data
            files.append(
                ReceiptFile(
                    filename=row[1],
                    modified_at=float(row[2]),
                    receipt=checked_record(value, TuningReceipt),
                )
            )
        return files, processes
    except (IndexError, TypeError, ValueError, KeyError):
        raise ValueError("invalid tuned live cgroup/process/receipt proof") from None


def validate_profile_configuration(
    profile: TunedProfile, resources: dict[str, float | int], environment: dict[str, str]
) -> None:
    if any(resources.get(key) != value for key, value in profile.resources.items()):
        raise ValueError("tuned target resources differ from the selected profile")
    if any(environment.get(key) != value for key, value in profile.environment.items()):
        raise ValueError("tuned target requires explicit matching profile environment references")
