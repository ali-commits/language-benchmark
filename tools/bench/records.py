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
