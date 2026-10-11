"""Production settings are explicit and historical pool defaults remain unchanged."""

import pytest
from modern_api.db import make_pool
from modern_api.settings import Settings

BASE = dict(database_url="postgresql://localhost/local", jwt_secret="x" * 32)


def test_production_pool_and_historical_defaults():
    for workers in (2, 4):
        cfg = Settings(**BASE, workers=workers)
        assert make_pool(cfg).max_size == 20 // workers
    cfg = Settings(**BASE, workers=4, db_pool_max_size=10, db_pool_min_size=2)
    pool = make_pool(cfg)
    assert (pool.min_size, pool.max_size) == (2, 10)


@pytest.mark.parametrize("minimum,maximum", [(0, 10), (11, 10), (2, 0), (2, 101)])
def test_invalid_pool_limits(minimum, maximum):
    with pytest.raises(ValueError):
        Settings(**BASE, db_pool_min_size=minimum, db_pool_max_size=maximum)


def test_uvicorn_options_preserve_legacy_and_select_accelerators():
    import runpy

    options = runpy.run_path("backends/python/entrypoint.py")["server_options"]
    assert options({}) == {"loop": "asyncio", "http": "h11"}
    assert options({"UVICORN_LOOP": "uvloop", "UVICORN_HTTP": "httptools"}) == {
        "loop": "uvloop",
        "http": "httptools",
    }
    for env in ({"UVICORN_LOOP": "auto"}, {"UVICORN_HTTP": "invalid"}):
        with pytest.raises(ValueError):
            options(env)


@pytest.mark.parametrize("value", ["", "0", "10.0", "-1", "oops"])
def test_invalid_pool_environment(monkeypatch, value):
    monkeypatch.setenv("DB_POOL_MAX_SIZE", value)
    with pytest.raises(ValueError):
        Settings(**BASE)


def test_production_lock_preserves_all_existing_packages():
    from pathlib import Path

    import tomllib

    baseline = tomllib.loads(Path("backends/python/modern/uv.lock").read_text())
    production = tomllib.loads(Path("backends/python/production/uv.lock").read_text())
    existing = {p["name"]: p["version"] for p in baseline["package"] if p["name"] != "modern-api"}
    selected = {p["name"]: p["version"] for p in production["package"]}
    assert {name: selected[name] for name in existing} == existing
    assert selected["uvloop"] == "0.22.1"
    assert selected["httptools"] == "0.7.1"
