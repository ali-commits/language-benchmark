"""Receipts shared by trial orchestration and ownership recovery."""

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from ..records import checked_record
from .config import legacy_search_snapshot
from .environment_binding import BoundEnvironment, bind_environment, reference_names
from .records import Manifest
from .targets import ResolvedTarget, tree_checksums
from .types import Environment, ResolvedJob, Workload
from .workloads import PreparedWorkload


@dataclass(frozen=True)
class PreparedFixture:
    manifest: Path
    checksums: dict[str, str]

    def verify(self) -> Path:
        if tree_checksums(self.manifest.parent) != self.checksums:
            raise ValueError("prepared fixture checksum mismatch")
        return self.manifest


@dataclass(frozen=True)
class TrialContext:
    job: ResolvedJob
    users: int | None
    directory: Path
    queue_id: str
    trial_id: str
    prepared_target: ResolvedTarget | None = None
    prepared_workload: PreparedWorkload | None = None
    generator_image_id: str | None = None
    prepared_fixture: PreparedFixture | None = None

    bound_environment: BoundEnvironment | None = None

    @property
    def fixture_manifest(self) -> Path | None:
        if self.prepared_fixture is not None:
            return self.prepared_fixture.verify()
        fixture = self.job.workload.fixture
        return fixture.manifest if fixture else None

    @property
    def fixture_workload(self) -> Workload:
        """Actual fixture IO uses the private pin; logical job/identity stays unchanged."""
        fixture = self.job.workload.fixture
        if self.prepared_fixture is None or fixture is None:
            return self.job.workload
        return self.job.workload.model_copy(
            update={
                "fixture": fixture.model_copy(update={"manifest": self.prepared_fixture.verify()})
            }
        )

    @property
    def environment(self) -> Environment:
        return self.job.environment

    @property
    def job_id(self) -> str:
        return self.job.id

    @classmethod
    def from_manifest(cls, path: Path | str) -> "TrialContext":
        path = Path(path)
        data = checked_record(json.loads(path.read_text()), Manifest)
        pin = data.get("prepared_fixture")
        prepared_fixture = PreparedFixture(Path(pin["manifest"]), pin["checksums"]) if pin else None
        job = ResolvedJob.model_validate_json(
            json.dumps(legacy_search_snapshot(checked_record(data["job"], dict[str, JsonValue])))
        )
        bound = None
        if reference_names(job):
            if not data.get("environment_binding") or not data.get("environment_key"):
                raise ValueError("legacy trial lacks environment binding; requires attention")
            bound = bind_environment(
                job, Path(data["environment_key"]), expected=data["environment_binding"]
            )
        # Reconciliation never resolves images, builds or re-reads mutable sources.
        return cls(
            job,
            data["users"],
            path.parent,
            data["queue_id"],
            data["trial_id"],
            prepared_fixture=prepared_fixture,
            bound_environment=bound,
        )


@dataclass(frozen=True)
class CleanupReceipt:
    verified: bool
    removed: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
