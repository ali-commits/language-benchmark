"""Explicit machine profiles; legacy locks and defaults remain authoritative."""

from __future__ import annotations

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
    loader = load_production_profiles if cell.profile == "production4" else load_tuned_profiles
    for profile in loader(registry_root):
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
    receipt: TuningReceipt | ProductionReceipt


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
) -> list[TuningReceipt | ProductionReceipt]:
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
        schema_version=2 if profile.cell.profile == "production4" else 1,
        run_id=run_id,
        language=language,
        workers_total=profile.cell.workers,
        connections_per_worker=profile.cell.pool_budget // profile.cell.workers,
        pool_budget=profile.cell.pool_budget,
        cpu_parallelism=4 if scope == "process" else None,
        hash_concurrency=1 if scope == "worker" else 4,
        hash_scope=scope,
    )
    extras = (
        _production_receipt_fields(profile.base_cell)
        if profile.cell.profile == "production4"
        else {}
    )
    expected.update(extras)
    receipts = []
    for row in rows:
        receipt = checked_record(
            row["receipt"],
            ProductionReceipt if profile.cell.profile == "production4" else TuningReceipt,
        )
        if set(receipt) != set(TuningReceipt.__annotations__) | set(extras):
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
    if sum(r["connections_per_worker"] for r in receipts) != profile.cell.pool_budget:
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
                    receipt=checked_record(
                        value,
                        ProductionReceipt
                        if profile.cell.profile == "production4"
                        else TuningReceipt,
                    ),
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


class ProductionRegistry(ProfileRegistry):
    dependency_locks: dict[str, str]


def _production_controls(base: str) -> tuple[dict[str, str], dict[str, str]]:
    language = base.split("-")[0]
    env = {"BENCH_PROFILE": "production4", "HASH_CONCURRENCY": "4"}
    build = {"HASH_CONCURRENCY": "4"}
    if language == "python":
        env.update(
            APP_VARIANT="modern",
            ADAPTER="async",
            WORKERS="4",
            HASH_CONCURRENCY="1",
            DB_POOL_MIN_SIZE="2",
            DB_POOL_MAX_SIZE="10",
            UVICORN_LOOP="uvloop",
            UVICORN_HTTP="httptools",
        )
        build = dict(
            APP="modern",
            DEPENDENCY_PROFILE="production",
            WORKERS="4",
            HASH_CONCURRENCY="1",
            CONNECTIONS_PER_WORKER="10",
            POOL_BUDGET="40",
        )
    elif language in ("node", "bun"):
        env = dict(
            BENCH_PROFILE="production4",
            WORKERS="4",
            UV_THREADPOOL_SIZE="4",
            DB_POOL_MAX_SIZE="10",
            NODE_ENV="production",
        )
        build = dict(
            WORKERS="4", CONNECTIONS_PER_WORKER="10", POOL_BUDGET="40", UV_THREADPOOL_SIZE="4"
        )
        if language == "node":
            env["NODE_OPTIONS"] = "--max-old-space-size=1024"
    elif language == "php":
        env.update(
            PHP_WORKERS="32",
            PHP_MEMORY_LIMIT="128M",
            OPCACHE_MEMORY="256",
            OPCACHE_INTERNED_STRINGS="16",
            DB_PERSISTENT="1" if base == "php-laravel" else "0",
            DB_SESSION_RESET="1",
            RR_MAX_WORKER_MEMORY="256",
        )
        build.update({k: v for k, v in env.items() if k != "BENCH_PROFILE"})
    else:
        minimum = "16" if language == "java" else "4"
        env.update(DB_POOL_MIN_SIZE=minimum, DB_POOL_MAX_SIZE="16")
        build.update(DB_POOL_MIN_SIZE=minimum, DB_POOL_MAX_SIZE="16")
        if language == "go":
            env.update(GOMAXPROCS="4", GOMEMLIMIT="5GiB", GOGC="100")
        elif language == "rust":
            env["CPU_PARALLELISM"] = "4"
            build["CPU_PARALLELISM"] = "4"
        elif language == "java":
            env.update(
                JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=65 -XX:ActiveProcessorCount=4",
                HASH_EXECUTOR_MODE="platform",
            )
            build["HASH_EXECUTOR_MODE"] = "platform"
        elif language == "csharp":
            env.update(
                DOTNET_PROCESSOR_COUNT="4",
                DOTNET_GCHeapHardLimitPercent="0x41",
                DB_PREPARE_ENABLED="true",
            )
            build["DB_PREPARE_ENABLED"] = "true"
    return env, build


