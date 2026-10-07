import hashlib
import json
import subprocess
import uuid
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .fixture import client_command, verify_fixture
from .models import FixtureManifest
from .records import FixtureRecord, checked_record


def guard(dsn: str, allowed_database: str) -> None:
    name = conninfo_to_dict(dsn).get("dbname", "")
    if name != allowed_database or not name.startswith("language_benchmark"):
        raise ValueError("reset database must match explicit language_benchmark allowlist")


def mark_database(dsn: str, allowed_database: str) -> str:
    guard(dsn, allowed_database)
    with psycopg.connect(dsn) as c:
        identity = str(uuid.uuid4())
        c.execute(
            "INSERT INTO benchmark_identity(singleton,identity) VALUES (true,%s) ON CONFLICT DO NOTHING",
            (identity,),
        )
        row = c.execute("SELECT identity::text FROM benchmark_identity WHERE singleton").fetchone()
        if row is None or not isinstance(row[0], str):
            raise ValueError("database identity is missing or invalid")
        return row[0]


def restore_fixture(dsn: str, manifest_path: Path, allowed_database: str) -> FixtureManifest:
    guard(dsn, allowed_database)
    m = FixtureManifest(**checked_record(json.loads(manifest_path.read_text()), FixtureRecord))
    dump = manifest_path.parent / m.dump
    if dump.resolve().parent != manifest_path.parent.resolve():
        raise ValueError("dump path escaped fixture directory")
    if hashlib.sha256(dump.read_bytes()).hexdigest() != m.checksum:
        raise ValueError("fixture checksum mismatch")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("SELECT pg_advisory_lock(20261004)")
        marker = c.execute(
            "SELECT identity::text FROM benchmark_identity WHERE singleton"
        ).fetchone()
        if marker is None or marker[0] != m.identity:
            raise ValueError("database identity mismatch")
        active = c.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND application_name='lb-backend'"
        ).fetchone()
        if active is None:
            raise ValueError("database session count is missing")
        if active[0]:
            raise ValueError("backend sessions remain; stop backend before reset")
        argv, env = client_command(
            "pg_restore",
            dsn,
            [
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--single-transaction",
            ],
        )
        with dump.open("rb") as f:
            subprocess.run(
                argv,
                env=env,
                stdin=f,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=1800,
            )
        c.execute("ANALYZE")
        if verify_fixture(dsn, m):
            raise RuntimeError("restored data fingerprints do not match fixture")
    return m
