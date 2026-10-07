"""Bounded recovery and reuse of immutable, qualified probe artifacts."""

from collections.abc import Callable
from .records import Campaign, Outcome, TrialResult, checked_record

import json
import time
from collections import defaultdict, deque
from pathlib import Path

from .transport import execute


def recover_hosts(config: Campaign) -> None:
    # Start an inactive daemon; never restart a running daemon or change AWS hardware.
    for host in (config.get("backend_host", {}), config.get("load_host", {})):
        if host.get("ssh"):
            execute(host, ["sudo", "systemctl", "start", "docker"], timeout=60)


def retry_probe(probe: Callable[[int], Outcome], users: int, recover: Callable[[], None], attempts: int = 3, delay: float = 30, sleep: Callable[[float], None] = time.sleep) -> Outcome:
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


def qualified_cache(root: Path) -> dict[tuple[str, int], deque[Outcome]]:
    for result_path in sorted(root.glob("*/result.json")):
        result = checked_record(json.loads(result_path.read_text()), TrialResult)
        if (
            result.get("status") == "measured"
            and result.get("reset_verified") is True
            and not result_path.with_name("capacity-outcome.json").exists()
        ):
            raise RuntimeError(
                "measured trial awaits qualification; finalize it before another probe"
            )
    cached: dict[tuple[str, int], deque[Outcome]] = defaultdict(deque)
    for path in sorted(root.glob("*/capacity-outcome.json")):
        outcome = checked_record(json.loads(path.read_text()), Outcome)
        result_path = path.with_name("result.json")
        if not result_path.exists():
            continue
        try:
            result = checked_record(json.loads(result_path.read_text()), TrialResult)
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
