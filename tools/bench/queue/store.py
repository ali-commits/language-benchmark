"""Local SQLite control state. Do not place this database on NFS/SMB."""

import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from tools.bench.engine.config import resolve_jobs
from tools.bench.engine.targets import snapshot_build, tree_checksums
from tools.bench.engine.types import BenchmarkConfig, Job, ResolvedJob
from tools.bench.records import checked_record

from .records import (
    ControllerState,
    FrozenInputs,
    JobRow,
    QueueSnapshot,
)


class QueueConflict(ValueError):
    pass


@dataclass(frozen=True)
class QueueReceipt:
    queue_id: str
    revision: int


@dataclass(frozen=True)
class JobReceipt:
    queue_id: str
    job_id: str
    revision: int
    state: str


@dataclass(frozen=True)
class ClaimedJob(JobReceipt):
    owner: str
    definition: Job
    resolved_jobs: tuple[ResolvedJob, ...]
    inputs: FrozenInputs


@dataclass(frozen=True)
class PendingDefinition:
    definition: Job
    resolved: tuple[ResolvedJob, ...]
    inputs: FrozenInputs


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _file_identity(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(str(path.stat().st_mode).encode() + b"\0" + path.read_bytes()).hexdigest()


def _inputs(jobs: Sequence[ResolvedJob], state_path: Path) -> FrozenInputs:
    paths = set()
    builds = {}
    for job in jobs:
        paths.update([job.workload.script, *job.workload.dependencies])
        if job.workload.fixture and job.workload.fixture.manifest:
            paths.add(job.workload.fixture.manifest)
        if job.target.build:
            build = job.target.build
            builds[build.model_dump_json()] = build
    result: FrozenInputs = {}
    for key, build in sorted(builds.items()):
        source = build.context.resolve()
        dockerfile = (build.dockerfile or source / "Dockerfile").resolve()
        specific = Path(str(dockerfile) + ".dockerignore")
        # Bind both candidates' presence and content, even when rules currently
        # select identical input sets. A newly created specific file changes precedence.
        policies = {str(p): _file_identity(p) for p in (specific, source / ".dockerignore")}
        with tempfile.TemporaryDirectory(prefix="bench-queue-inputs-") as directory:
            snapshot = snapshot_build(build, Path(directory) / "snapshot")
            if source == state_path or source in state_path.parents:
                relative = state_path.relative_to(source).as_posix()
                if any(
                    name == relative or name.startswith(relative + "-")
                    for name in snapshot.checksums
                ):
                    raise QueueConflict(
                        "queue control state is included in Docker build inputs; exclude its state directory using the effective Docker ignore file or move queue state outside the build context"
                    )
            result["build:" + key] = {"identity": snapshot.identity, "ignore_policies": policies}
    for path in sorted(paths):
        path = path.resolve()
        if path.is_dir():
            result[str(path)] = tree_checksums(path)
        else:
            if not path.exists():
                raise QueueConflict("frozen input missing")
            result[str(path)] = _file_identity(path)
    return result


def require_local_storage(path: Path) -> None:
    """Linux control state requires local filesystem locking/durability semantics."""
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.exists():
        raise QueueConflict("queue control storage requires Linux mount inspection")
    matches = []
    for line in mountinfo.read_text().splitlines():
        fields = line.split()
        mount = Path(fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\"))
        if path == mount or mount in path.parents:
            matches.append((len(mount.parts), fields[fields.index("-") + 1]))
    if not matches or max(matches)[1] in {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "9p",
        "ceph",
        "glusterfs",
        "fuse.sshfs",
    }:
        raise QueueConflict(
            "queue control storage must be local; network filesystems are unsupported"
        )


class QueueStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        require_local_storage(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise QueueConflict("unsupported queue schema")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS queues(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, config TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
                CREATE TABLE IF NOT EXISTS jobs(queue TEXT REFERENCES queues(id), id TEXT, position INTEGER, revision INTEGER NOT NULL, state TEXT NOT NULL, definition TEXT NOT NULL, resolved TEXT NOT NULL, inputs TEXT NOT NULL, owner TEXT, PRIMARY KEY(queue,id));
                CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY, queue TEXT REFERENCES queues(id), job TEXT, kind TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trials(queue TEXT, job TEXT, id TEXT, path TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(queue,job,id), FOREIGN KEY(queue,job) REFERENCES jobs(queue,id));
                CREATE TABLE IF NOT EXISTS controllers(queue TEXT PRIMARY KEY REFERENCES queues(id), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(sequence INTEGER PRIMARY KEY, queue TEXT REFERENCES queues(id), after_current INTEGER NOT NULL);
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                PRAGMA user_version=1;
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _event(self, conn: sqlite3.Connection, queue: str, job: str | None, kind: str, payload: object = None) -> int:
        conn.execute("UPDATE queues SET revision=revision+1 WHERE id=?", (queue,))
        revision = conn.execute("SELECT revision FROM queues WHERE id=?", (queue,)).fetchone()[0]
        conn.execute(
            "INSERT INTO events(queue,job,kind,revision,payload) VALUES(?,?,?,?,?)",
            (queue, job, kind, revision, _json(payload or {})),
        )
        return checked_record(revision, int)

    def _prepare_definition(self, config: BenchmarkConfig, job: Job) -> PendingDefinition:
        resolved = tuple(resolve_jobs(config.model_copy(update={"jobs": [job]})))
        return PendingDefinition(job, resolved, _inputs(resolved, self.path))

    def _insert(self, conn: sqlite3.Connection, queue: str, prepared: PendingDefinition) -> JobReceipt:
        job, resolved = prepared.definition, prepared.resolved
        position = conn.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM jobs WHERE queue=?", (queue,)
        ).fetchone()[0]
        try:
            conn.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,NULL)",
                (
                    queue,
                    job.id,
                    position,
                    1,
                    "pending",
                    job.model_dump_json(by_alias=True),
                    _json([j.model_dump(mode="json", by_alias=True) for j in resolved]),
                    _json(prepared.inputs),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise QueueConflict("duplicate job ID") from exc
        conn.execute("UPDATE queues SET state='pending' WHERE id=?", (queue,))
        self._event(conn, queue, job.id, "append", dict(self._job(conn, queue, job.id)))
        return JobReceipt(queue, job.id, 1, "pending")

    def register(self, config: BenchmarkConfig) -> QueueReceipt:
        queue = config.name or str(uuid4())
        prepared = [self._prepare_definition(config, job) for job in config.jobs]
        with self._transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO queues(id,revision,config) VALUES(?,?,?)",
                    (queue, 0, _json(config.public_snapshot())),
                )
            except sqlite3.IntegrityError as exc:
                raise QueueConflict("queue already registered") from exc
            for definition in prepared:
                self._insert(conn, queue, definition)
            return QueueReceipt(
                queue,
                conn.execute("SELECT revision FROM queues WHERE id=?", (queue,)).fetchone()[0],
            )

    def _config(self, conn: sqlite3.Connection, queue: str) -> BenchmarkConfig:
        row = conn.execute("SELECT config FROM queues WHERE id=?", (queue,)).fetchone()
        if not row:
            raise QueueConflict("unknown queue")
        return BenchmarkConfig.model_validate_json(row[0])

    def append(self, queue: str, job: Job) -> JobReceipt:
        with self._connection() as conn:
            config = self._config(conn, queue)
        prepared = self._prepare_definition(config, job)
        with self._transaction() as conn:
            return self._insert(conn, queue, prepared)

    def _job(self, conn: sqlite3.Connection, queue: str, job_id: str) -> JobRow:
        row = conn.execute("SELECT * FROM jobs WHERE queue=? AND id=?", (queue, job_id)).fetchone()
        if not row:
            raise QueueConflict("unknown job")
        return checked_record(dict(row), JobRow)

    def update(self, queue: str, job_id: str, replacement: Job, expected_revision: int) -> JobReceipt:
        if replacement.id != job_id:
            raise QueueConflict("replacement ID differs")
        with self._connection() as conn:
            row = self._job(conn, queue, job_id)
            if row["state"] != "pending" or row["revision"] != expected_revision:
                raise QueueConflict("job is active or revision changed")
            config = self._config(conn, queue)
        prepared = self._prepare_definition(config, replacement)
        with self._transaction() as conn:
            row = self._job(conn, queue, job_id)
            if row["state"] != "pending" or row["revision"] != expected_revision:
                raise QueueConflict("job is active or revision changed")
            revision = expected_revision + 1
            conn.execute(
                "UPDATE jobs SET definition=?,resolved=?,inputs=?,revision=? WHERE queue=? AND id=?",
                (
                    replacement.model_dump_json(by_alias=True),
                    _json([j.model_dump(mode="json", by_alias=True) for j in prepared.resolved]),
                    _json(prepared.inputs),
                    revision,
                    queue,
                    job_id,
                ),
            )
            self._event(
                conn,
                queue,
                job_id,
                "update",
                {"previous": dict(row), "replacement": dict(self._job(conn, queue, job_id))},
            )
            return JobReceipt(queue, job_id, revision, "pending")

    def cancel(self, queue: str, job_id: str) -> JobReceipt:
        with self._transaction() as conn:
            row = self._job(conn, queue, job_id)
            if row["state"] != "pending":
                raise QueueConflict("only pending jobs can be cancelled")
            revision = row["revision"] + 1
            conn.execute(
                "UPDATE jobs SET state='cancelled',revision=? WHERE queue=? AND id=?",
                (revision, queue, job_id),
            )
            self._event(conn, queue, job_id, "cancel")
            return JobReceipt(queue, job_id, revision, "cancelled")

    def _dispatch_blocked(self, conn: sqlite3.Connection, queue: str) -> bool:
        return bool(conn.execute("SELECT 1 FROM requests WHERE queue=?", (queue,)).fetchone()
                    or conn.execute("SELECT 1 FROM jobs WHERE queue=? AND state='active'", (queue,)).fetchone())

    def _next_pending(self, conn: sqlite3.Connection, queue: str) -> JobRow | None:
        row = conn.execute(
            "SELECT * FROM jobs WHERE queue=? AND state='pending' ORDER BY position LIMIT 1",
            (queue,),
        ).fetchone()
        return checked_record(dict(row), JobRow) if row else None

    def claim_next(self, queue: str, owner: str) -> ClaimedJob | None:
        if not owner:
            raise QueueConflict("owner required")
        # Docker export and source hashing never hold the SQLite writer lock.
        # Recheck stop, ownership, ordering and revision in one short transaction.
        for _ in range(3):
            with self._connection() as conn:
                self._config(conn, queue)
                if self._dispatch_blocked(conn, queue):
                    return None
                row = self._next_pending(conn, queue)
            resolved: tuple[ResolvedJob, ...] = ()
            expected: FrozenInputs = {}
            actual: FrozenInputs = {}
            failure: Exception | None = None
            if row is not None:
                resolved = tuple(ResolvedJob.model_validate_json(_json(j)) for j in
                    checked_record(json.loads(row["resolved"]), list[dict[str, JsonValue]]))
                expected = checked_record(json.loads(row["inputs"]), FrozenInputs)
                try:
                    actual = _inputs(resolved, self.path)
                except (OSError, QueueConflict) as exc:
                    failure = exc
            with self._transaction() as conn:
                if self._dispatch_blocked(conn, queue):
                    return None
                current = self._next_pending(conn, queue)
                if current is None:
                    conn.execute("UPDATE queues SET state='completed' WHERE id=?", (queue,))
                    return None
                if row is None or (current["id"], current["revision"]) != (row["id"], row["revision"]):
                    continue
                if failure is not None:
                    raise QueueConflict("frozen input missing or unavailable") from failure
                if actual != expected:
                    raise QueueConflict("frozen input changed; explicitly update pending job")
                revision = row["revision"] + 1
                conn.execute(
                    "UPDATE jobs SET state='active',owner=?,revision=? WHERE queue=? AND id=?",
                    (owner, revision, queue, row["id"]),
                )
                self._event(conn, queue, row["id"], "claim", {"owner": owner})
                return ClaimedJob(queue, row["id"], revision, "active", owner,
                    Job.model_validate_json(row["definition"]), resolved, expected)
        raise QueueConflict("pending job changed repeatedly during input preparation; retry claim")

    def finish(
        self, queue: str, job_id: str, owner: str, *, state: str = "completed", expected_revision: int | None = None
    ) -> JobReceipt:
        if state not in ("completed", "failed", "interrupted"):
            raise QueueConflict("invalid terminal state")
        with self._transaction() as conn:
            row = self._job(conn, queue, job_id)
            if (
                row["state"] != "active"
                or row["owner"] != owner
                or (expected_revision is not None and row["revision"] != expected_revision)
            ):
                raise QueueConflict("ownership or revision changed")
            revision = row["revision"] + 1
            conn.execute(
                "UPDATE jobs SET state=?,revision=? WHERE queue=? AND id=?",
                (state, revision, queue, job_id),
            )
            self._event(conn, queue, job_id, state)
            return JobReceipt(queue, job_id, revision, state)

    def request_stop(self, queue: str, after_current: bool = False) -> QueueReceipt:
        with self._transaction() as conn:
            self._config(conn, queue)
            conn.execute(
                "INSERT INTO requests(queue,after_current) VALUES(?,?)", (queue, int(after_current))
            )
            return QueueReceipt(
                queue, self._event(conn, queue, None, "stop", {"after_current": after_current})
            )

    def clear_stop(self, queue: str) -> QueueReceipt:
        with self._transaction() as conn:
            self._config(conn, queue)
            conn.execute("DELETE FROM requests WHERE queue=?", (queue,))
            return QueueReceipt(queue, self._event(conn, queue, None, "resume"))

    def stop_request(self, queue: str) -> bool | None:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT after_current FROM requests WHERE queue=?", (queue,)
            ).fetchall()
            return all(bool(r[0]) for r in rows) if rows else None

    def record_trial(self, queue: str, job_id: str, owner: str, trial_id: str, path: Path, state: str = "started") -> QueueReceipt:
        with self._transaction() as conn:
            row = self._job(conn, queue, job_id)
            if row["state"] != "active" or row["owner"] != owner:
                raise QueueConflict("trial requires active owner")
            existing = conn.execute(
                "SELECT path FROM trials WHERE queue=? AND job=? AND id=?",
                (queue, job_id, trial_id),
            ).fetchone()
            if existing and existing[0] != str(path.resolve()):
                raise QueueConflict("trial path is immutable")
            conn.execute(
                "INSERT INTO trials VALUES(?,?,?,?,?) ON CONFLICT(queue,job,id) DO UPDATE SET state=excluded.state",
                (queue, job_id, trial_id, str(path.resolve()), state),
            )
            return QueueReceipt(
                queue,
                self._event(
                    conn,
                    queue,
                    job_id,
                    "trial",
                    {"id": trial_id, "path": str(path.resolve()), "state": state},
                ),
            )

    def jobs(self, queue: str) -> tuple[JobReceipt, ...]:
        with self._connection() as conn:
            return tuple(
                JobReceipt(queue, r["id"], r["revision"], r["state"])
                for r in conn.execute(
                    "SELECT * FROM jobs WHERE queue=? ORDER BY position", (queue,)
                )
            )

    def snapshot(self, queue: str) -> QueueSnapshot:
        with self._connection() as conn:
            conn.execute("BEGIN")
            self._config(conn, queue)
            return checked_record({
                table: [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT * FROM {table} WHERE {'id' if table == 'queues' else 'queue'}=?",
                        (queue,),
                    )
                ]
                for table in ("queues", "jobs", "events", "trials", "requests")
            }, QueueSnapshot)

    def active_job(self, queue: str, owner: str) -> ClaimedJob | None:
        """Read retained ownership on restart; no reassignment or stale takeover."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE queue=? AND state='active'", (queue,)
            ).fetchone()
            if not row:
                return None
            row = checked_record(dict(row), JobRow)
            if row["owner"] != owner:
                raise QueueConflict("active job belongs to another owner")
            return ClaimedJob(
                queue,
                row["id"],
                row["revision"],
                row["state"],
                owner,
                Job.model_validate_json(row["definition"]),
                tuple(
                    ResolvedJob.model_validate_json(_json(j)) for j in checked_record(json.loads(row["resolved"]), list[dict[str, JsonValue]])
                ),
                checked_record(json.loads(row["inputs"]), FrozenInputs),
            )

    def controller_state(self, queue: str) -> ControllerState | None:
        """Durable private controller checkpoint; callers hold ControllerLock."""
        with self._connection() as conn:
            self._config(conn, queue)
            row = conn.execute("SELECT payload FROM controllers WHERE queue=?", (queue,)).fetchone()
            return checked_record(json.loads(row[0]), ControllerState) if row else None

    def checkpoint(self, queue: str, payload: ControllerState, *, state: str | None = None) -> None:
        with self._transaction() as conn:
            self._config(conn, queue)
            conn.execute(
                "INSERT INTO controllers VALUES(?,?) ON CONFLICT(queue) DO UPDATE SET payload=excluded.payload",
                (queue, _json(payload)),
            )
            if state is not None:
                if state not in {
                    "pending",
                    "preparing",
                    "running",
                    "cleaning",
                    "stopping",
                    "completed",
                    "interrupted",
                    "needs_attention",
                }:
                    raise QueueConflict("invalid queue state")
                conn.execute("UPDATE queues SET state=? WHERE id=?", (state, queue))
            self._event(conn, queue, None, "checkpoint", {"state": state})
