"""Checked wire records at the engine's process and artifact boundaries."""
from typing_extensions import TypedDict

from ..records import Ulimit


class CommandResponse(TypedDict, total=False):
    timeout: bool
    returncode: int
    stdout: str
    stderr: str

class ImageConfig(TypedDict, total=False):
    Labels: dict[str, str | None] | None

class DockerImage(TypedDict, total=False):
    Id: str
    RepoDigests: list[str] | None
    Os: str
    Architecture: str
    Config: ImageConfig

class ContainerConfig(TypedDict, total=False):
    Labels: dict[str, str] | None

class ContainerState(TypedDict, total=False):
    Running: bool
    StartedAt: str
    ExitCode: int
    OOMKilled: bool

class HostConfig(TypedDict, total=False):
    NanoCpus: int
    Memory: int
    MemorySwap: int
    Ulimits: list[Ulimit] | None

class PortBinding(TypedDict):
    HostPort: str

class NetworkSettings(TypedDict):
    Ports: dict[str, list[PortBinding] | None]

class ContainerInfo(TypedDict, total=False):
    Id: str
    Name: str
    Image: str
    Config: ContainerConfig
    State: ContainerState
    HostConfig: HostConfig
    NetworkSettings: NetworkSettings

class NetworkInfo(TypedDict, total=False):
    Id: str
    Labels: dict[str, str] | None

class BuildManifest(TypedDict):
    checksums: dict[str, str]
    dockerfile_sha256: str
    build_args_sha256: str
    identity: str

class HostPoint(TypedDict):
    epoch: float
    cpu: list[int]
    memory_total: int
    memory_available: int

class CpuUsage(TypedDict):
    total_usage: int

class CpuStats(TypedDict):
    cpu_usage: CpuUsage

class MemoryStats(TypedDict, total=False):
    usage: int

class ProbePoint(TypedDict, total=False):
    epoch: float
    cpu: list[int]
    memory_total: int
    memory_available: int
    cpu_stats: CpuStats
    memory_stats: MemoryStats

class RunningContainer(TypedDict):
    ID: str
    Names: str

class Authentication(TypedDict):
    distinct_accounts: int
    target_accounts: int
    fraction: float | None

class JournalEntry(TypedDict):
    sequence: int
    action: str
    state: str
    epoch: float
    details: dict[str, object]

class RestoreDetails(TypedDict):
    owner: dict[str, str]
    backend_lease_id: str
    fixture_identity: str

class LeaseAssignment(TypedDict):
    host: dict[str, object]
    id: str

class ReleaseDetails(TypedDict):
    owner: dict[str, str]
    fixture_identity: str
    restore_sequence: int | None
    leases: list[LeaseAssignment]

class StageDetails(TypedDict):
    path: str

class Manifest(TypedDict, total=False):
    version: int
    job: dict[str, object]
    users: int | None
    queue_id: str
    trial_id: str
    fixture_manifest_sha256: str | None

class RawMetric(TypedDict, total=False):
    name: str
    type: str
    unit: str

class RawCriterion(TypedDict, total=False):
    metric: str
    scope: str
    statistic: str
    value: str | int | float
    unit: str

class RawWorkload(TypedDict, total=False):
    metrics: dict[str, RawMetric]

class RawDefaults(TypedDict, total=False):
    workload: str | None
    criteria: list[RawCriterion]

class RawJob(RawDefaults, total=False):
    objectives: dict[str, list[RawCriterion]]

class RawConfig(TypedDict, total=False):
    version: int
    definitions: list[str]
    targets: dict[str, dict[str, object]]
    workloads: dict[str, RawWorkload]
    environments: dict[str, dict[str, object]]
    defaults: RawDefaults
    jobs: list[RawJob]

class RdsConfig(TypedDict):
    provider: str
    region: str
    identifier: str

class BaselinePoint(TypedDict):
    epoch: float
    cpu: list[int]
    memory_available: float

class TrialManifest(Manifest):
    execution_id: str
    image_id: str
    generator_image_id: str
    workload_identity: str
    wrapper_sha256: dict[str, str]
    colocated_generator: bool

class PreparedProvenance(TypedDict, total=False):
    image_id: str
    generator_image_id: str | None
    generator_version: str
    fixture_manifest_sha256: str | None
    wrapper_sha256: dict[str, str]
    build_input_identity: str | None
    workload_identity: str
