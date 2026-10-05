"""Request a supervised restart after the current probe is saved, without losing valid work."""

import json
import subprocess
import time
from pathlib import Path

ROOT = Path("/home/rocky/language-benchmark")
RESULTS = ROOT / "artifacts/results/user-capacity"
UNIT = "language-benchmark-user-capacity"
STATE = ROOT / "artifacts/aws/fast-confirmation-handoff.json"


def active():
    return (
        subprocess.run(["systemctl", "is-active", "--quiet", UNIT], capture_output=True).returncode
        == 0
    )


def record(status):
    STATE.write_text(json.dumps({"status": status, "updated_epoch": time.time()}, indent=2))


def main():
    manifests = sorted(RESULTS.glob("*/manifest.json"))
    if not manifests:
        raise RuntimeError("current probe manifest missing")
    target = manifests[-1].with_name("capacity-outcome.json")
    record("waiting_for_probe_completion")
    while active() and not target.exists():
        time.sleep(5)
    if active():
        # The regular lifecycle stops load/backend and verifies restoration.
        # The persistent supervisor then restarts with checkpoints and the new policy.
        subprocess.run(
            ["systemctl", "kill", "--signal=SIGINT", "--kill-whom=all", UNIT], check=True
        )
        record("restart_requested")
    else:
        record("controller_already_stopped")


if __name__ == "__main__":
    main()
