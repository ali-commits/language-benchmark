"""Local or SSH subprocess transport. Environment secrets travel via encrypted stdin."""

from collections.abc import Mapping
from typing import Protocol

import json
import os
import shlex
import subprocess
from pathlib import Path

REMOTE = "import sys,json,subprocess,os; d=json.load(sys.stdin); r=subprocess.run(d['argv'],cwd=d['cwd'],env={**os.environ,**d['env']}); sys.exit(r.returncode)"


class CommandFailure(RuntimeError):
    def __init__(self, result: subprocess.CompletedProcess[str]) -> None:
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr
        super().__init__(f"operation failed (exit {result.returncode}): " + result.stderr[-4000:])


def execute(
    host: Mapping[str, str], argv: list[str], env: Mapping[str, object] | None = None, timeout: float = 1800
) -> subprocess.CompletedProcess[str]:
    cwd = host.get("project", str(Path.cwd()))
    supplied = {str(k): str(v) for k, v in (env or {}).items()}
    if host.get("ssh"):
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", host["ssh"], "python3 -c " + shlex.quote(REMOTE)],
            input=json.dumps({"argv": argv, "env": supplied, "cwd": cwd}),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    else:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env={**os.environ, **supplied},
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    if result.returncode:
        raise CommandFailure(result)
    return result


def copy_to(host: Mapping[str, str], source: Path, destination: str) -> None:
    if host.get("ssh"):
        # Paths are restricted to absolute alphanumeric project artifact paths for scp.
        import re

        if not re.fullmatch(r"/[\w/.-]+", destination):
            raise ValueError("unsafe remote transfer path")
        subprocess.run(
            ["scp", "-q", str(source), host["ssh"] + ":" + destination], check=True, timeout=120
        )
    else:
        import shutil

        dest = Path(destination)
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)


def copy_from(host: Mapping[str, str], source: str, destination: Path) -> None:
    if host.get("ssh"):
        subprocess.run(
            ["scp", "-q", "-r", host["ssh"] + ":" + source, str(destination)],
            check=True,
            timeout=600,
        )
    else:
        import shutil

        if Path(source).resolve() != destination.resolve():
            shutil.copytree(source, destination, dirs_exist_ok=True)


class ExecuteCommand(Protocol):
    """Injectable transport; callers preserve environment and timeout semantics."""

    def __call__(
        self, host: Mapping[str, str], argv: list[str],
        env: Mapping[str, object] | None = None, timeout: float = 1800,
    ) -> subprocess.CompletedProcess[str]: ...
