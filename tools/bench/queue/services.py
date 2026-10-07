"""Opt-in user-systemd adapter; no sudo, daemon dependency or secret files."""

import hashlib
import os
import shutil
import signal
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from .controller import run_queue
from .store import QueueStore, require_local_storage


class ServiceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceReceipt:
    queue_id: str
    unit_name: str
    unit_path: Path
    state_dir: Path
    reboot_persistent: bool


def _command(argv: Sequence[str]) -> str:
    try:
        return subprocess.run(
            argv, check=True, capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServiceUnavailable(
            "user-systemd command failed; check the user session and service support"
        ) from exc


def _unit_name(queue: str, state_dir: Path) -> str:
    token = hashlib.sha256((str(state_dir.resolve()) + "\0" + queue).encode()).hexdigest()[:24]
    return "bench-queue-" + token + ".service"


def _quote(value: str | Path, *, expand_environment: bool = True) -> str:
    value = str(value)
    if any(c in value for c in "\n\r\0"):
        raise ValueError("service arguments must be single-line")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if expand_environment:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def install_service(
    queue: str, state_dir: Path, *, environment_file: Path | None = None
) -> ServiceReceipt:
    """Install and enable (but do not start) an explicitly requested user unit.

    The executable is the current installed Python environment. Required secret
    refs for reboot use must be supplied in an explicit owned 0600 systemd
    EnvironmentFile. The caller shell environment is not copied or imported.
    Only the file path enters the unit; the adapter never reads its values. Call start_service after registering the queue.
    """
    state_dir = Path(state_dir).resolve()
    require_local_storage(state_dir)
    if not shutil.which("systemctl") or not shutil.which("loginctl"):
        raise ServiceUnavailable(
            "user-systemd support is unavailable; run the queue in the foreground"
        )
    _command(["systemctl", "--user", "show-environment"])
    if (
        _command(["loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"])
        != "yes"
    ):
        raise ServiceUnavailable(
            "user lingering is disabled; reboot persistence is unavailable; use foreground mode or arrange lingering first"
        )
    private_environment = None
    if environment_file is not None:
        supplied = Path(environment_file).absolute()
        info = supplied.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ServiceUnavailable(
                "environment file must be an owned regular file with mode 0600"
            )
        private_environment = supplied.resolve()
    name = _unit_name(queue, state_dir)
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).resolve()
    directory = config / "systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = directory / name
    argv = [
        str(Path(sys.executable).absolute()),
        "-m",
        "tools.bench.queue.services",
        "run",
        queue,
        str(state_dir),
    ]
    unit = "\n".join(
        [
            "[Unit]",
            "Description=Benchmark queue controller",
            "After=default.target",
            "StartLimitIntervalSec=300",
            "StartLimitBurst=3",
            "[Service]",
            "Type=simple",
            "ExecStart=" + " ".join(_quote(a) for a in argv),
            "WorkingDirectory=" + str(Path(__file__).resolve().parents[3]).replace("%", "%%"),
            *(
                ["EnvironmentFile=" + _quote(private_environment, expand_environment=False)]
                if private_environment
                else []
            ),
            "Restart=on-failure",
            "RestartSec=5",
            "TimeoutStopSec=300",
            "KillSignal=SIGTERM",
            "UMask=0077",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )
    temporary = path.with_suffix(".tmp")
    temporary.write_text(unit)
    temporary.chmod(0o600)
    os.replace(temporary, path)
    _command(["systemctl", "--user", "daemon-reload"])
    _command(["systemctl", "--user", "enable", name])
    return ServiceReceipt(queue, name, path, state_dir, True)


def start_service(queue: str, state_dir: Path) -> None:
    """Start the installed unit; stop requests must be cleared explicitly."""
    _command(["systemctl", "--user", "start", _unit_name(queue, Path(state_dir))])


def stop_service(queue: str, state_dir: Path, *, after_current: bool = False) -> None:
    """Persist deliberate stop and disable reboot activation without killing load."""
    QueueStore(Path(state_dir) / "queue.db").request_stop(queue, after_current=after_current)
    _command(["systemctl", "--user", "disable", _unit_name(queue, Path(state_dir))])


def resume_service(
    queue: str, state_dir: Path, *, environment_file: Path | None = None
) -> ServiceReceipt:
    state_dir = Path(state_dir).resolve()
    name = _unit_name(queue, state_dir)
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).resolve()
    path = config / "systemd/user" / name
    if path.exists() and environment_file is None:
        if (
            _command(["loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"])
            != "yes"
        ):
            raise ServiceUnavailable(
                "user lingering is disabled; reboot persistence is unavailable"
            )
        _command(["systemctl", "--user", "enable", name])
        receipt = ServiceReceipt(queue, name, path, state_dir, True)
    else:
        receipt = install_service(queue, state_dir, environment_file=environment_file)
    QueueStore(receipt.state_dir / "queue.db").clear_stop(queue)
    start_service(queue, receipt.state_dir)
    return receipt


def _run(queue: str, state_dir: Path) -> int:
    store = QueueStore(Path(state_dir) / "queue.db")

    interrupted = False

    def stop(signum: int, frame: FrameType | None) -> None:
        # SIGTERM is also systemd's normal shutdown signal. Process interruption
        # must not create the durable user-stop latch or disable reboot resume.
        nonlocal interrupted
        interrupted = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        result = run_queue(store, queue, interrupt_requested=lambda: interrupted)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    deliberate_stop = store.stop_request(queue) is not None
    terminal = result["queues"][0]["state"] in {"needs_attention", "completed"}
    if (deliberate_stop or terminal) and shutil.which("systemctl"):
        try:
            _command(["systemctl", "--user", "disable", _unit_name(queue, Path(state_dir))])
        except ServiceUnavailable:
            pass
    # needs_attention is durable and requires explicit resume; no restart loop.
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "run":
        raise SystemExit("usage: python -m tools.bench.queue.services run QUEUE STATE_DIR")
    raise SystemExit(_run(sys.argv[2], Path(sys.argv[3])))
