"""Concrete legacy wire records and checked external-data boundaries.

Records describe persisted version-one payloads without changing their keys. JSON
readers keep unknown keys for historical forward compatibility after checking the
known fields; operational guards remain in their original callers.
"""

from typing import TypeVar, cast

from pydantic import TypeAdapter
from typing_extensions import TypedDict

JSONValue = str | int | float | bool | None | list['JSONValue'] | dict[str, 'JSONValue']
T = TypeVar('T')


def checked_record(value: object, schema: type[T]) -> T:
    """Validate known wire fields strictly, preserving original extra metadata."""
    TypeAdapter(schema).validate_python(value, strict=True)
    # The schema checked above establishes the exact target record. Preserve the
    # original mapping because TypedDict adapters intentionally drop unknown keys.
    return cast(T, value)


class RuntimeCellRecord(TypedDict, total=False):
    id: str
    python: str
    app: str
    build: str
    adapter: str
    workers: int
    threads: int
    pool_budget: int
    jit: bool
    gil: bool
    supported: bool
    reasons: list[str]
    dependency_lock: str
    image: str


class NativeCellRecord(TypedDict, total=False):
    id: str
    language: str
    runtime_version: str
    framework: str
    framework_version: str
    image: str
    dockerfile: str
    workers: int
    connections_per_worker: int
    topology: str
    upstream: str
    pool_budget: int
    supported: bool
    reasons: list[str]


class PythonSource(TypedDict, total=False):
    version: str
    sha256: str
    url: str


class RuntimeLock(TypedDict, total=False):
    base: str
    runtimes: list[PythonSource]
    cells: list[RuntimeCellRecord]


class NativeLock(TypedDict, total=False):
    budget: dict[str, int]
    cells: list[NativeCellRecord]


class WorkerSnapshot(TypedDict, total=False):
    pid: int
    run_id: str
    config_args: str | None
    jit_available: bool
    python: str
    gil: bool
    free_threaded: bool
    jit: bool
    dependencies: dict[str, str]


class Nofile(TypedDict):
    soft: int
    hard: int
    process_samples: int


class Ulimit(TypedDict, total=False):
    Name: str
    Soft: int
    Hard: int


class Mount(TypedDict, total=False):
    Destination: str
    RW: bool


class NativeContainer(TypedDict, total=False):
    id: str
    image: str
    running: bool
    nano_cpus: int
    memory: int
    memory_swap: int
    network_mode: str
    run_id: str
    cell_id: str
    ulimits: list[Ulimit]
    mounts: list[Mount]
    nofile: Nofile


class NativeImage(TypedDict, total=False):
    id: str
    os: str
    architecture: str
    labels: dict[str, str | None]


class NativeAttestation(TypedDict):
    cell: NativeCellRecord
    container: NativeContainer
    image: NativeImage
    attestation: str


class FixtureRecord(TypedDict, total=False):
    schema_revision: str
    checksum: str
    dump: str
    identity: str
    counts: dict[str, int]
    fingerprints: dict[str, str]
    generator_version: str
    spec: dict[str, int | str]


class ConfirmationPolicy(TypedDict):
    passing_repeats: int
    failing_repeats: int
    stop_on_contradiction: bool


class StartingHint(TypedDict, total=False):
    start_users: int
    initial_failing_hint: int


class Handoff(TypedDict, total=False):
    reason: str
    measurement_identity_sha256: str
    previous_measurement_identity_sha256: str
    new_measurement_identity_sha256: str
    from_cells: list[str]
    to_cells: list[str]


# "from" is a Python keyword but remains the historical serialized key.
DecisionHandoff = TypedDict('DecisionHandoff', {'from': str, 'to': str, 'reason': str, 'measurement_identity_sha256': str}, total=False)
ResolutionHandoff = TypedDict('ResolutionHandoff', {'from': int, 'to': int, 'reason': str, 'measurement_identity_sha256': str}, total=False)


class BaselinePolicy(TypedDict, total=False):
    enabled: bool
    interval_seconds: int
    consecutive_samples: int
    timeout_seconds: int
    allowed_telemetry_containers: list[str]
    stopped_cpu_max_percent: float
    ready_cpu_max_percent: float
    stopped_mem_available_mib: float
    ready_mem_available_mib: float


class ExecutionPolicy(TypedDict, total=False):
    backend_baseline_gate: BaselinePolicy


