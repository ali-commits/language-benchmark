import os
from pathlib import Path

import psycopg
import pytest

DSN = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:benchmark@127.0.0.1:55432/language_benchmark_test"
)


@pytest.fixture
def database():
    from psycopg.conninfo import conninfo_to_dict

    name = conninfo_to_dict(DSN).get("dbname", "")
    if not name.startswith("language_benchmark") or not name.endswith("_test"):
        pytest.fail("contract tests require a dedicated language_benchmark*_test database")
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.execute(Path("database/schema.sql").read_text())
    return DSN


@pytest.fixture(
    params=["external"]
    if os.environ.get("TARGET_URL")
    else ["baseline_api.main"]
    if os.environ.get("BASELINE_ONLY")
    else ["baseline_api.main", "modern_api.main", "modern_api.threaded"]
)
def api_client(request, database, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-has-at-least-32-bytes")
    monkeypatch.setenv("WORKERS", "1")
    if request.param == "external":
        import httpx

        with httpx.Client(base_url=os.environ["TARGET_URL"], timeout=30) as client:
            yield client
        return
    module = importlib.import_module(request.param)
    with TestClient(module.create_app()) as c:
        yield c
