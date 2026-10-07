"""Guard native deployments without importing Python worker-attestation schemas."""

import hashlib
import json
import re
import time
from dataclasses import asdict
from pathlib import Path

import httpx

from .models import NativeCell, RuntimeCell
from .transport import execute

NOFILE_LIMIT = 250000
REGISTRY_LANGUAGES = {
    "java": "java",
    "php": "php",
    "go": "go",
    "rust": "rust",
    "node": "node",
    "bun": "bun",
    "csharp": "csharp",
    "php-octane": "php",
}
LANGUAGES = tuple(REGISTRY_LANGUAGES)
LABEL_PREFIX = "org.language-benchmark."
BUDGET = {"cpus": 2, "memory_bytes": 3221225472, "swap_bytes": 0, "database_connections": 20}
LABEL_FIELDS = (
    "language",
    "runtime_version",
    "framework",
    "framework_version",
    "workers",
    "connections_per_worker",
)
CONTAINER_FORMAT = (
    '{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},'
    '"nano_cpus":{{.HostConfig.NanoCpus}},"memory":{{.HostConfig.Memory}},'
    '"ulimits":{{json .HostConfig.Ulimits}},"memory_swap":{{.HostConfig.MemorySwap}},"network_mode":{{json .HostConfig.NetworkMode}},'
    '"run_id":{{json (index .Config.Labels "org.language-benchmark.run-id")}},'
    '"cell_id":{{json (index .Config.Labels "org.language-benchmark.cell-id")}},'
    '"mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}'
    '{"Destination":{{json $m.Destination}},"RW":{{json $m.RW}}}{{end}}]}'
)
IMAGE_FORMAT = (
    '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},"labels":{'
    + ",".join(
        '"'
        + LABEL_PREFIX
        + field.replace("_", "-")
        + '":{{json (index .Config.Labels "'
        + LABEL_PREFIX
        + field.replace("_", "-")
        + '")}}'
        for field in LABEL_FIELDS
    )
    + "}}"
)


def _read_registry(path):
    data = json.loads(path.read_text())
    if path.parent.name != "python" and data.get("budget") != BUDGET:
        raise ValueError("native registry budget must enforce 2 CPUs, 3 GiB and 20 connections")
    return data


def validate_native_registry_cell(data, registry):
    if data["language"] != REGISTRY_LANGUAGES.get(registry):
        raise ValueError("native registry language mismatch")
    if registry == "php-octane" and (
        data["id"] != "php-laravel-octane" or data["framework"] != "laravel-octane"
    ):
        raise ValueError("Octane registry requires its explicit framework identity")
    if registry == "php" and data["framework"] != "laravel":
        raise ValueError("baseline PHP registry must remain Laravel FPM")
    return NativeCell(**data)


def load_runtime_cell(cell_id, registry_root=Path("runtimes")):
    """Python selection is deliberately independent of unselected native registries."""
    root = Path(registry_root)
    python = _read_registry(root / "python/lock.json")
    for data in python["cells"]:
        if data["id"] == cell_id:
            return RuntimeCell(**data)
    matches = []
    for language in LANGUAGES:
        path = root / language / "lock.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text())
        for data in raw["cells"]:
            if data["id"] == cell_id:
                _read_registry(path)
                matches.append(validate_native_registry_cell(data, language))
    if len(matches) != 1:
        raise ValueError("unknown or ambiguous runtime cell: " + cell_id)
    return matches[0]


def load_runtime_cells(registry_root=Path("runtimes")):
    root = Path(registry_root)
    cells = [RuntimeCell(**data) for data in _read_registry(root / "python/lock.json")["cells"]]
    for language in LANGUAGES:
        path = root / language / "lock.json"
        if not path.exists():
            continue
        for data in _read_registry(path)["cells"]:
            cells.append(validate_native_registry_cell(data, language))
    if len({cell.id for cell in cells}) != len(cells):
        raise ValueError("runtime cell identifiers must be unique")
    return cells


def registry_metadata(cell):
    directory = (
        "php-octane"
        if cell.language == "php" and cell.framework == "laravel-octane"
        else cell.language
    )
    path = Path("runtimes") / directory / "lock.json"
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def expected_image_id(config, cell):
    value = config.get("expected_image_ids", {}).get(cell.id)
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("native cell requires a verified expected_image_ids digest: " + cell.id)
    return value


