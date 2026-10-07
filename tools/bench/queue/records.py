"""Version-one SQLite rows and mutable controller checkpoints.

Checked reads retain the original mappings (including extension metadata), so
mutating a nested checkpoint remains visible to the controller's atomic save.
"""

from typing import Literal

from pydantic import JsonValue
from typing_extensions import NotRequired, TypedDict

from tools.bench.engine.records import FixturePin, MountSource


class QueueRow(TypedDict):
    id: str
    revision: int
    config: str
    state: str


class JobRow(TypedDict):
    queue: str
    id: str
    position: int
    revision: int
    state: str
    definition: str
    resolved: str
    inputs: str
    owner: str | None


class TrialRow(TypedDict):
    queue: str
    job: str
    id: str
    path: str
    state: str


class EventRow(TypedDict):
    sequence: int
    queue: str
    job: str | None
    kind: str
    revision: int
    payload: str


class RequestRow(TypedDict):
    sequence: int
    queue: str
    after_current: int


class QueueSnapshot(TypedDict):
    queues: list[QueueRow]
    jobs: list[JobRow]
    events: list[EventRow]
    trials: list[TrialRow]
    requests: list[RequestRow]


class BuildInput(TypedDict):
    identity: str
    ignore_policies: dict[str, str | None]


FrozenInputs = dict[str, str | None | dict[str, str] | BuildInput]


class TargetPin(TypedDict):
    mount_sources: NotRequired[list[MountSource]]
    image: str
    requested_image: str | None
    repo_digests: list[str]
    build: str | None


class WorkloadPin(TypedDict):
    fixture: NotRequired[FixturePin]
    directory: str
    script: str
    checksums: dict[str, str]
    identity: str


class PreparedPin(TargetPin):
    workload: WorkloadPin
    generator: str
    execution_identity: NotRequired[str]


class PendingTrial(TypedDict):
    id: str
    users: int | None
    path: str
    identity: str
    objective: str
    recovery_attempts: NotRequired[int]
    interrupted: NotRequired[bool]
    evidence: NotRequired[dict[str, JsonValue]]


class CriterionRecord(TypedDict):
    metric: str
    scope: str
    statistic: str
    operator: str
    value: float
    unit: str | None
    stages: list[str] | None
    minimum_samples: int
    minimum_coverage: float


class CriterionResultRecord(TypedDict):
    criterion: CriterionRecord
    valid: bool
    passed: bool
    value: float | None
    sample_count: int
    coverage: float | None
    reason: str | None


class EvaluationRecord(TypedDict):
    verdict: Literal["pass", "fail", "invalid"]
    reasons: list[str]
    results: list[CriterionResultRecord]
    warnings: list[str]


class ObservationRecord(TypedDict):
    users: int
    trial_id: str
    evaluation: EvaluationRecord
    reused: bool


class SearchPolicyRecord(TypedDict):
    min_users: int
    start: int
    max_users: int | None
    max_trials: int
    max_elapsed: float
    expansion_factor: float
    resolution: int
    confirmation: str
    limiting_reason: str


class Decision(TypedDict):
    trial_id: str
    target: int
    objective: str
    evaluation: EvaluationRecord
    reused: NotRequired[bool]
    source_objective: NotRequired[str]


class RawTrial(TypedDict):
    target: int
    objective: str
    path: str
    identity: NotRequired[str]


class JobResult(TypedDict):
    target: int
    objective: str
    observed_only: bool
    status: str
    passing_users: int | None
    failing_users: int | None
    reason: str | None
    contradictions: list[list[int]]
    observations: list[ObservationRecord]
    attempts: int
    confirmation: str


class Budget(TypedDict):
    attempts: int
    clock: str
    started_at: float
    now: float


class SearchCheckpoint(TypedDict):
    policy: SearchPolicyRecord
    budget: Budget
    observations: list[ObservationRecord]
    pending_trial_id: str | None
    pending_users: int | None


class JobCheckpoint(TypedDict):
    job_id: str
    directory: str
    target: int
    cursor: int
    objective: int
    prepared: dict[str, PreparedPin]
    workloads: dict[str, WorkloadPin]
    preparation_attempts: dict[str, int]
    pending: PendingTrial | None
    search: SearchCheckpoint | None
    budget: Budget
    decisions: list[Decision]
    results: list[JobResult]
    fixed_attempts: dict[str, int]
    environment_bindings: NotRequired[dict[str, str]]
    target_pins: NotRequired[dict[str, TargetPin]]
    invalid_retries: NotRequired[dict[str, int]]
    interruptions: NotRequired[dict[str, int]]
    raw_trials: NotRequired[dict[str, RawTrial]]


class LeaseRecord(TypedDict):
    host_identity: str
    container_id: str
    queue_id: str
    owner: str


class RetainedLease(TypedDict):
    host: dict[str, JsonValue]
    receipt: LeaseRecord
    releasing: bool


class ControllerState(TypedDict):
    owner: str
    jobs: dict[str, JobCheckpoint]
    leases: dict[str, RetainedLease]
    last_error: str | None


class PublicJob(TypedDict):
    queue_id: str
    job_id: str
    revision: int
    state: str


class PublicStatus(TypedDict):
    queue: str
    state: str
    revision: int
    jobs: list[PublicJob]
    trials: list[TrialRow]
    stop_requested: bool | None
    last_error: str | None
