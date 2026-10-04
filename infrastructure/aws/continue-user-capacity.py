"""Apply the updated controller after a completed runtime, preserving measured results.

Run on benchmark-monitor as root. This controls only the benchmark controller;
it never stops AWS instances or RDS. A cleanup problem leaves the campaign stopped.
"""

import json
import subprocess
import time
from pathlib import Path

ROOT = Path("/home/rocky/language-benchmark")
RESULTS = ROOT / "artifacts/results/user-capacity"
UNIT = "language-benchmark-user-capacity.service"


def systemctl(*args):
    return subprocess.run(["systemctl", *args, UNIT], check=True, capture_output=True, text=True)


def main():
    while True:
        results_file = RESULTS / "capacity-results.json"
        if results_file.exists():
            results = json.loads(results_file.read_text())
            if results:
                if any(
                    r["status"] not in ("bracket_found", "ceiling_reached")
                    for r in results.values()
                ):
                    raise RuntimeError(
                        "Runtime evidence requires attention; no automatic continuation"
                    )
                break
        if systemctl("show", "-p", "ActiveState", "--value").stdout.strip() != "active":
            raise RuntimeError("Controller stopped before a valid runtime completed")
        time.sleep(5)

    # Signal the entire unit so its Python controller performs guarded cleanup.
    if systemctl("show", "-p", "ActiveState", "--value").stdout.strip() == "active":
        systemctl("kill", "--signal=SIGINT", "--kill-whom=all")
    deadline = time.monotonic() + 600
    while systemctl("show", "-p", "ActiveState", "--value").stdout.strip() in (
        "active",
        "activating",
        "deactivating",
    ):
        if time.monotonic() >= deadline:
            raise RuntimeError("Graceful cleanup did not finish; do not restart")
        time.sleep(5)
    manifests = sorted(RESULTS.glob("*/manifest.json"))
    latest = json.loads(manifests[-1].with_name("result.json").read_text())
    if not latest.get("reset_verified"):
        raise RuntimeError("Final fixture restoration is not verified; do not restart")
    systemctl("restart")
    print("Updated controller resumed with completed runtime results preserved", flush=True)


if __name__ == "__main__":
    main()
