"""Ownership-checked Docker operations over a captured host transport."""

import json
import subprocess

from ..records import checked_record
from .docker_env import CREATE_WITH_ENV, validate_container_environment
from .records import ContainerInfo, NetworkInfo
from .targets import HostExecutor, execute_host
from .types import Host


class OwnedDocker:
    def __init__(
        self, host: Host, labels: dict[str, str], execute: HostExecutor = execute_host
    ) -> None:
        self.host, self.labels = host, labels
        self.execute = execute

    def command(
        self,
        *argv: str,
        env: dict[str, str] | None = None,
        timeout: float = 1800,
        input_data: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.execute(
            self.host, ["docker", *argv], env=env, timeout=timeout, input_data=input_data
        )

    def inspect(self, identity: str) -> ContainerInfo:
        info = checked_record(
            json.loads(self.command("inspect", identity).stdout), list[ContainerInfo]
        )[0]
        if any(
            (info.get("Config", {}).get("Labels") or {}).get(k) != v for k, v in self.labels.items()
        ):
            raise ValueError("resource ownership mismatch")
        return info

    def by_name(self, name: str) -> ContainerInfo | None:
        ids = self.command("ps", "-aq", "--no-trunc", "--filter", f"name=^/{name}$").stdout.split()
        return self.inspect(ids[0]) if ids else None

    def create(
        self, name: str, arguments: list[str], *, env: dict[str, str] | None = None
    ) -> ContainerInfo:
        validate_container_environment(env or {})
        if self.by_name(name):
            raise ValueError("existing resource requires explicit reconciliation")
        flags = [flag for k, v in self.labels.items() for flag in ("--label", k + "=" + v)]
        try:
            if env:
                result = self.execute(
                    self.host,
                    ["python3", "-c", CREATE_WITH_ENV, "--name", name, *flags, *arguments],
                    input_data=json.dumps({"environment": env, "timeout": 1800}),
                )
            else:
                result = self.command("create", "--name", name, *flags, *arguments)
            identity = result.stdout.strip()
        except Exception:
            # A successful remote create may lose its SSH response. Never issue
            # a duplicate create until actual owner-labelled state is known.
            found = self.by_name(name)
            if found:
                return found
            raise
        return self.inspect(identity)

    def start(self, identity: str) -> ContainerInfo:
        self.inspect(identity)
        try:
            self.command("start", identity)
        except Exception:
            info = self.inspect(identity)
            if info.get("State", {}).get("Running") or info.get("State", {}).get(
                "StartedAt", ""
            ).startswith(("20",)):
                return info
            raise
        return self.inspect(identity)

    def remove(self, identity: str) -> None:
        self.inspect(identity)
        self.command("rm", "-f", identity)

    def exists(self, identity: str) -> bool:
        # Query immutable ID without owner filters: a label change is not absence.
        return (
            identity
            in self.command("ps", "-aq", "--no-trunc", "--filter", "id=" + identity).stdout.split()
        )

    def owned(self) -> list[str]:
        args = [
            v for k, value in self.labels.items() for v in ("--filter", "label=" + k + "=" + value)
        ]
        return self.command("ps", "-aq", "--no-trunc", *args).stdout.split()

    def inspect_network(self, identity: str) -> NetworkInfo:
        info = checked_record(
            json.loads(self.command("network", "inspect", identity).stdout), list[NetworkInfo]
        )[0]
        if any((info.get("Labels") or {}).get(key) != value for key, value in self.labels.items()):
            raise ValueError("network ownership mismatch")
        return info

    def create_network(self, name: str) -> NetworkInfo:
        flags = [
            flag for key, value in self.labels.items() for flag in ("--label", key + "=" + value)
        ]
        try:
            identity = self.command("network", "create", *flags, name).stdout.strip()
        except Exception:
            return self.inspect_network(name)
        return self.inspect_network(identity)

    def remove_network(self, identity: str) -> None:
        self.inspect_network(identity)
        self.command("network", "rm", identity)

    def owned_networks(self) -> list[str]:
        flags = [
            flag
            for key, value in self.labels.items()
            for flag in ("--filter", "label=" + key + "=" + value)
        ]
        return self.command("network", "ls", "-q", *flags).stdout.split()
