from pathlib import Path

import pytest

from tests.unit.test_queue_store import config
from tools.bench.engine.targets import ResolvedTarget
from tools.bench.engine.types import Evaluation, TrialEvidence
from tools.bench.queue import controller
from tools.bench.queue.store import QueueStore


@pytest.fixture
def harness(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    store = QueueStore(tmp_path / "state" / "queue.db")
    store.register(cfg)
    launches = []
    monkeypatch.setattr(
        controller,
        "resolve_target",
        lambda target, env, **kw: ResolvedTarget(target, target.image, "sha256:app", ()),
    )
    monkeypatch.setattr(
        controller,
        "_inspect_image",
        lambda host, image: image if image.startswith("sha256:") else "sha256:k6",
    )
    monkeypatch.setattr(controller, "_reserve_hosts", lambda *a: None)
    monkeypatch.setattr(controller, "_release_hosts", lambda *a: None)

    def run(ctx, stop_requested):
        launches.append(ctx)
        return TrialEvidence(
            trial_id=ctx.trial_id,
            users=ctx.users,
            execution_id=controller._identity(ctx),
            cleanup_verified=True,
        )

    monkeypatch.setattr(controller, "run_trial", run)
    monkeypatch.setattr(controller, "evaluate", lambda *a: Evaluation(verdict="pass"))
    return store, cfg, launches


def test_fixed_multiple_targets_repetitions(harness):
    store, cfg, launches = harness
    job = cfg.jobs[0].model_copy(
        update={
            "targets": ["app", "second"],
            "test": cfg.jobs[0].test.model_copy(update={"users": [1, 2], "repetitions": 2}),
        }
    )
    # A second target is retained in the registered config.
    other = QueueStore(store.path.parent / "other.db")
    other.register(
        cfg.model_copy(
            update={"targets": {**cfg.targets, "second": cfg.targets["app"]}, "jobs": [job]}
        )
    )
    result = controller.run_queue(other, "test")
    assert result["queues"][0]["state"] == "completed"
    assert [c.users for c in launches] == [1, 1, 2, 2] * 2
    assert len({str(c.prepared_workload.directory) for c in launches[:4]}) == 1


def test_stop_then_resume_pending_work(harness, monkeypatch):
    store, cfg, launches = harness
    original = controller.run_trial

    def stop(ctx, callback):
        value = original(ctx, callback)
        store.request_stop("test")
        return value.model_copy(update={"valid": False, "invalid_reasons": ("stop requested",)})

    monkeypatch.setattr(controller, "run_trial", stop)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "interrupted"
    assert store.jobs("test")[0].state == "active"
    store.clear_stop("test")
    monkeypatch.setattr(controller, "run_trial", original)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert len(launches) == 2


def test_finalized_result_recovered_without_repeat(harness, monkeypatch):
    store, cfg, launches = harness
    original = controller._decide
    monkeypatch.setattr(
        controller, "_decide", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        controller.run_queue(store, "test")
    monkeypatch.setattr(controller, "_decide", original)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert len(launches) == 1


def test_after_current_and_append(harness, monkeypatch):
    store, cfg, launches = harness
    store.append("test", cfg.jobs[0].model_copy(update={"id": "two"}))
    original = controller.run_trial

    def stop(ctx, callback):
        result = original(ctx, callback)
        store.request_stop("test", after_current=True)
        return result

    monkeypatch.setattr(controller, "run_trial", stop)
    controller.run_queue(store, "test")
    assert [j.state for j in store.jobs("test")] == ["completed", "pending"]
    store.clear_stop("test")
    monkeypatch.setattr(controller, "run_trial", original)
    controller.run_queue(store, "test")
    store.append("test", cfg.jobs[0].model_copy(update={"id": "three"}))
    assert len(launches) == 2
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert len(launches) == 3


def search_store(harness, max_trials=3):
    from tools.bench.engine.types import TestPolicy

    store, cfg, launches = harness
    policy = TestPolicy.model_validate(
        {
            "type": "search",
            "users": {"min": 1, "start": 1, "max": 2},
            "limits": {"max_trials": max_trials, "max_elapsed": 100},
            "confirmation": "single_observation",
        }
    )
    store.update("test", "one", cfg.jobs[0].model_copy(update={"test": policy}), 1)
    return store, launches


def test_search_reservation_precedes_launch_and_recovery(harness, monkeypatch):
    store, launches = search_store(harness)
    original_run = controller.run_trial

    def verify(ctx, callback):
        data = store.controller_state("test")["jobs"]["one"]
        assert data["search"]["pending_trial_id"] == ctx.trial_id
        assert data["search"]["budget"]["attempts"] == len(launches) + 1
        return original_run(ctx, callback)

    monkeypatch.setattr(controller, "run_trial", verify)
    original_decide = controller._decide
    monkeypatch.setattr(
        controller, "_decide", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        controller.run_queue(store, "test")
    monkeypatch.setattr(controller, "_decide", original_decide)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert [c.users for c in launches] == [1, 2]
    data = store.controller_state("test")["jobs"]["one"]
    assert data["results"][0]["status"] == "lower_bound"
    assert data["results"][0]["attempts"] == 2


def test_search_exhausted_recovery_launches_nothing(harness, monkeypatch):
    store, launches = search_store(harness, 1)
    original = controller._decide
    monkeypatch.setattr(
        controller, "_decide", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        controller.run_queue(store, "test")
    monkeypatch.setattr(controller, "_decide", original)
    controller.run_queue(store, "test")
    assert len(launches) == 1
    assert store.controller_state("test")["jobs"]["one"]["results"][0]["status"] == "incomplete"


def test_missing_pinned_image_blocks_resume(harness, monkeypatch):
    store, cfg, launches = harness
    original = controller._decide
    monkeypatch.setattr(
        controller, "_decide", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        controller.run_queue(store, "test")
    monkeypatch.setattr(controller, "_decide", original)
    monkeypatch.setattr(
        controller, "_inspect_image", lambda *a: (_ for _ in ()).throw(ValueError("missing"))
    )
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "needs_attention"
    assert len(launches) == 1


def test_changed_identity_between_decided_trials_blocks_resume(harness, monkeypatch):
    store, cfg, launches = harness
    store.update(
        "test",
        "one",
        cfg.jobs[0].model_copy(
            update={"test": cfg.jobs[0].test.model_copy(update={"users": [1, 2]})}
        ),
        1,
    )
    original = controller.run_trial

    def stopping(ctx, callback):
        value = original(ctx, callback)
        store.request_stop("test")
        return value

    monkeypatch.setattr(controller, "run_trial", stopping)
    controller.run_queue(store, "test")
    store.clear_stop("test")
    monkeypatch.setattr(controller, "run_trial", original)
    monkeypatch.setattr(controller, "prepared_execution_identity", lambda *a, **kw: "changed")
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "needs_attention"
    assert len(launches) == 1


def test_stop_during_prepare_retains_pin(harness, monkeypatch):
    store, cfg, launches = harness
    original = controller.resolve_target
    prepared = []

    def stopping(*args, **kwargs):
        prepared.append(True)
        value = original(*args, **kwargs)
        store.request_stop("test")
        return value

    monkeypatch.setattr(controller, "resolve_target", stopping)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "interrupted"
    assert not launches
    store.clear_stop("test")
    monkeypatch.setattr(
        controller, "resolve_target", lambda *a, **kw: pytest.fail("must retain prepared image")
    )
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert len(prepared) == 1


def test_search_objectives_share_budget(harness):
    store, launches = search_store(harness, 2)
    original = store.jobs("test")[0]
    from tools.bench.engine.types import Job

    row = store.snapshot("test")["jobs"][0]
    job = Job.model_validate_json(row["definition"]).model_copy(
        update={"objectives": {"first": [], "second": []}}
    )
    store.update("test", "one", job, original.revision)
    controller.run_queue(store, "test")
    results = store.controller_state("test")["jobs"]["one"]["results"]
    assert [r["objective"] for r in results] == ["first", "second"]
    assert results[1]["status"] == "incomplete"
    assert results[1]["observations"] == []
    assert len(launches) == 2


def test_cleanup_failure_blocks_next_dispatch(harness, monkeypatch):
    store, cfg, launches = harness
    store.append("test", cfg.jobs[0].model_copy(update={"id": "two"}))
    original = controller.run_trial

    def dirty(ctx, callback):
        value = original(ctx, callback)
        ctx.directory.mkdir(parents=True, exist_ok=True)
        (ctx.directory / "manifest.json").write_text("{}")
        return value.model_copy(update={"cleanup_verified": False})

    monkeypatch.setattr(controller, "run_trial", dirty)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "needs_attention"
    assert [j.state for j in store.jobs("test")] == ["active", "pending"]
    assert len(launches) == 1


def test_foreground_without_systemd(harness, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a: None)
    assert controller.run_queue(harness[0], "test")["queues"][0]["state"] == "completed"


@pytest.mark.skipif(
    __import__("os").getenv("BENCH_INTEGRATION") != "1",
    reason="opt-in disposable local Docker queue",
)
def test_real_fixed_and_search_queue(tmp_path):
    from tools.bench.engine.config import load_config
    from tools.bench.engine.types import OccupancyTolerance, Stage, Telemetry, TestPolicy

    cfg = load_config(Path(__file__).resolve().parents[2] / "examples/http/queue.yaml")
    defaults = cfg.defaults.model_copy(
        update={
            "stages": [
                Stage(
                    id="hold", from_users="target", to_users="target", duration=3.0, evaluate=True
                )
            ],
            "drain": 0.2,
            "graceful_ramp_down": 0.2,
            "hold_tolerance": OccupancyTolerance(grace=1.1),
        }
    )
    env = cfg.environments["local"].model_copy(update={"telemetry": Telemetry(interval=0.2)})
    fixed = cfg.jobs[0].model_copy(update={"test": TestPolicy(type="fixed", users=[1])})
    search = fixed.model_copy(
        update={
            "id": "search",
            "test": TestPolicy.model_validate(
                {
                    "type": "search",
                    "users": {"min": 1, "start": 1, "max": 2},
                    "limits": {"max_trials": 2, "max_elapsed": 120},
                    "confirmation": "single_observation",
                }
            ),
        }
    )
    cfg = cfg.model_copy(
        update={
            "name": "queue-smoke-" + tmp_path.name,
            "defaults": defaults,
            "environments": {"local": env},
            "jobs": [fixed, search],
        }
    )
    store = QueueStore(tmp_path / "queue.db")
    store.register(cfg)
    snapshot = controller.run_queue(store, cfg.name)
    checkpoint = store.controller_state(cfg.name)
    assert snapshot["queues"][0]["state"] == "completed", checkpoint
    assert [j.state for j in store.jobs(cfg.name)] == ["completed", "completed"]
    assert len(snapshot["trials"]) == 3
    assert checkpoint["leases"] == {}
    for row in snapshot["trials"]:
        evidence = TrialEvidence.model_validate_json(
            (Path(row["path"]) / "result.json").read_text()
        )
        assert evidence.cleanup_verified and evidence.valid, evidence.invalid_reasons


def test_valid_criterion_failure_continues_fixed_jobs(harness, monkeypatch):
    store, cfg, launches = harness
    store.append("test", cfg.jobs[0].model_copy(update={"id": "two"}))
    monkeypatch.setattr(controller, "evaluate", lambda *args: Evaluation(verdict="fail"))
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert len(launches) == 2


def test_cleanup_stop_does_not_start_next_job(harness, monkeypatch):
    store, cfg, launches = harness
    store.append("test", cfg.jobs[0].model_copy(update={"id": "two"}))
    original = controller.run_trial

    def cleanup(ctx, callback):
        value = original(ctx, callback)
        store.request_stop("test")
        assert callback()
        assert store.snapshot("test")["queues"][0]["state"] == "stopping"
        return value

    monkeypatch.setattr(controller, "run_trial", cleanup)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "interrupted"
    assert [j.state for j in store.jobs("test")] == ["completed", "pending"]
    assert len(launches) == 1


def test_repeated_preparation_failures_have_durable_budget(harness, monkeypatch):
    store, cfg, launches = harness
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("secret should not be saved")

    monkeypatch.setattr(controller, "resolve_target", fail)
    for _ in range(5):
        assert (
            controller.run_queue(QueueStore(store.path), "test")["queues"][0]["state"]
            == "needs_attention"
        )
    assert len(calls) == controller.MAX_RECOVERY_ATTEMPTS
    assert "secret" not in str(store.controller_state("test"))


def test_clock_rollback_blocks_new_probe(harness, monkeypatch):
    store, launches = search_store(harness)
    original = controller.run_trial

    def stop(ctx, callback):
        result = original(ctx, callback)
        store.request_stop("test")
        return result

    monkeypatch.setattr(controller, "run_trial", stop)
    controller.run_queue(store, "test")
    saved = store.controller_state("test")["jobs"]["one"]["budget"]["now"]
    monkeypatch.setattr(controller.time, "time", lambda: saved - 100)
    monkeypatch.setattr(controller, "run_trial", original)
    store.clear_stop("test")
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "needs_attention"
    assert len(launches) == 1


def test_crash_at_trial_status_checkpoint_recovers_reference(harness, monkeypatch):
    store, cfg, launches = harness
    original = store.record_trial

    def crash(*args, **kwargs):
        if kwargs.get("state") == "finalized":
            raise KeyboardInterrupt()
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "record_trial", crash)
    with pytest.raises(KeyboardInterrupt):
        controller.run_queue(store, "test")
    monkeypatch.setattr(store, "record_trial", original)
    result = controller.run_queue(store, "test")
    assert result["trials"][0]["state"] == "finalized"
    assert len(launches) == 1
