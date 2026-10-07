"""Social fixture adapter: legacy guarded restore and runtime attestation."""

import json
import os
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from typing_extensions import TypedDict

from ..records import Credential, FixtureRecord, WorkerSnapshot, checked_record
from .records import Authentication, ContainerInfo, DockerImage
from .types import Sample, Workload

if TYPE_CHECKING:
    from .trial import TrialContext

from ..fixture import verify_fixture
from ..models import FixtureManifest, NativeCell
from ..native_runtime import load_runtime_cell, native_image_metadata, validate_process_nofile
from ..reset import guard, restore_fixture
from ..runtime import validate_dependency_inventory, validate_worker_snapshots
from .evidence import immutable_json
from .resources import dependency_lock_path, runtime_root
from .targets import execute_host


class SocialAdapter:
    def __init__(self, spec: Workload) -> None:
        self.spec = spec
        fixture = spec.fixture
        if (
            not fixture
            or fixture.adapter != "social"
            or not fixture.dsn
            or not fixture.manifest
            or not fixture.allowed_database
        ):
            raise ValueError(
                "social fixture requires adapter, dsn env reference, manifest and allowed_database"
            )
        if not fixture.runtime_cell:
            raise ValueError("social adapter requires locked runtime_cell")
        self.fixture = fixture
        self.manifest_path = fixture.manifest
        self.runtime_cell = fixture.runtime_cell
        self.dsn_ref = fixture.dsn
        self.allowed_database = fixture.allowed_database

    def _dsn(self) -> str:
        dsn = os.environ[self.dsn_ref.env]
        guard(dsn, self.allowed_database)
        return dsn

    def prepare(self, context: "TrialContext") -> None:
        self._dsn()
        manifest = FixtureManifest(
            **checked_record(json.loads(self.manifest_path.read_text()), FixtureRecord)
        )
        from .stages import resolve_stages

        stages = resolve_stages(context.job, context.users)
        peak = max(max(s.from_users, s.to_users) for s in stages)
        if manifest.counts.get("users", 0) < peak:
            raise ValueError("social fixture contains fewer accounts than target")
        load_runtime_cell(self.runtime_cell, runtime_root())

    def reset(self, context: "TrialContext") -> FixtureManifest:
        return restore_fixture(self._dsn(), self.manifest_path, self.allowed_database)

    def verify(self, context: "TrialContext") -> None:
        manifest = FixtureManifest(
            **checked_record(json.loads(self.manifest_path.read_text()), FixtureRecord)
        )
        if verify_fixture(self._dsn(), manifest):
            raise ValueError("social fixture fingerprints differ")

    def attest(self, context: "TrialContext", container_id: str) -> None:
        host = context.environment.backend
        cell = load_runtime_cell(self.runtime_cell, runtime_root())
        info = checked_record(
            json.loads(execute_host(host, ["docker", "inspect", container_id]).stdout),
            list[ContainerInfo],
        )[0]
        image = checked_record(
            json.loads(execute_host(host, ["docker", "image", "inspect", info["Image"]]).stdout),
            list[DockerImage],
        )[0]
        receipt: dict[str, object] = {"cell": cell.id, "image_id": image["Id"]}
        if isinstance(cell, NativeCell):
            receipt["runtime"] = native_image_metadata(
                {
                    "id": image["Id"],
                    "os": image["Os"],
                    "architecture": image["Architecture"],
                    "labels": image["Config"].get("Labels") or {},
                },
                cell,
                info["Image"],
            )
        else:
            cell = replace(
                cell,
                dependency_lock=str(dependency_lock_path(cell.dependency_lock)),
            )
            code = "import pathlib,json; print(json.dumps([json.loads(p.read_text()) for p in pathlib.Path('/tmp/bench-runtime').glob('*.json')]))"
            deadline = time.monotonic() + context.job.target.readiness.timeout
            snapshots: list[WorkerSnapshot] = []
            while time.monotonic() < deadline:
                snapshots = checked_record(
                    json.loads(
                        execute_host(
                            host, ["docker", "exec", container_id, "python", "-c", code]
                        ).stdout
                    ),
                    list[WorkerSnapshot],
                )
                if len(snapshots) == cell.workers:
                    break
                time.sleep(0.1)
            reasons = validate_worker_snapshots(cell, snapshots) + validate_dependency_inventory(
                cell, snapshots
            )
            if any(s.get("run_id") != context.trial_id for s in snapshots):
                reasons.append("runtime trial identity mismatch")
            if reasons:
                raise ValueError("social runtime attestation failed")
            receipt["workers"] = snapshots
        limits = execute_host(
            host,
            [
                "docker",
                "exec",
                container_id,
                "sh",
                "-c",
                'for path in /proc/[0-9]*/limits; do cat "$path" 2>/dev/null || true; done',
            ],
        ).stdout
        receipt["nofile"] = validate_process_nofile(limits)
        immutable_json(context.directory / "social-runtime.json", receipt)

    def authentication(self, samples: Sequence[Sample], target: int) -> Authentication:
        accounts = set()
        for sample in samples:
            if sample.metric == "authenticated_account" and sample.value == 1:
                account = sample.provider_fields.get("account")
                if isinstance(account, str) and account:
                    accounts.add(account.lower())
        return {
            "distinct_accounts": len(accounts),
            "target_accounts": target,
            "fraction": len(accounts) / target if target else None,
        }


