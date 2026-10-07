"""Monitor-host controller supervision with persisted, bounded recovery attempts."""

import argparse
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar, overload

from typing_extensions import TypeForm

from .control import recover_hosts
from .manifest import atomic_json
from .records import Campaign, ControllerStatus, checked_record

COMPLETE = {"complete", "complete_with_unstable_boundaries"}


T = TypeVar("T")


@overload
def read_status(path: Path) -> dict[str, object]: ...


@overload
def read_status(path: Path, schema: TypeForm[T]) -> T: ...


def read_status(path: Path, schema: TypeForm[T] | None = None) -> T | dict[str, object]:
    value = json.loads(path.read_text()) if path.exists() else {}
    return (
        checked_record(value, schema)
        if schema is not None
        else checked_record(value, dict[str, object])
    )


def controller_active(unit: str) -> bool:
    return (
        subprocess.run(["systemctl", "is-active", "--quiet", unit], capture_output=True).returncode
        == 0
    )


def supervise(
    config_path: Path,
    output: Path,
    state_path: Path,
    existing_unit: str,
    max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    config = checked_record(json.loads(config_path.read_text()), Campaign)
    if not 1 <= max_attempts <= 3:
        raise ValueError("supervisor recovery budget must be between one and three")
    previous = read_status(state_path, ControllerStatus)
    attempts = previous.get("controller_attempts", 0)

    def status(value: str | None, **details: object) -> None:
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
    current = read_status(campaign_status, ControllerStatus).get("status")
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
            current = read_status(campaign_status, ControllerStatus).get("status")
            if current in COMPLETE:
                status(current)
                return
            if (
                current == "needs_attention"
                and read_status(campaign_status, ControllerStatus).get("updated_epoch", 0)
                >= started
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--existing-unit", default="language-benchmark-user-capacity")
    args = parser.parse_args()
    supervise(args.config, args.output, args.state, args.existing_unit)


if __name__ == "__main__":
    main()
