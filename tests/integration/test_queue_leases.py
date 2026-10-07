import os
import subprocess

import pytest

from tools.bench.engine.types import Host
from tools.bench.queue.leases import ControllerLock, HostLease
from tools.bench.queue.store import QueueConflict


def test_controller_lock(tmp_path):
    with ControllerLock(tmp_path / "control.lock"):
        with pytest.raises(QueueConflict):
            with ControllerLock(tmp_path / "control.lock"):
                pass
    with ControllerLock(tmp_path / "control.lock"):
        pass


@pytest.mark.skipif(os.environ.get("BENCH_INTEGRATION") != "1", reason="requires local Docker")
def test_actual_docker_alias_contention_and_recovery(tmp_path):
    image = subprocess.run(
        ["docker", "image", "inspect", "grafana/k6:1.4.2", "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    owner = HostLease(Host(transport="local"), "queue-a", "owner-a")
    alias = HostLease(Host(transport="local", host="other-local-name"), "queue-b", "owner-b")
    assert owner.identity() == alias.identity()
    receipt = owner.acquire(image)
    try:
        with pytest.raises(QueueConflict):
            alias.acquire(image)
        with pytest.raises(QueueConflict):
            owner.release(receipt, cleanup_verified=False)
        with ControllerLock(tmp_path / "control.lock") as lock:
            with pytest.raises(QueueConflict):
                owner.restore(receipt, controller_lock=lock, cleanup_verified=False)
            assert owner.restore(receipt, controller_lock=lock, cleanup_verified=True) == receipt
    finally:
        owner.release(receipt, cleanup_verified=True)


@pytest.mark.skipif(os.environ.get("BENCH_INTEGRATION") != "1", reason="requires local Docker")
def test_queue_reservation_composes_with_trial_guard():
    from tools.bench.engine.trial import OwnedDocker

    image = subprocess.run(
        ["docker", "image", "inspect", "grafana/k6:1.4.2", "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    host = Host(transport="local")
    reservation = HostLease(host, "compose-test", "controller")
    receipt = reservation.acquire(image)
    trial = OwnedDocker(
        host, {"bench.queue": "compose-test", "bench.job": "job", "bench.trial": "trial"}
    )
    trial_info = None
    try:
        trial_info = trial.create("bench-engine-lease", [image])
        foreign = OwnedDocker(
            host, {"bench.queue": "foreign", "bench.job": "job", "bench.trial": "trial"}
        )
        with pytest.raises(ValueError):
            foreign.create("bench-engine-lease", [image])
        assert reservation.docker.inspect(receipt.container_id)["Id"] == receipt.container_id
    finally:
        if trial_info:
            trial.remove(trial_info["Id"])
        reservation.release(receipt, cleanup_verified=True)