def load_production_profiles(
    registry_root: str | Path = Path("runtimes"), *, path: Path | None = None
) -> list[TunedProfile]:
    from .engine.resources import dependency_lock_path
    from .native_runtime import load_runtime_cell

    root = Path(registry_root)
    d = checked_record(
        json.loads((path or root / "profiles/production4.json").read_text()), ProductionRegistry
    )
    if (
        set(d) != set(ProductionRegistry.__annotations__)
        or d["schema_version"] != 2
        or d["resources"] != TUNED_RESOURCES
    ):
        raise ValueError("uncontrolled production profile budget/schema")
    original_locks = checked_record(
        json.loads((root / "profiles/tuned4.json").read_text()), ProfileRegistry
    )["source_locks"]
    if d["source_locks"] != original_locks or any(
        hashlib.sha256((root / k).read_bytes()).hexdigest() != v for k, v in original_locks.items()
    ):
        raise ValueError("production original lock fingerprint mismatch")
    paths = {
        "backends/python/modern/requirements.lock",
        "backends/python/production/requirements.lock",
        "backends/python/production/uv.lock",
        "backends/python/production/pyproject.toml",
    }
    if set(d["dependency_locks"]) != paths or any(
        hashlib.sha256(dependency_lock_path(k).read_bytes()).hexdigest() != v
        for k, v in d["dependency_locks"].items()
    ):
        raise ValueError("production dependency fingerprint mismatch")

    def pins(relative: str) -> dict[str, str]:
        import re

        return dict(
            re.findall(r"^([\w.-]+)==([^\s;]+)", dependency_lock_path(relative).read_text(), re.M)
        )

    old = pins("backends/python/modern/requirements.lock")
    new = pins("backends/python/production/requirements.lock")
    if (
        not old
        or any(new.get(k) != v for k, v in old.items())
        or set(new) - set(old) != {"uvloop", "httptools"}
    ):
        raise ValueError(
            "production dependencies must preserve every old pin plus two accelerators"
        )
    if len(d["cells"]) != 9 or {e["base_cell"] for e in d["cells"]} != set(BASE_CELLS):
        raise ValueError("production requires nine unique frozen cells")
    result = []
    for e in d["cells"]:
        original = load_runtime_cell(e["base_cell"], root)
        env, build = _production_controls(original.id)
        if (
            set(e) != set(ProfileEntry.__annotations__)
            or e["id"] != original.id + "-production4"
            or e["image"] != original.image + "-production4"
            or e["environment"] != env
            or e["build_args"] != build
        ):
            raise ValueError("production identity/version/configuration mismatch")
        workers = (
            4
            if original.id.startswith(("python", "node", "bun"))
            else 32
            if original.id.startswith("php")
            else 1
        )
        budget = 40 if workers == 4 else 32 if workers == 32 else 16
        if isinstance(original, RuntimeCell):
            cell: RuntimeCell | NativeCell = replace(
                original,
                id=e["id"],
                image=e["image"],
                profile="production4",
                workers=workers,
                pool_budget=budget,
                dependency_lock="backends/python/production/requirements.lock",
            )
        else:
            cell = replace(
                original,
                id=e["id"],
                image=e["image"],
                profile="production4",
                workers=workers,
                pool_budget=budget,
                connections_per_worker=budget // workers,
                topology=f"production4; workers{workers}; pool{budget}; aggregate hash4",
            )
        result.append(TunedProfile(original.id, cell, d["resources"].copy(), env, build))
    return result


class ProductionReceipt(TuningReceipt, total=False):
    pool_min_size: int
    event_loop: str
    http_parser: str
    worker_mode: str
    libuv_threads: int
    hash_executor_mode: str
    db_prepare_enabled: bool
    db_max_auto_prepare: int
    db_auto_prepare_min_usages: int
    db_enlist: bool
    db_connection_reuse: str
    db_session_reset: bool
    memory_limit: str
    opcache_memory: int
    opcache_interned_strings: int


def _production_receipt_fields(base: str) -> dict[str, int | str | bool]:
    language = base.split("-")[0]
    if language == "python":
        return dict(pool_min_size=2, event_loop="uvloop", http_parser="httptools")
    if language in ("node", "bun"):
        return dict(worker_mode="cluster", libuv_threads=4)
    if language == "php":
        return dict(
            db_connection_reuse="pdo-persistent" if base == "php-laravel" else "worker-retained",
            db_session_reset=True,
            memory_limit="128M",
            opcache_memory=256,
            opcache_interned_strings=16,
        )
    fields: dict[str, int | str | bool] = dict(pool_min_size=16 if language == "java" else 4)
    if language == "java":
        fields["hash_executor_mode"] = "platform"
    elif language == "csharp":
        fields.update(
            db_prepare_enabled=True,
            db_max_auto_prepare=64,
            db_auto_prepare_min_usages=5,
            db_enlist=False,
        )
    return fields
