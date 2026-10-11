"""Validated campaign identities shared by tooling; no application imports."""

from dataclasses import dataclass, field


@dataclass
class RuntimeCell:
    id: str
    python: str
    app: str = "baseline"
    build: str = "standard"
    adapter: str = "async"
    workers: int = 2
    threads: int = 16
    pool_budget: int = 20
    jit: bool = False
    gil: bool = True
    supported: bool = True
    reasons: list[str] = field(default_factory=list)
    dependency_lock: str = ""
    image: str = ""
    profile: str = ""

    def __post_init__(self) -> None:
        if self.app not in ("baseline", "modern") or self.build not in (
            "standard",
            "jit",
            "free-threaded",
        ):
            raise ValueError("unknown application/build")
        tuned = self.profile in ("tuned4", "production4")
        if self.profile not in ("", "tuned4", "production4") or (
            tuned
            and (
                self.app != "modern"
                or self.adapter != "async"
                or self.id != "python-modern-3.14.8-standard-jit0-gil1-async-" + self.profile
                or self.python != "3.14.8"
                or self.build != "standard"
                or self.jit
                or not self.gil
            )
        ):
            raise ValueError("uncontrolled runtime profile")
        if (
            self.workers not in ((4,) if tuned else (1, 2))
            or self.pool_budget != (40 if self.profile == "production4" else 20)
            or self.threads != 16
        ):
            raise ValueError("uncontrolled resource settings")
        if self.adapter not in ("async", "threaded"):
            raise ValueError("unknown adapter")
        if self.adapter == "threaded" and (self.app != "modern" or self.workers != 1):
            raise ValueError("threaded comparison requires modern one-process configuration")
        if not self.supported and not self.reasons:
            raise ValueError("unsupported cells require reasons")


@dataclass
class FixtureSpec:
    users: int = 50000
    posts: int = 500000
    follows_per_user: int = 50
    likes: int = 1000000
    comments: int = 250000
    seed: int = 20261004
    time_origin: str = "2026-01-01T00:00:00+00:00"

    def __post_init__(self) -> None:
        if (
            self.users < 2
            or self.posts < 1
            or not 0 <= self.follows_per_user < self.users
            or not 0 <= self.likes <= self.users * self.posts
            or self.comments < 0
        ):
            raise ValueError("impossible fixture counts")


@dataclass
class FixtureManifest:
    schema_revision: str
    checksum: str
    dump: str
    identity: str
    counts: dict[str, int]
    fingerprints: dict[str, str]
    generator_version: str = "1"
    spec: dict[str, int | str] = field(default_factory=dict)


@dataclass
class NativeCell:
    """One locked, locally verified language/framework topology."""

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
    upstream: str = ""
    profile: str = ""
    pool_budget: int = 20
    supported: bool = True
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        expected = {
            "java": ("spring-boot", 1, 20),
            "php": ("laravel", 20, 1),
            "go": ("net-http", 1, 20),
            "rust": ("axum", 1, 20),
            "node": ("express", 2, 10),
            "bun": ("express", 2, 10),
            "csharp": ("aspnet-core", 1, 20),
        }
        if self.language not in expected:
            raise ValueError("unknown native language")
        if self.profile not in ("", "tuned4", "production4") or (
            self.profile and not self.id.endswith("-" + self.profile)
        ):
            raise ValueError("uncontrolled native profile")
        framework, workers, connections = expected[self.language]
        if self.profile and self.id != self.language + "-" + self.framework + "-" + self.profile:
            raise ValueError("uncontrolled tuned native identity")
        if self.profile and self.language in ("node", "bun"):
            workers, connections = 4, 5
        if self.profile == "production4":
            workers, connections = (
                (4, 10)
                if self.language in ("node", "bun")
                else (32, 1)
                if self.language == "php"
                else (1, 16)
            )
        budget = 40 if self.language in ("node", "bun") else 32 if self.language == "php" else 16
        if self.language == "php" and self.framework == "laravel-octane":
            if self.id != "php-laravel-octane" + ("-" + self.profile if self.profile else ""):
                raise ValueError("uncontrolled native Octane identity")
            framework = "laravel-octane"
        if (
            self.framework != framework
            or type(self.workers) is not int
            or type(self.connections_per_worker) is not int
            or (self.workers, self.connections_per_worker) != (workers, connections)
            or self.pool_budget != (budget if self.profile == "production4" else 20)
            or self.workers * self.connections_per_worker != self.pool_budget
        ):
            raise ValueError("uncontrolled native resource settings")
        if not all(
            (self.id, self.runtime_version, self.framework_version, self.image, self.topology)
        ):
            raise ValueError("native runtime identity is incomplete")
        from pathlib import PurePosixPath

        path = PurePosixPath(self.dockerfile)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] != ("backends",):
            raise ValueError("native Dockerfile must be inside backends")
        if not self.supported and not self.reasons:
            raise ValueError("unsupported cells require reasons")
