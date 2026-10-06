"""Stable-interpreter reference preserves the historical experimental matrix."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import tomllib
from bench.models import RuntimeCell
from bench.runtime import validate_worker_snapshots
from packaging.specifiers import SpecifierSet

REFERENCE = "python-modern-3.14.8-standard-jit0-gil1-async"


def test_reference_is_modern_stable_standard_async_with_fixed_budgets():
    cells = json.loads(Path("runtimes/python/lock.json").read_text())["cells"]
    selected = [c for c in cells if c["id"] == REFERENCE]
    assert len(selected) == 1
    c = RuntimeCell(**selected[0])
    assert (c.python, c.app, c.build, c.adapter, c.workers, c.pool_budget) == (
        "3.14.8",
        "modern",
        "standard",
        "async",
        2,
        20,
    )
    assert c.jit is False and c.gil is True
    assert c.image == "lb-python-modern:3.14.8-standard"
    assert c.dependency_lock == "backends/python/modern/requirements.lock"
    snapshots = [
        dict(
            pid=pid,
            run_id="fresh",
            python="3.14.8",
            gil=True,
            free_threaded=False,
            jit=False,
            jit_available=False,
            config_args="--enable-optimizations --with-lto",
        )
        for pid in (10, 11)
    ]
    assert validate_worker_snapshots(c, snapshots) == []


def test_modern_project_and_uv_lock_allow_stable_reference_without_changing_beta_pins():
    project = tomllib.loads(Path("backends/python/modern/pyproject.toml").read_text())
    lock = tomllib.loads(Path("backends/python/modern/uv.lock").read_text())
    assert "3.14.8" in SpecifierSet(project["project"]["requires-python"])
    assert "3.14.8" in SpecifierSet(lock["requires-python"])
    assert SpecifierSet(project["project"]["requires-python"]).contains(
        "3.15.0rc3", prereleases=True
    )
    assert "pydantic==2.14.0b2" in project["project"]["dependencies"]


def test_apps_build_includes_314_modern_without_enabling_experimental_builds(monkeypatch):
    commands = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: commands.append(argv))
    monkeypatch.setattr(sys, "argv", ["build.py", "--version", "3.14.8", "--apps"])
    runpy.run_path("runtimes/python/build.py", run_name="__main__")
    assert any("lb-python-baseline:3.14.8" in c for c in commands)
    assert any("lb-python-modern:3.14.8-standard" in c for c in commands)
    commands.clear()
    monkeypatch.setattr(sys, "argv", ["build.py", "--version", "3.14.8", "--apps", "--mode", "jit"])
    runpy.run_path("runtimes/python/build.py", run_name="__main__")
    assert commands == []
