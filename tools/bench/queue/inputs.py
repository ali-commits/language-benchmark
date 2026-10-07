"""Bind preparation's retained copies to the inventory frozen at enqueue time."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from tools.bench.engine.records import FixturePin
from tools.bench.engine.targets import BuildSnapshot, tree_checksums
from tools.bench.engine.trial_context import PreparedFixture
from tools.bench.engine.types import Build, Workload
from tools.bench.engine.workloads import PreparedWorkload
from tools.bench.records import FixtureRecord, checked_record

from .records import BuildInput, FrozenInputs, WorkloadPin
from .store import QueueConflict, _file_identity


def check_file(source: Path, retained: Path, expected: FrozenInputs) -> None:
    """Read retained bytes, never assume that a live pre-copy check still holds."""
    key = str(source.resolve())
    if key not in expected or _file_identity(retained) != expected[key]:
        raise QueueConflict("frozen input changed; explicitly update pending job")


def check_workload(spec: Workload, prepared: PreparedWorkload, expected: FrozenInputs) -> None:
    root = spec.script.resolve().parent
    for source in (spec.script, *spec.dependencies):
        relative = source.resolve().relative_to(root)
        check_file(source, prepared.directory / relative, expected)


def check_build_policies(build: Build, expected: FrozenInputs) -> BuildInput:
    retained = checked_record(expected["build:" + build.model_dump_json()], BuildInput)
    for path, identity in retained["ignore_policies"].items():
        if _file_identity(Path(path)) != identity:
            raise QueueConflict("frozen input Docker ignore policy changed")
    return retained


def check_build(build: Build, snapshot: BuildSnapshot, expected: FrozenInputs) -> None:
    check_build_policies(build, expected)
    check_retained_build(build, snapshot, expected)


def check_retained_build(build: Build, snapshot: BuildSnapshot, expected: FrozenInputs) -> None:
    retained = checked_record(expected["build:" + build.model_dump_json()], BuildInput)
    if snapshot.identity != retained["identity"]:
        raise QueueConflict("frozen input build snapshot changed")


def freeze_fixture(spec: Workload, directory: Path, expected: FrozenInputs) -> FixturePin | None:
    fixture = spec.fixture
    if fixture is None or fixture.manifest is None:
        return None
    directory.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete copy atomically. A crash before saving its checkpoint
    # can reuse this copy only after rechecking the original frozen manifest.
    with tempfile.TemporaryDirectory(prefix="bench-fixture-", dir=directory.parent) as name:
        temporary = Path(name) / "fixture"
        root = directory if directory.exists() else temporary
        if root == temporary:
            root.mkdir()
            shutil.copy2(fixture.manifest, root / fixture.manifest.name)
        manifest = root / fixture.manifest.name
        check_file(fixture.manifest, manifest, expected)
        if fixture.adapter == "social":
            document = checked_record(json.loads(manifest.read_text()), FixtureRecord)
            source = fixture.manifest.parent / document["dump"]
            if source.resolve().parent != fixture.manifest.parent.resolve():
                raise QueueConflict("fixture archive escaped manifest directory")
            retained = root / document["dump"]
            if retained.resolve().parent != root.resolve() or retained == manifest:
                raise QueueConflict("fixture archive has invalid name")
            if root == temporary:
                shutil.copy2(source, retained)
            if hashlib.sha256(retained.read_bytes()).hexdigest() != document["checksum"]:
                raise QueueConflict("frozen input fixture archive changed")
        checksums = tree_checksums(root)
        if root == temporary:
            os.rename(root, directory)
    return {"manifest": str(directory / fixture.manifest.name), "checksums": checksums}


def prepared_fixture(workload: WorkloadPin) -> PreparedFixture | None:
    pin = workload.get("fixture")
    if pin is None:
        return None
    return PreparedFixture(Path(pin["manifest"]), pin["checksums"])
