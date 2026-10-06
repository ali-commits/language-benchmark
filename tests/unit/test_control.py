import json
from types import SimpleNamespace

from bench.control import qualified_cache, retry_probe
from bench.images import ensure_backend_image


def test_backend_overload_is_a_result_and_does_not_trigger_recovery():
    calls = []
    result = retry_probe(
        lambda _: {"outcome": "fail"}, 10000, lambda: calls.append("recover"), sleep=lambda _: None
    )
    assert result["outcome"] == "fail" and not calls


def test_infrastructure_retries_are_bounded_and_each_attempt_is_preserved():
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "invalid", "trial_id": str(len(calls))}

    result = retry_probe(probe, 10000, lambda: None, sleep=lambda _: None)
    assert calls == [10000] * 3 and result["trial_id"] == "3"


def test_explicit_interrupt_is_never_retried():
    calls = []
    result = retry_probe(
        lambda _: {"outcome": "invalid", "lifecycle_status": "interrupted"},
        10000,
        lambda: calls.append("recover"),
        sleep=lambda _: None,
    )
    assert result["lifecycle_status"] == "interrupted" and not calls


def test_restart_cache_excludes_invalid_or_unrestored_trials(tmp_path):
    for n, outcome, restored in (
        (0, "pass", True),
        (1, "fail", True),
        (2, "invalid", True),
        (3, "pass", False),
    ):
        p = tmp_path / str(n)
        p.mkdir()
        (p / "capacity-outcome.json").write_text(
            json.dumps({"outcome": outcome, "trial_id": str(n)})
        )
        (p / "result.json").write_text(
            json.dumps(
                {
                    "status": "measured",
                    "reset_verified": restored,
                    "trial": {"cell": "python", "users": 2000},
                }
            )
        )
    assert [r["trial_id"] for r in qualified_cache(tmp_path)[("python", 2000)]] == ["0", "1"]


def test_existing_image_never_builds_or_redeploys(monkeypatch, tmp_path):
    from bench import images

    monkeypatch.setattr(images, "image_id", lambda *_: "sha256:existing")
    monkeypatch.setattr(
        images,
        "execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected build")),
    )
    ensure_backend_image({}, SimpleNamespace(image="locked"), tmp_path)


def test_supervisor_adopts_running_controller_and_exits_on_completion(tmp_path, monkeypatch):
    from bench import supervisor

    config = tmp_path / "config.json"
    config.write_text("{}")
    output = tmp_path / "results"
    output.mkdir()
    state = tmp_path / "supervisor.json"
    active = iter([True, False])
    monkeypatch.setattr(supervisor, "controller_active", lambda _: next(active))

    def sleep(_):
        (output / "capacity-status.json").write_text(
            json.dumps({"status": "complete_with_unstable_boundaries"})
        )

    supervisor.supervise(config, output, state, "old", sleep=sleep)
    assert json.loads(state.read_text())["status"] == "complete_with_unstable_boundaries"
    assert json.loads(state.read_text())["controller_attempts"] == 0


def test_supervisor_crash_budget_persists_across_restart(tmp_path, monkeypatch):
    from bench import supervisor

    config = tmp_path / "config.json"
    config.write_text("{}")
    output = tmp_path / "results"
    output.mkdir()
    state = tmp_path / "supervisor.json"
    state.write_text(json.dumps({"controller_attempts": 3}))
    monkeypatch.setattr(supervisor, "controller_active", lambda _: False)
    monkeypatch.setattr(
        supervisor,
        "recover_hosts",
        lambda _: (_ for _ in ()).throw(AssertionError("retry budget exhausted")),
    )
    supervisor.supervise(config, output, state, "old", sleep=lambda _: None)
    assert json.loads(state.read_text())["status"] == "needs_attention"