def validate_native_container(data, cell, run_id, image):
    if (
        data.get("image") != image
        or data.get("running") is not True
        or data.get("run_id") != run_id
        or data.get("cell_id") != cell.id
    ):
        raise ValueError("native container image/run identity is stale or invalid")
    if [data.get(k) for k in ("nano_cpus", "memory", "memory_swap")] != [
        2000000000,
        3221225472,
        3221225472,
    ] or data.get("network_mode") != "host":
        raise ValueError("native backend must enforce 2 CPUs, 3 GiB, no swap and host networking")
    limits = [limit for limit in data.get("ulimits", []) if limit.get("Name") == "nofile"]
    if (
        len(limits) != 1
        or limits[0].get("Soft") != NOFILE_LIMIT
        or limits[0].get("Hard") != NOFILE_LIMIT
    ):
        raise ValueError("native backend must enforce nofile=250000:250000")
    mounts = data.get("mounts", [])
    if not any(
        m.get("Destination") == "/certs/rds-ca.pem" and m.get("RW") is False for m in mounts
    ):
        raise ValueError("native backend requires a read-only RDS CA mount")
    return {
        k: data[k]
        for k in (
            "id",
            "image",
            "running",
            "nano_cpus",
            "memory",
            "memory_swap",
            "network_mode",
            "run_id",
            "cell_id",
        )
    }


def native_image_metadata(data, cell, expected):
    if (
        data.get("id") != expected
        or data.get("os") != "linux"
        or data.get("architecture") != "amd64"
    ):
        raise ValueError("native image identity/platform differs from the verified image")
    wanted = {
        LABEL_PREFIX + field.replace("_", "-"): str(getattr(cell, field)) for field in LABEL_FIELDS
    }
    if any(data.get("labels", {}).get(key) != value for key, value in wanted.items()):
        raise ValueError("native image runtime/topology labels differ from the locked registry")
    return {
        "id": data["id"],
        "os": data["os"],
        "architecture": data["architecture"],
        "labels": wanted,
    }


def wait_native_ready(base_url, timeout=120):
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=5) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(base_url.rstrip("/") + "/health/ready")
                if response.status_code == 200 and response.json() == {"status": "ok"}:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise RuntimeError("native backend readiness did not become healthy")


def validate_process_nofile(text):
    matches = re.findall(r"Max open files\s+(\d+)\s+(\d+)\s+files", text)
    if not matches or any(
        (int(soft), int(hard)) != (NOFILE_LIMIT, NOFILE_LIMIT) for soft, hard in matches
    ):
        raise ValueError("native process nofile limit is missing or inconsistent")
    return {"soft": NOFILE_LIMIT, "hard": NOFILE_LIMIT, "process_samples": len(matches)}


def verify_runtime_nofile(backend, compose, env):
    ids = execute(backend, compose + ["ps", "--quiet"], env, timeout=30).stdout.split()
    if len(ids) != 1:
        raise ValueError("exactly one measured backend container required")
    limits = json.loads(
        execute(
            backend,
            ["docker", "inspect", "--format", "{{json .HostConfig.Ulimits}}", ids[0]],
            timeout=30,
        ).stdout
    )
    nofile = [item for item in limits or [] if item.get("Name") == "nofile"]
    if (
        len(nofile) != 1
        or nofile[0].get("Soft") != NOFILE_LIMIT
        or nofile[0].get("Hard") != NOFILE_LIMIT
    ):
        raise ValueError("backend must enforce nofile=250000:250000")
    text = execute(
        backend,
        [
            "docker",
            "exec",
            ids[0],
            "sh",
            "-c",
            'for path in /proc/[0-9]*/limits; do cat "$path" 2>/dev/null || true; done',
        ],
        timeout=30,
    ).stdout
    return validate_process_nofile(text)


def native_preflight(backend, cell, compose, env, base_url, run_id, expected):
    wait_native_ready(base_url)
    ids = execute(backend, compose + ["ps", "--quiet"], env, timeout=30).stdout.split()
    if len(ids) != 1:
        raise ValueError("exactly one measured native backend container required")
    container = json.loads(
        execute(
            backend, ["docker", "inspect", "--format", CONTAINER_FORMAT, ids[0]], timeout=30
        ).stdout
    )
    safe_container = validate_native_container(container, cell, run_id, expected)
    process_limits = execute(
        backend,
        [
            "docker",
            "exec",
            ids[0],
            "sh",
            "-c",
            'for path in /proc/[0-9]*/limits; do cat "$path" 2>/dev/null || true; done',
        ],
        timeout=30,
    ).stdout
    safe_container["nofile"] = validate_process_nofile(process_limits)
    raw_image = json.loads(
        execute(
            backend,
            ["docker", "image", "inspect", "--format", IMAGE_FORMAT, cell.image],
            timeout=30,
        ).stdout
    )
    image = native_image_metadata(raw_image, cell, expected)
    return {
        "cell": asdict(cell),
        "container": safe_container,
        "image": image,
        "attestation": "verified image digest, locked topology, fresh container nonce and HTTP readiness",
    }


def native_collector_hosts(backend, load):
    host = {key: value for key, value in backend.items() if key != "runtime_dir"}
    return {"backend": host, "generator": load}
