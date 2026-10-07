"""Receipts shared by trial orchestration and ownership recovery."""

import json
from dataclasses import dataclass
from pathlib import Path

from ..records import checked_record
from .records import Manifest
from .targets import ResolvedTarget
from .types import Environment, ResolvedJob
from .workloads import PreparedWorkload


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
        # Reconciliation never resolves images, builds or re-reads mutable sources.
        return cls(
            ResolvedJob.model_validate_json(json.dumps(data["job"])),
            data["users"],
            path.parent,
            data["queue_id"],
            data["trial_id"],
        )


@dataclass(frozen=True)
class CleanupReceipt:
    verified: bool
    removed: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