class Campaign(TypedDict, total=False):
    mode: str
    smoke: bool
    cells: list[str]
    rates: list[int]
    max_rate: int
    vus: int
    repeats: int
    measure_seconds: int
    warmup_seconds: int
    seed: int
    database_allowlist: str
    profile: str
    experiment_type: str
    ramp_seconds: int
    settle_seconds: int
    hold_seconds: int
    backend_nofile_limit: int
    start_users: int
    step_users: int
    max_users: int
    resolution_users: int
    wait_min: int
    wait_max: int
    timeout_seconds: int
    success_fraction: float
    authentication_fraction: float
    confirmation_repeats: int
    confirmation_policy: ConfirmationPolicy
    fixed_users: list[int]
    boundary_decision_policy: str
    growth_strategy: str
    initial_failing_hint: int
    cell_starting_hints: dict[str, StartingHint]
    fixture: str
    credentials: str
    expected_image_ids: dict[str, str]
    backend_host: dict[str, str]
    load_host: dict[str, str]
    build_host: dict[str, str]
    auto_build_images: bool
    build_uv_path: str
    infrastructure_attempts: int
    recovery_delay_seconds: int
    rds_wait_seconds: int
    rds_identifier: str
    region: str
    base_url: str
    load_base_url: str
    backend_database_url: str
    bind_ip: str
    generator_local_ips: str
    prometheus_url: str
    operation: str
    campaign_instance: str
    execution_policy: ExecutionPolicy
    queue_extension_handoff: Handoff
    decision_policy_handoff: DecisionHandoff
    resolution_handoff: ResolutionHandoff
    classification_version: str


class Methodology(TypedDict, total=False):
    backend_nofile_limit: int
    experiment_type: str
    profile: str
    requested_profile: str
    configured_profile: str
    ramp_seconds: int
    settle_seconds: int
    hold_seconds: int
    configured_trial_seconds: int
    steady_duration_seconds: int
    success_fraction: float
    authentication_fraction: float
    timeout_seconds: int
    wait_min: int
    wait_max: int
    mode: str
    classification_version: str
    description: str


class Trial(TypedDict, total=False):
    id: str
    campaign_id: str
    cell: str
    rate: int
    repetition: int
    seed: int
    users: int


class Lifecycle(TypedDict, total=False):
    status: str
    reasons: list[str]
    reset_verified: bool
    requested_stop_signals: list[str]


class Latency(TypedDict, total=False):
    duration_samples: int
    mean: float
    p50: float
    p95: float
    p99: float
    p999: float


class EndpointMetrics(Latency, total=False):
    attempted: float
    succeeded: float
    http_requests: float
    timeouts: float
    network_errors: float
    completed_journeys: float
    durations: list[float]
    success_fraction: float | None
    attempted_operations_s: float
    successful_operations_s: float


class PhaseMetrics(EndpointMetrics, total=False):
    active_users_max: float
    started_epoch: float
    finished_epoch: float
    endpoints: dict[str, EndpointMetrics]


class Authentication(TypedDict, total=False):
    login_attempted: float
    login_succeeded: float
    login_success_fraction: float | None


class JourneyMetrics(PhaseMetrics, total=False):
    dropped: float
    authenticated_users: float
    generator_resource_errors: float
    ramp_attempted: float
    ramp_succeeded: float
    settle_attempted: float
    settle_succeeded: float
    scenario_started_epoch: float
    activity_started_epoch: float
    activity_finished_epoch: float | None
    steady_started_epoch: float
    steady_finished_epoch: float
    incomplete: bool
    phases: dict[str, PhaseMetrics]
    authentication: Authentication
    steady_seconds: float
    http_requests_s: float


class TrialResult(Lifecycle, total=False):
    trial: Trial
    label: str | None
    methodology: Methodology
    ramp_seconds: int
    settle_seconds: int
    hold_seconds: int
    execution_started_epoch: float
    execution_finished_epoch: float
    measurement_started_epoch: float
    measurement_finished_epoch: float
    scenario_started_epoch: float
    k6_exit_code: int
    journey_metrics: JourneyMetrics
    metrics: JourneyMetrics


