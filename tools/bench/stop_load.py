"""Stop owned Compose load containers, including detached --rm one-offs."""

import json
import re
import time
from pathlib import PurePosixPath

from .transport import CommandFailure, execute

PROJECT = "language-benchmark-load"
FORMAT = (
    '{"id":{{json .Id}},"running":{{json .State.Running}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"working_dir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}}}'
)


def stop_owned_load(
    host, working_dir, execute_command=execute, sleep=time.sleep, monotonic=time.monotonic
):
    if not isinstance(working_dir, str) or not PurePosixPath(working_dir).is_absolute():
        raise ValueError("absolute Compose load working directory required")
    filters = [
        "--filter",
        "label=com.docker.compose.project=" + PROJECT,
        "--filter",
        "label=com.docker.compose.service=k6",
        "--filter",
        "label=com.docker.compose.project.working_dir=" + working_dir,
    ]

    def listed():
        ids = execute_command(
            host, ["docker", "ps", "--all", "--quiet", "--no-trunc", *filters], timeout=30
        ).stdout.split()
        if any(not re.fullmatch(r"[0-9a-f]{64}", identity) for identity in ids):
            raise RuntimeError("invalid container identity")
        return ids

    def optional(command, timeout=30):
        try:
            return execute_command(host, command, timeout=timeout)
        except CommandFailure as e:
            if "no such container" in e.stderr.lower() or "no such object" in e.stderr.lower():
                return None
            raise

    def inspect(identity):
        raw = optional(["docker", "inspect", "--format", FORMAT, identity])
        if raw is None:
            return None
        data = json.loads(raw.stdout)
        if (
            data.get("id") != identity
            or data.get("project") != PROJECT
            or data.get("service") != "k6"
            or data.get("working_dir") != working_dir
        ):
            raise RuntimeError("load container ownership does not match the campaign")
        return data

    found = listed()
    stopped = []
    removed = []
    for identity in found:
        data = inspect(identity)
        if data is None:
            continue
        if data.get("running"):
            optional(["docker", "stop", "--signal", "SIGINT", "--time", "30", identity], timeout=60)
            stopped.append(identity)
        remaining = inspect(identity)
        if remaining is not None:
            if remaining.get("running"):
                raise RuntimeError("owned k6 did not stop; fixture reset blocked")
            optional(["docker", "rm", identity])
            removed.append(identity)
    deadline = monotonic() + 30
    while listed():
        if monotonic() >= deadline:
            raise RuntimeError("owned load containers remain; fixture reset blocked")
        sleep(0.25)
    return {
        "discovered": found,
        "stopped": stopped,
        "removed": removed,
        "verified_gone": True,
        "ownership": {
            "compose_project": PROJECT,
            "compose_service": "k6",
            "working_dir": working_dir,
        },
    }
