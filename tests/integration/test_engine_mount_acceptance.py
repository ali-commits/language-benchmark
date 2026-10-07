"""Opt-in read-only bind smoke using an existing local image (never builds or pulls)."""

import os
from uuid import uuid4

import pytest

from tools.bench.engine.owned_docker import OwnedDocker
from tools.bench.engine.targets import mount_arguments, mount_source_receipts, verify_mounts
from tools.bench.engine.types import Host, Target, TargetMount


@pytest.mark.skipif(
    not os.environ.get("BENCH_MOUNT_SMOKE_IMAGE"), reason="requires existing Python image"
)
def test_real_readonly_mount_and_owned_cleanup(tmp_path):
    source = tmp_path / "CA with spaces.pem"
    source.write_text("smoke-certificate")
    target = Target(
        image=os.environ["BENCH_MOUNT_SMOKE_IMAGE"],
        mounts=[TargetMount(source=str(source), destination="/certs/rds-ca.pem", read_only=True)],
    )
    host = Host(transport="local")
    receipt = mount_source_receipts(target, host)
    docker = OwnedDocker(host, {"bench.queue": "mount-smoke-" + uuid4().hex})
    assert target.image is not None
    info = docker.create(
        "mount-smoke-" + uuid4().hex,
        [
            *mount_arguments(target),
            "--entrypoint",
            "python",
            target.image,
            "-c",
            "from pathlib import Path; import errno; p=Path('/certs/rds-ca.pem'); assert p.read_text()=='smoke-certificate';\ntry: p.write_text('bad')\nexcept OSError as e: assert e.errno==errno.EROFS\nelse: raise AssertionError('writable mount')",
        ],
    )
    try:
        verify_mounts(target, info)
        docker.start(info["Id"])
        assert docker.command("wait", info["Id"]).stdout.strip() == "0"
    finally:
        docker.remove(info["Id"])
    assert source.read_text() == "smoke-certificate"
    assert mount_source_receipts(target, host) == receipt
