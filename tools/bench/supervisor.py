"""Monitor-host controller supervision with persisted, bounded recovery attempts."""

import argparse
import json
import subprocess
import time
from pathlib import Path

from .control import recover_hosts
from .manifest import atomic_json

COMPLETE = {"complete", "complete_with_unstable_boundaries"}


def read_status(path):
    return json.loads(path.read_text()) if path.exists() else {}


def controller_active(unit):
    return (
        subprocess.run(["systemctl", "is-active", "--quiet", unit], capture_output=True).returncode
        == 0
    )


def supervise(config_path, output, state_path, existing_unit, max_attempts=3, sleep=time.sleep):
    config = json.loads(config_path.read_text())
    if not 1 <= max_attempts <= 3:
        raise ValueError("supervisor recovery budget must be between one and three")
    previous = read_status(state_path)
    attempts = previous.get("controller_attempts", 0)

    def status(value, **details):
        atomic_json(
            state_path,
            {
                "status": value,
                "controller_attempts": attempts,
                "updated_epoch": time.time(),
                **details,
            },
        )

    # Adopt an already-running controller without restarting or competing with it.
    while controller_active(existing_unit):
        status("watching_existing_controller")
        sleep(15)
    campaign_status = output / "capacity-status.json"
    current = read_status(campaign_status).get("status")
    if current in COMPLETE:
        status(current)
        return
    while attempts < max_attempts:
        attempts += 1
        status("recovering")
        try:
            recover_hosts(config)
            status("running_controller")
            started = time.time()
            subprocess.run(
                ["sudo", "systemctl", "start", existing_unit], check=True, capture_output=True
            )
            while controller_active(existing_unit):
                sleep(15)
            current = read_status(campaign_status).get("status")
            if current in COMPLETE:
                status(current)
                return
            if (
                current == "needs_attention"
                and read_status(campaign_status).get("updated_epoch", 0) >= started
            ):
                # Probe-level retries have already exhausted their own bounded budget.
                status(
                    "needs_attention", reason="qualified evidence or cleanup could not be recovered"
                )
                return
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            status("recovering", error_type=type(error).__name__)
        if attempts < max_attempts:
            sleep(30)
    status("needs_attention", reason="controller recovery budget exhausted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--existing-unit", default="language-benchmark-user-capacity")
    args = parser.parse_args()
    supervise(args.config, args.output, args.state, args.existing_unit)


if __name__ == "__main__":
    main()
