"""Modern tuning controls preserve historical defaults and credential-free imports."""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from modern_api.settings import Settings


def test_worker_budget_and_defaults(monkeypatch):
    monkeypatch.delenv("WORKERS", raising=False)
    monkeypatch.delenv("HASH_CONCURRENCY", raising=False)
    defaults = dict(database_url="postgresql://localhost/local", jwt_secret="x" * 32)
    cfg = Settings(**defaults)
    assert (cfg.workers, cfg.hash_concurrency, cfg.thread_limit) == (2, 2, 16)
    cfg = Settings(**defaults, workers=4, hash_concurrency=1)
    assert (20 // cfg.workers, cfg.hash_concurrency, cfg.thread_limit) == (5, 1, 16)
    for workers in (0, 3, 5):
        with pytest.raises(ValueError):
            Settings(**defaults, workers=workers)
    for slots in (0, 3, 5):
        with pytest.raises(ValueError):
            Settings(**defaults, hash_concurrency=slots)


def test_security_import_and_actual_semaphore_capacity(monkeypatch):
    monkeypatch.setenv("HASH_CONCURRENCY", "1")
    import modern_api.security as security

    security = importlib.reload(security)
    assert security.HASH_LIMIT.acquire(blocking=False)
    assert not security.HASH_LIMIT.acquire(blocking=False)
    security.HASH_LIMIT.release()
    monkeypatch.delenv("HASH_CONCURRENCY")
    importlib.reload(security)
    assert security.HASH_LIMIT.acquire(blocking=False)
    assert security.HASH_LIMIT.acquire(blocking=False)
    assert not security.HASH_LIMIT.acquire(blocking=False)
    security.HASH_LIMIT.release()
    security.HASH_LIMIT.release()
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "JWT_SECRET")}
    env["PYTHONPATH"] = str(Path("backends/python/modern/src").resolve())
    result = subprocess.run([sys.executable, "-c", "import modern_api.security"], env=env)
    assert result.returncode == 0


@pytest.mark.parametrize("workers,slots", [(2, 2), (4, 1)])
def test_live_worker_receipts(tmp_path, workers, slots):
    import json
    import time
    import urllib.request

    dsn = os.environ.get("PYTHON_TUNING_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires disposable local PostgreSQL")
    nonce = f"local-python-tuning-{workers}"
    env = {
        **os.environ,
        "DATABASE_URL": dsn,
        "JWT_SECRET": "x" * 32,
        "WORKERS": str(workers),
        "HASH_CONCURRENCY": str(slots),
        "APP_VARIANT": "modern",
        "RUN_DIR": str(tmp_path),
        "RUN_ID": nonce,
        "PYTHONPATH": str(Path("backends/python/modern/src").resolve()),
    }
    child = subprocess.Popen(
        [sys.executable, "backends/python/entrypoint.py"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(100):
            files = list(tmp_path.glob("*.json"))
            if len(files) == workers or child.poll() is not None:
                break
            time.sleep(0.05)
        assert child.poll() is None
        assert len(files) == workers
        rows = [json.loads(p.read_text())["tuning"] for p in files]
        assert len({r["pid"] for r in rows}) == workers
        assert sum(r["connections_per_worker"] for r in rows) == 20
        for row in rows:
            os.kill(row["pid"], 0)
            assert row["run_id"] == nonce
            assert row["workers_total"] == workers
            assert row["hash_concurrency"] == slots
            assert row["hash_scope"] == "worker"
            assert (tmp_path / f"{row['pid']}.json").is_file()
        assert urllib.request.urlopen("http://127.0.0.1:8000/health/ready").status == 200
    finally:
        child.terminate()
        child.communicate(timeout=20)


@pytest.mark.parametrize("value", ["", "0", "3", "5", "1.0", "oops"])
def test_hash_environment_rejects_malformed_values(monkeypatch, value):
    from modern_api.settings import HashSettings

    monkeypatch.setenv("HASH_CONCURRENCY", value)
    with pytest.raises(ValueError):
        HashSettings()


@pytest.mark.parametrize("value", ["", "0", "3", "5", "4.0", "oops"])
def test_worker_environment_rejects_malformed_values(monkeypatch, value):
    monkeypatch.setenv("WORKERS", value)
    monkeypatch.delenv("HASH_CONCURRENCY", raising=False)
    with pytest.raises(ValueError):
        Settings(database_url="postgresql://localhost/local", jwt_secret="x" * 32)
