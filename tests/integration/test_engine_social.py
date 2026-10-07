"""Real disposable engine lifecycle; opt in explicitly, never use existing DBs."""

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import psycopg
import pytest

from tools.bench.engine.config import load_config, resolve_jobs
from tools.bench.engine.trial import TrialContext, run_trial
from tools.bench.engine.types import (
    Environment,
    Fixture,
    Host,
    Network,
    OccupancyTolerance,
    Readiness,
    ResolvedJob,
    SecretRef,
    Stage,
    Target,
    Telemetry,
    Workload,
)
from tools.bench.engine.types import (
    TestPolicy as Policy,
)

pytestmark = pytest.mark.skipif(
    os.getenv("BENCH_INTEGRATION") != "1", reason="opt-in local Docker lifecycle"
)
ROOT = Path(__file__).resolve().parents[2]


def docker(*args):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_stateless_real_trial(tmp_path):
    config = load_config(ROOT / "examples/http/queue.yaml")
    job = resolve_jobs(config)[0]
    job = job.model_copy(
        update={
            "stages": [Stage(id="hold", from_users=2, to_users=2, duration=3.0, evaluate=True)],
            "drain": 0.2,
            "graceful_ramp_down": 0.2,
            "environment": job.environment.model_copy(
                update={"telemetry": Telemetry(interval=0.2)}
            ),
            "hold_tolerance": OccupancyTolerance(grace=1.1),
        }
    )
    evidence = run_trial(
        TrialContext(job, 2, tmp_path / "trial", "test-" + uuid.uuid4().hex, "http")
    )
    assert evidence.cleanup_verified
    assert evidence.valid, evidence.invalid_reasons
    assert any(s.scope == "backend_host" and s.metric == "cpu_used" for s in evidence.samples)
    assert any(
        s.scope == "container" and s.normalization == "container_quota" for s in evidence.samples
    )


def test_social_fixture_restore_and_100_users(tmp_path, monkeypatch):
    from tools.bench.fixture import capture_fixture, verify_fixture
    from tools.bench.models import FixtureSpec
    from tools.bench.reset import mark_database
    from tools.bench.seed import seed_database

    name = "bench-social-test-" + uuid.uuid4().hex[:12]
    # Explicitly owned disposable database; inspect existing images before creating it.
    docker("image", "inspect", "postgres:18-alpine")
    docker("image", "inspect", "lb-go:1.27.1")
    db = docker(
        "run",
        "-d",
        "--name",
        name,
        "--label",
        "bench.integration=" + name,
        "-e",
        "POSTGRES_PASSWORD=disposable-test-password",
        "-e",
        "POSTGRES_DB=language_benchmark_engine_test",
        "-p",
        "127.0.0.1::5432",
        "postgres:18-alpine",
    )
    try:
        info = json.loads(docker("inspect", db))[0]
        port = info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
        dsn = f"postgresql://postgres:disposable-test-password@127.0.0.1:{port}/language_benchmark_engine_test"
        for _ in range(100):
            try:
                with psycopg.connect(dsn) as connection:
                    connection.execute((ROOT / "database/schema.sql").read_text())
                break
            except psycopg.OperationalError:
                time.sleep(0.1)
        else:
            pytest.fail("disposable PostgreSQL did not become ready")
        monkeypatch.setenv("DB_CLIENT_CONTAINER", name)
        monkeypatch.setenv("SEED_PASSWORD", "disposable-account-password")
        monkeypatch.setenv("SEED_HASH_WORKERS", "1")
        monkeypatch.setenv("SOCIAL_TEST_DSN", dsn)
        # Bridge target reaches DB through the inspected disposable bridge address.
        db_address = info["NetworkSettings"].get("IPAddress")
        if not db_address:
            db_address = info["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        monkeypatch.setenv(
            "SOCIAL_TARGET_DSN",
            f"postgresql://postgres:disposable-test-password@{db_address}:5432/language_benchmark_engine_test?sslmode=disable",
        )
        monkeypatch.setenv("SOCIAL_JWT", "disposable-jwt-secret-at-least-32-bytes")
        mark_database(dsn, "language_benchmark_engine_test")
        seed_database(
            dsn, FixtureSpec(users=100, posts=200, follows_per_user=2, likes=100, comments=20)
        )
        fixture = capture_fixture(dsn, tmp_path / "fixture")
        with psycopg.connect(dsn) as connection:
            usernames = [
                row[0] for row in connection.execute("SELECT username FROM users ORDER BY id")
            ]
        workload_root = tmp_path / "source"
        workload_root.mkdir()
        shutil.copy(ROOT / "benchmarks/k6/engine/social.js", workload_root / "social.js")
        actors = workload_root / "actors.json"
        actors.write_text(
            json.dumps(
                {
                    "actors": [
                        {"username": name, "password": "disposable-account-password"}
                        for name in usernames
                    ]
                }
            )
        )
        actors.chmod(0o600)
        host = Host(transport="local")
        job = ResolvedJob(
            id="social",
            target_name="go",
            workload_name="social",
            environment_name="local",
            target=Target(
                image="lb-go:1.27.1",
                readiness=Readiness(path="/health/ready", timeout=10.0),
                env={
                    "DATABASE_URL": SecretRef(env="SOCIAL_TARGET_DSN"),
                    "JWT_SECRET": SecretRef(env="SOCIAL_JWT"),
                },
            ),
            workload=Workload(
                adapter="social",
                script=workload_root / "social.js",
                dependencies=[actors],
                metrics={},
                parameters={"wait_seconds": 0.05, "timeout_seconds": 15},
                fixture=Fixture(
                    adapter="social",
                    dsn=SecretRef(env="SOCIAL_TEST_DSN"),
                    manifest=tmp_path / "fixture/manifest.json",
                    allowed_database="language_benchmark_engine_test",
                    runtime_cell="go-net-http",
                ),
            ),
            environment=Environment(
                backend=host,
                generator=host,
                network=Network(mode="direct"),
                telemetry=Telemetry(interval=0.25),
            ),
            test=Policy(type="fixed", users=100),
            criteria=[],
            stages=[
                Stage(id="ramp", from_users=0, to_users=100, duration=2.0),
                Stage(id="hold", from_users=100, to_users=100, duration=10.0, evaluate=True),
                Stage(id="reduce", from_users=100, to_users=20, duration=1.0),
                Stage(id="recover", from_users=20, to_users=20, duration=3.0),
            ],
            drain=1.0,
            graceful_ramp_down=0.5,
            hold_tolerance=OccupancyTolerance(grace=1.1),
        )
        evidence = run_trial(TrialContext(job, 100, tmp_path / "trial", name, "social"))
        assert evidence.cleanup_verified, evidence.invalid_reasons
        assert evidence.valid, evidence.invalid_reasons
        assert not verify_fixture(dsn, fixture)
        auth = json.loads((tmp_path / "trial/authentication.json").read_text())
        assert auth["distinct_accounts"] == auth["target_accounts"] == 100
        assert (tmp_path / "trial/social-runtime.json").exists()
        hold = next(stage for stage in evidence.stages if stage.id == "hold")
        assert any(
            sample.metric == "authentication"
            and sample.numerator == 100
            and hold.start_seconds <= sample.timestamp < hold.end_seconds
            for sample in evidence.samples
        )
    finally:
        info = json.loads(docker("inspect", db))[0]
        assert info["Config"]["Labels"]["bench.integration"] == name
        docker("rm", "-f", db)
