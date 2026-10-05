"""Bounded recovery and reuse of immutable, qualified probe artifacts."""

import json
import time
from collections import defaultdict, deque
from pathlib import Path

from .transport import execute


def recover_hosts(config: dict):
    # Start an inactive daemon; never restart a running daemon or change AWS hardware.
    for role in ("backend_host", "load_host"):
        host = config.get(role, {})
        if host.get("ssh"):
            execute(host, ["sudo", "systemctl", "start", "docker"], timeout=60)


def retry_probe(probe, users, recover, attempts=3, delay=30, sleep=time.sleep):
    for attempt in range(attempts):
        result = probe(users)
        if result["outcome"] != "invalid" or result.get("lifecycle_status") == "interrupted":
            return result
        if attempt + 1 == attempts:
            return result
        sleep(delay)
        try:
            recover()
        except Exception:
            # The next guarded trial records connection/cleanup failures in its artifacts.
            pass
    raise ValueError("at least one attempt required")


def qualified_cache(root: Path):
    cached = defaultdict(deque)
    for path in sorted(root.glob("*/capacity-outcome.json")):
        outcome = json.loads(path.read_text())
        result_path = path.with_name("result.json")
        if not result_path.exists():
            continue
        try:
            result = json.loads(result_path.read_text())
        except ValueError:
            continue
        if (
            outcome.get("outcome") in ("pass", "fail")
            and result.get("status") == "measured"
            and result.get("reset_verified")
        ):
            trial = result["trial"]
            cached[(trial["cell"], trial["users"])].append(outcome)
    return cached
