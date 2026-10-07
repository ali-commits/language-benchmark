"""Process-held controller lock and Docker-daemon-wide queue reservations.

Reservations are independent of the lifecycle's per-trial bench-engine-lease.
They are never reclaimed on a timer. Recovery requires the persisted exact owner
and immutable container ID, a held controller lock, and verified trial cleanup.
"""

import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from tools.bench.engine.records import ContainerInfo
from tools.bench.engine.targets import HostExecutor, execute_host
from tools.bench.engine.trial import OwnedDocker
from tools.bench.engine.types import Host

from .store import QueueConflict, require_local_storage


class ControllerLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self._file: TextIO | None = None

    @property
    def held(self) -> bool:
        return self._file is not None

    def __enter__(self) -> "ControllerLock":
        if self.held:
            raise QueueConflict("controller lock already held")
        require_local_storage(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise QueueConflict("another controller owns this state directory") from exc
        self._file = handle
        return self

    def __exit__(self, *args: object) -> None:
        if self._file:
            fcntl.flock(self._file, fcntl.LOCK_UN)
            self._file.close()
            self._file = None


@dataclass(frozen=True)
class LeaseReceipt:
    host_identity: str
    container_id: str
    queue_id: str
    owner: str


class HostLease:
    name = "bench-queue-lease"

    def __init__(
        self, host: Host, queue_id: str, owner: str, *, execute: HostExecutor | None = None
    ) -> None:
        if not owner or not queue_id:
            raise QueueConflict("lease owner required")
        self.host, self.queue_id, self.owner = host, queue_id, owner
        self.execute = execute or execute_host
        self.docker = OwnedDocker(
            host,
            {"bench.queue": queue_id, "bench.controller": owner, "bench.role": "queue-lease"},
            execute=self.execute,
        )

    def identity(self) -> str:
        identity = self.execute(
            self.host, ["docker", "info", "--format", "{{.ID}}"], timeout=15
        ).stdout.strip()
        if not identity:
            raise QueueConflict("Docker host identity missing")
        return identity

    def acquire(self, image_id: str) -> LeaseReceipt:
        """Use an already pinned local image; never pulls/builds an image."""
        identity = self.identity()
        try:
            info = self.docker.create(self.name, [image_id])
        except Exception as exc:
            raise QueueConflict("host reservation exists or cannot be verified") from exc
        return LeaseReceipt(identity, info["Id"], self.queue_id, self.owner)

    def restore(
        self, receipt: LeaseReceipt, *, controller_lock: ControllerLock, cleanup_verified: bool
    ) -> LeaseReceipt:
        """Reattach the same reservation after explicit controller/trial recovery.

        Caller must reconcile every persisted unfinished trial before setting
        cleanup_verified. A missing reservation fails closed, never recreates it.
        """
        if not controller_lock.held or not cleanup_verified:
            raise QueueConflict("recovery requires controller lock and verified cleanup")
        self._verify(receipt)
        return receipt

    def _verify(self, receipt: LeaseReceipt) -> ContainerInfo:
        if (receipt.queue_id, receipt.owner) != (
            self.queue_id,
            self.owner,
        ) or receipt.host_identity != self.identity():
            raise QueueConflict("reservation owner/host mismatch")
        info = self.docker.inspect(receipt.container_id)
        if info["Name"] != "/" + self.name:
            raise QueueConflict("reservation name mismatch")
        return info

    def release(self, receipt: LeaseReceipt, *, cleanup_verified: bool) -> None:
        if not cleanup_verified:
            raise QueueConflict("host remains reserved until cleanup is verified")
        self._verify(receipt)
        try:
            self.docker.remove(receipt.container_id)
        except Exception:
            if self.docker.exists(receipt.container_id):
                raise
        if self.docker.exists(receipt.container_id):
            raise QueueConflict("reservation removal unverified")