class Outcome(TypedDict, total=False):
    outcome: str
    users: int
    lifecycle_status: str | None
    reasons: list[str]
    metrics: JourneyMetrics
    trial_id: str
    methodology: Methodology
    finalized_after_restart: bool
    reused_after_restart: bool
    campaign: str
    qualification: str
    artifact: str
    resources: Resources


class Vote(TypedDict):
    trial_id: str
    outcome: str


class Decision(TypedDict):
    outcome: str | None
    valid_vote_count: int
    votes: list[Vote]
    consensus_final: bool


class NonMonotonic(TypedDict):
    failing_below_passing: list[int]
    highest_passing: int


class CapacityResult(TypedDict, total=False):
    status: str
    passing_users: int
    failing_users: int | None
    observations: list[Outcome]
    resolution: int
    confirmation_policy: ConfirmationPolicy
    confirmations: dict[str, list[Outcome]]
    prior_endpoint_trials: dict[str, list[Outcome]]
    starting_users: int
    boundary_decision_policy: str
    decisions: dict[str, Decision]
    non_monotonic_counts: NonMonotonic
    experiment_type: str
    fixed_users: list[int]
    reasons: list[str]


class StreamStatus(TypedDict):
    incomplete: bool


class MetricData(TypedDict, total=False):
    value: float
    tags: dict[str, str]
    time: str


class MetricPoint(TypedDict, total=False):
    type: str
    metric: str
    data: MetricData


class Credential(TypedDict, total=False):
    username: str
    password: str


class ResourcePhase(TypedDict, total=False):
    samples: int
    cpu_percent_mean: float
    cpu_percent_peak: float
    memory_working_set_mib_mean: float
    memory_working_set_mib_peak: float


class Resources(TypedDict, total=False):
    allocated_cpus: float
    source: str
    limitation: str
    phases: dict[str, ResourcePhase]
    malformed_samples: int


class GeneratorResources(TypedDict, total=False):
    budget: str
    phases: dict[str, dict[str, float]]


class RdsMetricSummary(TypedDict):
    mean: float | None
    peak: float | None
    buckets: int


class RdsResources(TypedDict, total=False):
    budget: str
    limitation: str
    metrics: dict[str, RdsMetricSummary]
    instance: dict[str, object]


class ExternalResources(TypedDict, total=False):
    generator: GeneratorResources
    rds: RdsResources


class TrialSummary(TypedDict, total=False):
    id: str
    campaign: str
    cell: str
    rate: int | None
    target_users: int | None
    workload: str
    label: str | None
    status: str
    qualification: str
    reasons: list[str]
    metrics: JourneyMetrics
    metrics_source: str
    achieved_operations_s: float | None
    achieved_http_requests_s: float | None
    successful_operations_s: float | None
    authentication_fraction: float | None
    authentication_gate: float
    methodology: Methodology
    resources: Resources
    external_resources: ExternalResources
    evidence: dict[str, str]
    artifact: str


class CampaignSummary(TypedDict):
    trials: list[TrialSummary]
    groups: list[dict[str, object]]
    method: str


class TrialManifest(TypedDict, total=False):
    campaign: Campaign
    trial: Trial
    git_revision: str
    dirty: bool
    orchestrator: str
    label: str | None
    lock_checksum: str
    methodology: Methodology
    runtime_lock: dict[str, str]
    native_runtime: NativeAttestation
    nofile: Nofile
    workers: list[WorkerSnapshot]
    image: dict[str, object]
    jit_engine_probe: dict[str, object]
    fixture: FixtureRecord
    backend_baseline_ready: dict[str, object] | None


class Observation(TypedDict, total=False):
    id: str
    campaign: str
    cell: str
    users: int | None
    valid: bool
    reasons: list[str]
    metrics: JourneyMetrics
    methodology: Methodology
    resources: Resources
    external_resources: ExternalResources
    validity_basis: str
    artifact: str | None


class ObservedBound(TypedDict):
    campaign: str
    cell: str
    methodology: Methodology
    threshold: float
    authentication_gate: float
    highest_observed_passing_users: int | None
    lowest_observed_failing_users: int | None
    passing_observations: int
    failing_observations: int
    mixed_users: list[int]
    contradictory: bool
    unsampled_ranges: list[list[int]]
    interpretation: str


class AnalysisReport(TypedDict):
    method: str
    thresholds: list[float]
    invalid_observations: int
    observations: list[Observation]
    bounds: list[ObservedBound]
