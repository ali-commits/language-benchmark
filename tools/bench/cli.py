"""Run with PYTHONPATH=tools python -m bench.cli; credentials are environment-only."""

import argparse
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .compatibility import probe_cell, validate_runtime_lock
from .fixture import capture_fixture
from .load_data import prepare_load_data
from .manifest import atomic_json
from .models import FixtureSpec, RuntimeCell
from .report import write_report
from .reset import mark_database, restore_fixture
from .runner import run_campaign
from .seed import seed_database


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    mark = sub.add_parser("mark")
    mark.add_argument("--database", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--small", action="store_true")
    capture = sub.add_parser("capture")
    capture.add_argument("--output", type=Path, required=True)
    for name in ["restore", "recover"]:
        s = sub.add_parser(name)
        s.add_argument("--fixture", type=Path, required=True)
        s.add_argument("--database", required=True)
    prepare = sub.add_parser("prepare-load")
    prepare.add_argument("--url", required=True)
    prepare.add_argument("--credentials", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--valid-seconds", type=int, default=600)
    credentials = sub.add_parser("credentials")
    credentials.add_argument("--count", type=int, default=200)
    credentials.add_argument("--output", type=Path, required=True)
    for name in ["run", "pilot"]:
        s = sub.add_parser(name)
        s.add_argument("--config", type=Path, required=True)
        s.add_argument("--output", type=Path, required=True)
    capacity = sub.add_parser("user-capacity")
    capacity.add_argument("--config", type=Path, required=True)
    capacity.add_argument("--output", type=Path, required=True)
    capacity.add_argument("--resume", action="store_true")
    capacity.add_argument("--retry-invalid", action="store_true")
    report = sub.add_parser("report")
    report.add_argument("--results", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    compatibility = sub.add_parser("compatibility")
    compatibility.add_argument("--cell")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL", "")
    if a.command == "migrate":
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config("database/alembic.ini"), "head")
    elif a.command == "mark":
        print(mark_database(dsn, a.database))
    elif a.command == "seed":
        print(
            seed_database(
                dsn,
                FixtureSpec(users=100, posts=1000, follows_per_user=10, likes=2000, comments=500)
                if a.small
                else FixtureSpec(),
            )
        )
    elif a.command == "capture":
        print(json.dumps(asdict(capture_fixture(dsn, a.output)), indent=2))
    elif a.command in ("restore", "recover"):
        restore_fixture(dsn, a.fixture, a.database)
        print("fixture restoration verified")
    elif a.command == "credentials":
        if not 1 <= a.count <= 50000:
            raise ValueError("invalid actor count")
        rows = [
            {"username": f"user_{i:05}", "password": os.environ["SEED_PASSWORD"]}
            for i in range(1, a.count + 1)
        ]
        a.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(a.output, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(rows, f)
    elif a.command == "prepare-load":
        print(prepare_load_data(a.url, a.credentials, a.output, a.valid_seconds))
    elif a.command in ("run", "pilot"):
        if a.command == "pilot":
            with private_pilot_config(a.config, a.output) as private:
                results = run_campaign(private, a.output)
        else:
            results = run_campaign(a.config, a.output)
        print(json.dumps(results, indent=2))
        if any(
            r["status"] in ("failed", "interrupted") or not r["reset_verified"] for r in results
        ):
            raise SystemExit(1)
    elif a.command == "user-capacity":
        from .user_capacity import run_user_capacity

        print(
            json.dumps(
                run_user_capacity(
                    a.config, a.output, resume=a.resume, retry_invalid=a.retry_invalid
                ),
                indent=2,
            )
        )
    elif a.command == "report":
        write_report(a.results, a.output)
    elif a.command == "compatibility":
        lock = validate_runtime_lock(json.loads(Path("runtimes/python/lock.json").read_text()))
        if a.cell:
            print(
                json.dumps(
                    probe_cell(RuntimeCell(**next(c for c in lock["cells"] if c["id"] == a.cell))),
                    indent=2,
                )
            )
        else:
            print(json.dumps(lock, indent=2))


@contextmanager
def private_pilot_config(source: Path, output: Path):
    cfg = json.loads(source.read_text())
    cfg.update(
        smoke=True,
        repeats=1,
        measure_seconds=30,
        warmup_seconds=30,
        rates=[25, 50, 100, 200, 400, 800, 1600, 3200],
    )
    cfg["rates"] = [r for r in cfg["rates"] if r <= cfg.get("max_rate", 3200)]
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "pilot-config.redacted.json", cfg)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="benchmark-pilot-", delete=True
    ) as private:
        json.dump(cfg, private)
        private.flush()
        yield Path(private.name)


if __name__ == "__main__":
    main()