def social_adapter(spec: Workload) -> SocialAdapter:
    return SocialAdapter(spec)


def preflight_social(workload: Workload, required_users: int | None = None) -> "SocialPreflight":
    """Validate local inputs only: no database connection/reset or container start."""
    import hashlib

    adapter = SocialAdapter(workload)
    adapter._dsn()  # Pure DSN allowlist validation, no database IO.
    manifest_path = adapter.manifest_path
    try:
        manifest = FixtureManifest(
            **checked_record(json.loads(manifest_path.read_text()), FixtureRecord)
        )
        if not all(
            isinstance(value, str) and value
            for value in (
                manifest.schema_revision,
                manifest.identity,
                manifest.dump,
                manifest.checksum,
            )
        ):
            raise ValueError()
        if (
            not isinstance(manifest.counts, dict)
            or type(manifest.counts.get("users")) is not int
            or manifest.counts["users"] <= 0
        ):
            raise ValueError()
        if not isinstance(manifest.fingerprints, dict) or not manifest.fingerprints:
            raise ValueError()
        dump = manifest_path.parent / manifest.dump
        if dump.resolve().parent != manifest_path.parent.resolve():
            raise ValueError()
        if hashlib.sha256(dump.read_bytes()).hexdigest() != manifest.checksum:
            raise ValueError()
    except (OSError, TypeError, KeyError, ValueError):
        raise ValueError(
            "social fixture manifest/archive is missing, malformed or checksum-invalid"
        ) from None
    cell = load_runtime_cell(adapter.runtime_cell, runtime_root())
    actors_paths = [path for path in workload.dependencies if path.name == "actors.json"]
    if len(actors_paths) != 1:
        raise ValueError("social requires one declared actors.json dependency")
    try:
        actors = checked_record(json.loads(actors_paths[0].read_text()), ActorsDocument)["actors"]
        if not isinstance(actors, list) or not actors:
            raise ValueError()
        names = []
        for actor in actors:
            if (
                not isinstance(actor, dict)
                or not isinstance(actor.get("username"), str)
                or not actor["username"].strip()
                or not isinstance(actor.get("password"), str)
                or not actor["password"]
            ):
                raise ValueError()
            names.append(actor["username"].casefold())
    except (OSError, TypeError, KeyError, ValueError):
        raise ValueError("social actors must contain nonempty username/password records") from None
    if len(names) != len(set(names)):
        raise ValueError("social actor account names must be case-insensitively unique")
    capacity = min(len(names), manifest.counts["users"])
    if required_users is not None and required_users > capacity:
        raise ValueError(
            f"social account capacity {capacity} is below configured peak/ceiling {required_users}"
        )
    if (
        not isinstance(cell, NativeCell)
        and not dependency_lock_path(cell.dependency_lock).is_file()
    ):
        raise ValueError("social runtime dependency attestation lock is missing")
    return {
        "fixture_users": manifest.counts["users"],
        "actors": len(names),
        "account_capacity": capacity,
        "required_peak": required_users,
        "runtime_cell": cell.id,
    }


class ActorsDocument(TypedDict):
    actors: list[Credential]


class SocialPreflight(TypedDict):
    fixture_users: int
    actors: int
    account_capacity: int
    required_peak: int | None
    runtime_cell: str
