import psycopg
import pytest
from bench.fixture import capture_fixture, verify_fixture
from bench.reset import mark_database, restore_fixture


def test_reset_roundtrip_restores_changed_data_and_sequences(database, tmp_path, monkeypatch):
    import os

    container = os.environ.get("TEST_DB_CLIENT_CONTAINER", "lb-test-db")
    if os.environ.get("SKIP_DUMP_TEST"):
        pytest.skip("dump/restore smoke is verified separately with installed PostgreSQL clients")
    monkeypatch.setenv("DB_CLIENT_CONTAINER", container)
    mark_database(database, "language_benchmark_test")
    with psycopg.connect(database) as c:
        c.execute("INSERT INTO users(username,password_hash) VALUES ('alice','hash')")
        c.execute("INSERT INTO posts(author_id,content) VALUES (1,'original')")
    manifest = capture_fixture(database, tmp_path)
    with psycopg.connect(database) as c:
        c.execute("UPDATE posts SET content='changed'")
    assert verify_fixture(database, manifest)
    restore_fixture(database, tmp_path / "manifest.json", "language_benchmark_test")
    assert not verify_fixture(database, manifest)
    with psycopg.connect(database) as c:
        assert (
            c.execute(
                "INSERT INTO posts(author_id,content) VALUES (1,'after reset') RETURNING id"
            ).fetchone()[0]
            == 2
        )


def test_reset_refuses_unmarked_or_wrong_database(database, tmp_path):
    with pytest.raises(ValueError):
        mark_database(database, "production")
    with pytest.raises(ValueError):
        restore_fixture(database, tmp_path / "missing.json", "production")


def test_fixture_manifest_records_seed_parameters(database, tmp_path, monkeypatch):
    import os
    from dataclasses import asdict

    from bench.models import FixtureSpec
    from bench.seed import seed_database

    if os.environ.get("SKIP_DUMP_TEST"):
        pytest.skip("requires PostgreSQL clients")
    monkeypatch.setenv(
        "DB_CLIENT_CONTAINER", os.environ.get("TEST_DB_CLIENT_CONTAINER", "lb-test-db")
    )
    monkeypatch.setenv("SEED_PASSWORD", "valid-fixture-password")
    monkeypatch.setenv("SEED_HASH_WORKERS", "1")
    spec = FixtureSpec(users=2, posts=3, follows_per_user=1, likes=1, comments=1)
    mark_database(database, "language_benchmark_test")
    seed_database(database, spec)
    m = capture_fixture(database, tmp_path)
    assert m.spec == asdict(spec)


def test_native_clients_capture_and_restore_configured_database(database, tmp_path, monkeypatch):
    import shutil

    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        pytest.skip("native PostgreSQL clients unavailable")
    monkeypatch.delenv("DB_CLIENT_CONTAINER", raising=False)
    mark_database(database, "language_benchmark_test")
    with psycopg.connect(database) as c:
        c.execute("INSERT INTO users(username,password_hash) VALUES ('alice','hash')")
        c.execute("INSERT INTO posts(author_id,content) VALUES (1,'original')")
    m = capture_fixture(database, tmp_path)
    with psycopg.connect(database) as c:
        c.execute("UPDATE posts SET content='changed'")
    restore_fixture(database, tmp_path / "manifest.json", "language_benchmark_test")
    assert not verify_fixture(database, m)
