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

    def __post_init__(self):
        if self.app not in ("baseline", "modern") or self.build not in (
            "standard",
            "jit",
            "free-threaded",
        ):
            raise ValueError("unknown application/build")
        if self.workers not in (1, 2) or self.pool_budget != 20 or self.threads != 16:
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

    def __post_init__(self):
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
    counts: dict
    fingerprints: dict
    generator_version: str = "1"
    spec: dict = field(default_factory=dict)
