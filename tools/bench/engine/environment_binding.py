"""Private keyed environment receipts; plaintext exists only in process memory."""

import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .types import ResolvedJob


@dataclass(frozen=True)
class BoundEnvironment:
    digest: str
    key_path: Path
    values: dict[str, str] = field(repr=False)


def reference_names(job: ResolvedJob) -> set[str]:
    """Only schema-declared references; opaque parameters are literal JSON."""
    names = {ref.env for ref in job.target.env.values()}
    fixture = job.workload.fixture
    if fixture:
        if fixture.dsn:
            names.add(fixture.dsn.env)
        for hook in (fixture.prepare, fixture.reset, fixture.verify):
            if hook:
                names.update(ref.env for ref in hook.env.values())
    return names


def bind_environment(
    job: ResolvedJob, key_path: Path, *, expected: str | None = None, create: bool = False
) -> BoundEnvironment | None:
    names = reference_names(job)
    if not names:
        return None
    try:
        values = {name: os.environ[name] for name in sorted(names)}
    except KeyError:
        raise ValueError("required execution environment reference is unset") from None
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if create and expected is None and not key_path.exists():
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(os.urandom(32))
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(key_path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    try:
        fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            key = stream.read(33)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or len(key) != 32
        ):
            raise ValueError("invalid environment binding key")
    except OSError:
        raise ValueError(
            "environment binding key missing or unavailable; restore the private key"
        ) from None
    digest = hmac.new(
        key, json.dumps(values, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256
    ).hexdigest()
    if expected is not None and not hmac.compare_digest(expected, digest):
        raise ValueError("resolved execution environment changed; cannot resume")
    return BoundEnvironment(digest, key_path, values)
