import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .models import FixtureManifest
from .records import checked_record
from .seed import COLUMNS, count_rows


def client_command(tool: str, dsn: str, args: list[str]) -> tuple[list[str], dict[str, str]]:
    raw_info = conninfo_to_dict(dsn)
    if any(not isinstance(value, str) for value in raw_info.values()):
        raise ValueError("PostgreSQL connection values must be strings")
    info = {key: value for key, value in raw_info.items() if isinstance(value, str)}
    env = os.environ.copy()
    # Never put password or the connection URL in argv, output, or exceptions.
    env.update(
        {
            ("PGDATABASE" if k == "dbname" else "PG" + k.upper()): v
            for k, v in info.items()
            if k in ("host", "port", "user", "password", "dbname", "sslmode", "sslrootcert")
        }
    )
    container = os.environ.get("DB_CLIENT_CONTAINER")
    if container:
        # Local development only: the known DB container uses its Unix socket.
        if info.get("host") not in ("127.0.0.1", "localhost", "db"):
            raise ValueError("DB container clients are local-only")
        return [
            "docker",
            "exec",
            "-i",
            container,
            tool,
            "-U",
            info["user"],
            "-d",
            info["dbname"],
            *args,
        ], env
    if not shutil.which(tool):
        raise RuntimeError("PostgreSQL client required: " + tool)
    return [tool, "-d", info["dbname"], *args], env


def logical_fingerprints(dsn: str) -> tuple[dict[str, int], dict[str, str]]:
    counts = {}
    digests = {}
    with psycopg.connect(dsn) as c:
        for table in COLUMNS:
            counts[table] = count_rows(c, table)
            keys = ",".join(COLUMNS[table][:2] if table in ("follows", "likes") else ("id",))
            h = hashlib.sha256()
            with c.cursor().copy(f"COPY (SELECT * FROM {table} ORDER BY {keys}) TO STDOUT") as copy:
                for row in copy:
                    h.update(row)
            digests[table] = h.hexdigest()
    return counts, digests


def capture_fixture(dsn: str, output_dir: Path) -> FixtureManifest:
    output_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(dsn) as c:
        marker = c.execute(
            "SELECT identity::text,fixture_spec FROM benchmark_identity WHERE singleton"
        ).fetchone()
        if not marker:
            raise ValueError("database is not explicitly marked for benchmark reset")
    dump = output_dir / "fixture.dump"
    argv, env = client_command("pg_dump", dsn, ["-Fc", "--no-owner", "--no-acl"])
    with dump.open("wb") as f:
        subprocess.run(argv, env=env, stdout=f, stderr=subprocess.PIPE, check=True, timeout=1800)
    counts, digests = logical_fingerprints(dsn)
    manifest = FixtureManifest(
        "0001",
        hashlib.sha256(dump.read_bytes()).hexdigest(),
        dump.name,
        checked_record(marker[0], str),
        counts,
        digests,
        spec=checked_record(marker[1], dict[str, int | str]),
    )
    (output_dir / "manifest.json").write_text(json.dumps(asdict(manifest), indent=2) + "\n")
    return manifest


def verify_fixture(dsn: str, manifest: FixtureManifest) -> list[str]:
    counts, digests = logical_fingerprints(dsn)
    return [
        name
        for name in manifest.counts
        if counts.get(name) != manifest.counts[name]
        or digests.get(name) != manifest.fingerprints[name]
    ]
