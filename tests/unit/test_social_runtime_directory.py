"""The engine provisions runtime receipts before the immutable image starts."""

import pytest

from tools.bench.engine import trial
from tools.bench.engine.types import Target, TargetMount


def test_social_runtime_directory_is_bounded_writable_tmpfs_without_entrypoint_override():
    arguments = trial.social_runtime_arguments(Target(image="immutable-image"), "trial-123")
    assert arguments == [
        "--tmpfs",
        "/tmp/bench-runtime:rw,noexec,nosuid,nodev,size=1m,mode=1777",
        "-e",
        "RUN_ID=trial-123",
        "-e",
        "RUN_DIR=/tmp/bench-runtime",
    ]
    assert "--entrypoint" not in arguments


@pytest.mark.parametrize(
    "destination",
    [
        "/tmp",
        "/tmp/bench-runtime",
        "/tmp/bench-runtime/receipt.json",
        "//tmp",
        "//tmp/bench-runtime",
        "//tmp/bench-runtime/receipt.json",
    ],
)
def test_social_runtime_directory_rejects_overlapping_user_mounts(destination):
    target = Target(
        image="immutable-image",
        mounts=[
            TargetMount(source="/certificates/ca.pem", destination=destination, read_only=True)
        ],
    )
    with pytest.raises(ValueError, match="reserved social runtime directory"):
        trial.social_runtime_arguments(target, "trial-123")


def test_social_runtime_directory_allows_unrelated_tls_mount():
    target = Target(
        image="immutable-image",
        mounts=[
            TargetMount(source="/certificates/ca.pem", destination="/certs/ca.pem", read_only=True)
        ],
    )
    assert "--tmpfs" in trial.social_runtime_arguments(target, "trial-123")