def test_new_image_builds_on_generator_and_digest_is_checked(tmp_path, monkeypatch):
    from bench import images

    backend = {"ssh": "backend", "project": "/srv/project"}
    build = {"ssh": "generator", "project": "/srv/project"}
    calls = []
    backend_ids = iter([None, "sha256:built"])
    build_ids = iter([None, "sha256:built"])
    monkeypatch.setattr(
        images, "image_id", lambda host, _: next(backend_ids if host == backend else build_ids)
    )

    def execute(host, argv, *args, **kwargs):
        calls.append((host, argv))
        return SimpleNamespace(stdout="build output", stderr="")

    monkeypatch.setattr(images, "execute", execute)
    monkeypatch.setattr(images, "copy_from", lambda host, source, dest: dest.write_text("archive"))
    monkeypatch.setattr(images, "copy_to", lambda *_: None)
    cell = SimpleNamespace(image="locked", id="cell", python="3.15.0rc3", build="jit")
    images.ensure_backend_image(
        {"backend_host": backend, "load_host": build, "auto_build_images": True}, cell, tmp_path
    )
    assert any(host == build and argv[:3] == ["uv", "run", "python"] for host, argv in calls)
    assert not any(host == backend and argv[:3] == ["uv", "run", "python"] for host, argv in calls)
    assert (tmp_path / "image-build.log").exists()


def test_changed_backend_cpu_allocation_blocks_measurement(monkeypatch):
    import pytest
    from bench import images

    outputs = iter(["container", "8000000000 3221225472 3221225472"])
    monkeypatch.setattr(
        images, "execute", lambda *_args, **_kwargs: SimpleNamespace(stdout=next(outputs))
    )
    with pytest.raises(RuntimeError, match="two CPUs"):
        images.verify_backend_limits({}, ["compose"], {})


def test_partial_runtime_replays_valid_probe_without_rerunning_it(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from bench import user_capacity as capacity

    config = {
        "mode": "local",
        "backend_nofile_limit": 250000,
        "cells": ["python"],
        "start_users": 10000,
        "step_users": 10000,
        "max_users": 50000,
        "resolution_users": 100,
        "ramp_seconds": 1,
        "settle_seconds": 1,
        "hold_seconds": 1,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    root = tmp_path / "results"
    p = root / "abc-0000"
    p.mkdir(parents=True)
    trial = {"id": "abc-0000", "campaign_id": "abc", "cell": "python", "users": 10000}
    (p / "manifest.json").write_text(
        json.dumps(
            {
                "campaign": config,
                "trial": trial,
                "nofile": {"soft": 250000, "hard": 250000, "process_samples": 2},
            }
        )
    )
    (p / "result.json").write_text(
        json.dumps({"status": "measured", "reset_verified": True, "trial": trial})
    )
    (p / "capacity-outcome.json").write_text(
        json.dumps({"outcome": "fail", "trial_id": "abc-0000"})
    )
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(capacity, "validate_config", lambda _: None)
    monkeypatch.setattr(capacity, "campaign_lock", lambda _: nullcontext())
    monkeypatch.setattr(capacity, "database_campaign_lock", lambda _: nullcontext())
    monkeypatch.setattr(
        capacity, "run_trial", lambda *_: (_ for _ in ()).throw(AssertionError("valid probe rerun"))
    )

    def search(probe, *args, **kwargs):
        reused = probe(10000)
        assert reused["reused_after_restart"] and reused["trial_id"] == "abc-0000"
        return {"status": "ceiling_reached", "passing_users": 50000}

    monkeypatch.setattr(capacity, "search_capacity", search)
    assert (
        capacity.run_user_capacity(path, root, resume=True)["python"]["status"] == "ceiling_reached"
    )


def test_supervisor_restarts_failed_controller_with_a_bounded_budget(tmp_path, monkeypatch):
    from bench import supervisor

    config = tmp_path / "config.json"
    config.write_text("{}")
    output = tmp_path / "results"
    output.mkdir()
    (output / "capacity-status.json").write_text(json.dumps({"status": "testing"}))
    state = tmp_path / "supervisor.json"
    calls = []
    monkeypatch.setattr(supervisor, "controller_active", lambda _: False)
    monkeypatch.setattr(supervisor, "recover_hosts", lambda _: None)

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(supervisor.subprocess, "run", run)
    supervisor.supervise(config, output, state, "benchmark-unit", sleep=lambda _: None)
    assert calls == [["sudo", "systemctl", "start", "benchmark-unit"]] * 3
    assert json.loads(state.read_text())["controller_attempts"] == 3
    assert json.loads(state.read_text())["status"] == "needs_attention"
