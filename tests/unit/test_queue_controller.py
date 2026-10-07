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
    assert results[1]["status"] == "lower_bound"
    assert results[1]["observed_only"] is True
    assert all(o["reused"] for o in results[1]["observations"])
    assert results[1]["attempts"] == 2
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
            "objectives": {"measured": [], "reanalysis": []},
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
    results = checkpoint["jobs"]["search"]["results"]
    assert [r["observed_only"] for r in results] == [False, True]
    assert all(o["reused"] for o in results[1]["observations"])
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


@pytest.mark.parametrize("search", [False, True])
def test_cleanup_verified_invalid_retries_automatically(harness, monkeypatch, search):
    store, cfg, launches = harness
    if search:
        store, launches = search_store(harness, 3)
    original = controller.run_trial

    def transient(ctx, callback):
        value = original(ctx, callback)
        if len(launches) == 1:
            return value.model_copy(
                update={"valid": False, "invalid_reasons": ("temporary collector failure",)}
            )
        return value

    monkeypatch.setattr(controller, "run_trial", transient)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert [c.users for c in launches] == ([1, 1, 2] if search else [1, 1])
    data = store.controller_state("test")["jobs"]["one"]
    assert [d["evaluation"]["verdict"] for d in data["decisions"]][:2] == ["invalid", "pass"]
    if search:
        assert data["budget"]["attempts"] == 3


def test_invalid_retry_exhaustion_survives_restart(harness, monkeypatch):
    store, cfg, launches = harness
    original = controller.run_trial

    def invalid(ctx, callback):
        return original(ctx, callback).model_copy(
            update={"valid": False, "invalid_reasons": ("temporary failure",)}
        )

    monkeypatch.setattr(controller, "run_trial", invalid)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "needs_attention"
    assert len(launches) == 2
    assert (
        controller.run_queue(QueueStore(store.path), "test")["queues"][0]["state"]
        == "needs_attention"
    )
    assert len(launches) == 2


def test_shutdown_interruption_resumes_without_durable_stop(harness, monkeypatch):
    store, cfg, launches = harness
    interrupted = False
    original = controller.run_trial

    def shutdown(ctx, callback):
        nonlocal interrupted
        value = original(ctx, callback)
        interrupted = True
        assert callback()
        return value.model_copy(update={"valid": False, "invalid_reasons": ("stop requested",)})

    monkeypatch.setattr(controller, "run_trial", shutdown)
    assert (
        controller.run_queue(store, "test", interrupt_requested=lambda: interrupted)["queues"][0][
            "state"
        ]
        == "interrupted"
    )
    assert store.stop_request("test") is None
    assert len(launches) == 1
    monkeypatch.setattr(controller, "run_trial", original)
    assert controller.run_queue(QueueStore(store.path), "test")["queues"][0]["state"] == "completed"
    assert len(launches) == 2


def test_reused_probes_fresh_objective_evaluation(harness, monkeypatch):
    from tools.bench.engine.types import Criterion, Job, ResolvedStage, Sample

    store, launches = search_store(harness, 4)
    row = store.snapshot("test")["jobs"][0]
    first = Criterion(
        metric="cpu_used", scope="backend_host", statistic="mean", operator="<=", value=1.0
    )
    second = first.model_copy(update={"value": 0.1})
    job = Job.model_validate_json(row["definition"]).model_copy(
        update={"objectives": {"lenient": [first], "strict": [second]}}
    )
    store.update("test", "one", job, store.jobs("test")[0].revision)
    original = controller.run_trial

    def measured(ctx, callback):
        value = original(ctx, callback)
        return value.model_copy(
            update={
                "stages": (
                    ResolvedStage(
                        id="hold",
                        from_users=ctx.users,
                        to_users=ctx.users,
                        duration=2.0,
                        evaluate=True,
                        start_seconds=0.0,
                        end_seconds=2.0,
                    ),
                ),
                "samples": (
                    Sample(
                        metric="cpu_used",
                        scope="backend_host",
                        kind="interval",
                        timestamp=0.0,
                        end_seconds=2.0,
                        value=0.5,
                        normalization="all_host_cpus",
                    ),
                ),
            }
        )

    monkeypatch.setattr(controller, "run_trial", measured)
    from tools.bench.engine.metrics import evaluate

    monkeypatch.setattr(controller, "evaluate", evaluate)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    data = store.controller_state("test")["jobs"]["one"]
    assert len(launches) == 2
    assert data["results"][0]["passing_users"] == 2
    assert data["results"][1]["status"] == "below_minimum"
    assert data["results"][1]["observed_only"] is True
    assert all(o["reused"] for o in data["results"][1]["observations"])
    assert {o["trial_id"] for o in data["results"][1]["observations"]} == {
        c.trial_id for c in launches
    }
    assert all(o["evaluation"]["verdict"] == "fail" for o in data["results"][1]["observations"])


def test_invalid_retry_checkpoint_survives_crash(harness, monkeypatch):
    store, cfg, launches = harness
    original_run = controller.run_trial
    monkeypatch.setattr(
        controller,
        "run_trial",
        lambda ctx, callback: original_run(ctx, callback).model_copy(
            update={"valid": False, "invalid_reasons": ("collector",)}
        ),
    )
    original_decide = controller._decide

    def crash(*args):
        original_decide(*args)
        raise KeyboardInterrupt()

    monkeypatch.setattr(controller, "_decide", crash)
    with pytest.raises(KeyboardInterrupt):
        controller.run_queue(store, "test")
    assert len(launches) == 1
    monkeypatch.setattr(controller, "_decide", original_decide)
    assert (
        controller.run_queue(QueueStore(store.path), "test")["queues"][0]["state"]
        == "needs_attention"
    )
    assert len(launches) == 2


def test_reuse_majority_history_never_adds_decided_vote(harness, monkeypatch):
    from tools.bench.engine.types import Criterion, Job, ResolvedStage, Sample

    store, launches = search_store(harness, 6)
    row = store.snapshot("test")["jobs"][0]
    job = Job.model_validate_json(row["definition"])
    criterion = Criterion(
        metric="cpu_used", scope="backend_host", statistic="mean", operator="<=", value=1.0
    )
    policy = job.test.model_copy(
        update={
            "users": job.test.users.model_copy(update={"max": 1}),
            "confirmation": "majority_on_disagreement",
        }
    )
    job = job.model_copy(
        update={
            "test": policy,
            "objectives": {
                "first": [criterion],
                "second": [criterion.model_copy(update={"value": 2.0})],
                "third": [criterion.model_copy(update={"value": 2.0})],
            },
        }
    )
    store.update("test", "one", job, store.jobs("test")[0].revision)
    original = controller.run_trial

    def measured(ctx, callback):
        value = original(ctx, callback)
        return value.model_copy(
            update={
                "stages": (
                    ResolvedStage(
                        id="hold",
                        from_users=1,
                        to_users=1,
                        duration=2.0,
                        evaluate=True,
                        start_seconds=0.0,
                        end_seconds=2.0,
                    ),
                ),
                "samples": (
                    Sample(
                        metric="cpu_used",
                        scope="backend_host",
                        kind="interval",
                        timestamp=0.0,
                        end_seconds=2.0,
                        value=1.5 if len(launches) == 1 else 0.5,
                        normalization="all_host_cpus",
                    ),
                ),
            }
        )

    monkeypatch.setattr(controller, "run_trial", measured)
    from tools.bench.engine.metrics import evaluate

    monkeypatch.setattr(controller, "evaluate", evaluate)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    data = store.controller_state("test")["jobs"]["one"]
    assert len(launches) == 3
    assert [len(r["observations"]) for r in data["results"]] == [3, 2, 2]
    for result in data["results"][1:]:
        assert result["observed_only"]
        assert len({o["trial_id"] for o in result["observations"]}) == 2
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert len(launches) == 3


def test_invalid_retry_allowance_resets_after_valid_vote(harness, monkeypatch):
    from tools.bench.engine.types import Job

    store, launches = search_store(harness, 5)
    row = store.snapshot("test")["jobs"][0]
    job = Job.model_validate_json(row["definition"])
    policy = job.test.model_copy(
        update={
            "users": job.test.users.model_copy(update={"max": 1}),
            "confirmation": "majority_on_disagreement",
        }
    )
    store.update(
        "test", "one", job.model_copy(update={"test": policy}), store.jobs("test")[0].revision
    )
    original = controller.run_trial

    def alternating(ctx, callback):
        value = original(ctx, callback)
        return value.model_copy(update={"valid": len(launches) % 2 == 0})

    monkeypatch.setattr(controller, "run_trial", alternating)
    monkeypatch.setattr(controller, "evaluate", lambda *args: Evaluation(verdict="fail"))
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "completed"
    assert len(launches) == 4


def test_service_shutdown_and_fresh_worker_continue_job(harness, monkeypatch):
    import signal

    from tools.bench.queue import services

    store, cfg, launches = harness
    handlers = {}

    def register(sig, handler):
        previous = handlers.get(sig, signal.SIG_DFL)
        handlers[sig] = handler
        return previous

    monkeypatch.setattr(services.signal, "signal", register)
    commands = []
    monkeypatch.setattr(services, "_command", lambda args: commands.append(args))
    original = controller.run_trial

    def shutdown(ctx, callback):
        value = original(ctx, callback)
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert callback()
        return value.model_copy(update={"valid": False, "invalid_reasons": ("stop requested",)})

    monkeypatch.setattr(controller, "run_trial", shutdown)
    assert services._run("test", store.path.parent) == 0
    assert store.stop_request("test") is None
    assert store.jobs("test")[0].state == "active"
    assert not any("disable" in command for command in commands)
    assert store.controller_state("test")["jobs"]["one"]["invalid_retries"] == {}
    monkeypatch.setattr(controller, "run_trial", original)
    assert services._run("test", store.path.parent) == 0
    assert store.jobs("test")[0].state == "completed"
    assert len(launches) == 2


def test_reuse_checkpoint_restart_is_idempotent(harness, monkeypatch):
    from tools.bench.engine.types import Job

    store, launches = search_store(harness, 4)
    row = store.snapshot("test")["jobs"][0]
    job = Job.model_validate_json(row["definition"]).model_copy(
        update={"objectives": {"first": [], "second": []}}
    )
    store.update("test", "one", job, store.jobs("test")[0].revision)
    original = controller._save
    crashed = False

    def crash(store, queue, state, status=None):
        nonlocal crashed
        original(store, queue, state, status)
        data = state["jobs"].get("one", {})
        observations = (data.get("search") or {}).get("observations", [])
        if (
            not crashed
            and data.get("objective") == 1
            and observations
            and all(o["reused"] for o in observations)
        ):
            crashed = True
            raise KeyboardInterrupt()

    monkeypatch.setattr(controller, "_save", crash)
    with pytest.raises(KeyboardInterrupt):
        controller.run_queue(store, "test")
    monkeypatch.setattr(controller, "_save", original)
    assert controller.run_queue(QueueStore(store.path), "test")["queues"][0]["state"] == "completed"
    decisions = store.controller_state("test")["jobs"]["one"]["decisions"]
    assert len(decisions) == len({(d["objective"], d["trial_id"]) for d in decisions}) == 4
    assert len(launches) == 2


def test_reuse_rejects_incompatible_raw_identity(harness, tmp_path):
    from tools.bench.engine.evidence import immutable_json
    from tools.bench.engine.search import SearchPolicy, SearchState

    store, launches = search_store(harness)
    controller.run_queue(store, "test")
    context = launches[0]
    directory = tmp_path / "foreign"
    immutable_json(
        directory / "result.json",
        TrialEvidence(
            trial_id="foreign", users=1, execution_id="different-binary", cleanup_verified=True
        ).model_dump(mode="json"),
    )
    data = {
        "target": 0,
        "decisions": [],
        "raw_trials": {"foreign": {"target": 0, "path": str(directory), "objective": "old"}},
    }
    search = SearchState(policy=SearchPolicy.from_test(context.job.test))
    result, decisions = controller._reuse_probes(
        store,
        "test",
        data,
        context.job,
        context.prepared_target,
        context.prepared_workload,
        context.generator_image_id,
        "default",
        search,
    )
    assert result.observations == ()
    assert result.budget.attempts == 0
    assert decisions == []


@pytest.mark.parametrize("source", ["script", "dependency", "fixture"])
def test_source_change_after_claim_never_launches(harness, monkeypatch, source):
    from tools.bench.engine.types import Fixture

    store, cfg, launches = harness
    script = cfg.workloads["load"].script
    dependency = script.parent / "actors.json"
    dependency.write_text('{"actors": []}')
    manifest = script.parent / "fixture.json"
    manifest.write_text('{"identity": "original"}')
    workload = cfg.workloads["load"].model_copy(
        update={"dependencies": [dependency], "fixture": Fixture(manifest=manifest)}
    )
    other = QueueStore(store.path.parent / "frozen.db")
    other.register(cfg.model_copy(update={"workloads": {"load": workload}}))
    original = other.claim_next

    def claim_then_change(queue, owner):
        claimed = original(queue, owner)
        path = {"script": script, "dependency": dependency, "fixture": manifest}[source]
        path.write_text("export default function() { return 2; }" if source == "script" else "{}")
        return claimed

    monkeypatch.setattr(other, "claim_next", claim_then_change)
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "needs_attention"
    assert launches == []
    assert "frozen input" in other.controller_state("test")["last_error"]


def test_restart_before_preparation_rejects_changed_source(harness):
    store, cfg, launches = harness
    state = controller._open_state(store, "test")
    assert store.claim_next("test", state["owner"]) is not None
    cfg.workloads["load"].script.write_text("export default function() { return 2; }")
    assert (
        controller.run_queue(QueueStore(store.path), "test")["queues"][0]["state"]
        == "needs_attention"
    )
    assert launches == []


def test_workload_copy_checked_against_enqueued_bytes(harness, monkeypatch):
    from tools.bench.queue import preparation

    store, cfg, launches = harness
    original = preparation.prepare_workload

    def change_during_copy(workload, destination):
        # A live check immediately before copying cannot close this window.
        workload.script.write_text("export default function() { return 2; }")
        return original(workload, destination)

    monkeypatch.setattr(preparation, "prepare_workload", change_during_copy)
    assert controller.run_queue(store, "test")["queues"][0]["state"] == "needs_attention"
    assert launches == []


@pytest.mark.skipif(
    __import__("os").environ.get("BENCH_INTEGRATION") != "1", reason="native BuildKit required"
)
@pytest.mark.parametrize("changed", ["context", "dockerfile", "ignore"])
def test_build_changed_after_claim_is_rejected_before_image_build(harness, monkeypatch, changed):
    from tools.bench.engine import targets
    from tools.bench.engine.types import Build, Target

    store, cfg, launches = harness
    source = store.path.parent.parent / "build-source"
    source.mkdir()
    dockerfile = source / "Dockerfile"
    dockerfile.write_text("FROM scratch\nCOPY payload /payload\n")
    payload = source / "payload"
    payload.write_text("original")
    ignore = source / ".dockerignore"
    ignore.write_text("ignored\n")
    other = QueueStore(store.path.parent / "build-frozen.db")
    other.register(cfg.model_copy(update={"targets": {"app": Target(build=Build(context=source))}}))
    claim = other.claim_next
    builds = []
    execute = targets.execute_host

    def change(queue, owner):
        claimed = claim(queue, owner)
        if changed == "context":
            payload.write_text("changed")
        elif changed == "dockerfile":
            dockerfile.write_text("FROM scratch\nCOPY payload /different\n")
        else:
            ignore.write_text("another-nonmatching-pattern\n")
        return claimed

    def observe(host, argv, **kwargs):
        if argv[:2] == ["docker", "build"]:
            builds.append(argv)
            raise RuntimeError("image build must not be reached")
        return execute(host, argv, **kwargs)

    monkeypatch.setattr(other, "claim_next", change)
    monkeypatch.setattr(controller, "resolve_target", targets.resolve_target)
    monkeypatch.setattr(targets, "execute_host", observe)
    result = controller.run_queue(other, "test")
    assert result["queues"][0]["state"] == "needs_attention"
    assert builds == []
    assert launches == []
    assert "frozen input" in other.controller_state("test")["last_error"]


def test_fixture_pin_survives_live_edit_and_manifest_recovery(harness, monkeypatch):
    import hashlib
    import json

    from tools.bench.engine import trial_preparation, trial_recovery
    from tools.bench.engine.trial import TrialContext
    from tools.bench.engine.types import Fixture

    store, cfg, launches = harness
    manifest = cfg.workloads["load"].script.parent / "fixture.json"
    original_bytes = b'{"identity":"enqueued fixture"}'
    manifest.write_bytes(original_bytes)
    workload = cfg.workloads["load"].model_copy(update={"fixture": Fixture(manifest=manifest)})
    other = QueueStore(store.path.parent / "fixture-pinned.db")
    other.register(cfg.model_copy(update={"workloads": {"load": workload}}))

    def run(context, stop_requested):
        # Preparation has completed. Neither execution nor recovery may consume this edit.
        identity = controller._identity(context)
        manifest.write_text('{"identity":"different fixture"}')
        context.directory.mkdir(parents=True)
        prepared = trial_preparation.prepare_trial(
            context, execute=lambda *a, **kw: pytest.fail("unexpected Docker call")
        )
        assert prepared.execution_id == identity
        saved = json.loads((context.directory / "manifest.json").read_text())
        assert saved["job"]["workload"]["fixture"]["manifest"] == str(manifest)
        assert saved["fixture_manifest_sha256"] == hashlib.sha256(original_bytes).hexdigest()
        assert context.fixture_workload.fixture.manifest.read_bytes() == original_bytes
        trial_recovery.fixture(context, "reset")
        recovered = TrialContext.from_manifest(context.directory / "manifest.json")
        assert recovered.fixture_workload.fixture.manifest.read_bytes() == original_bytes
        trial_recovery.fixture(recovered, "verify")
        launches.append(context)
        return TrialEvidence(
            trial_id=context.trial_id,
            users=context.users,
            execution_id=identity,
            cleanup_verified=True,
        )

    monkeypatch.setattr(controller, "run_trial", run)
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "completed"
    assert len(launches) == 1


def test_frozen_fixture_archive_used_for_restore_and_old_identity(tmp_path, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace

    from tools.bench.engine import social, trial_recovery
    from tools.bench.engine.config import resolve_jobs
    from tools.bench.engine.evidence import immutable_json
    from tools.bench.engine.targets import prepared_execution_identity
    from tools.bench.engine.trial import TrialContext
    from tools.bench.engine.types import Fixture
    from tools.bench.engine.workloads import prepare_workload
    from tools.bench.queue.inputs import freeze_fixture, prepared_fixture
    from tools.bench.queue.store import _file_identity

    cfg = config(tmp_path)
    archive = tmp_path / "snapshot.dump"
    archive.write_bytes(b"enqueued archive")
    manifest = tmp_path / "fixture.json"
    manifest.write_text(
        json.dumps(
            {"dump": archive.name, "checksum": hashlib.sha256(archive.read_bytes()).hexdigest()}
        )
    )
    workload = cfg.workloads["load"].model_copy(
        update={"adapter": "social", "fixture": Fixture(adapter="social", manifest=manifest)}
    )
    job = resolve_jobs(cfg.model_copy(update={"workloads": {"load": workload}}))[0]
    work = prepare_workload(workload, tmp_path / "bundle")
    target = ResolvedTarget(job.target, job.target.image, "sha256:app", ())
    old_identity = prepared_execution_identity(job, 1, target, work, generator_image_id="sha256:k6")
    pin = freeze_fixture(workload, tmp_path / "pinned", {str(manifest): _file_identity(manifest)})
    fixture = prepared_fixture({"fixture": pin})
    archive.write_bytes(b"different archive")
    manifest.write_text("{}")
    context = TrialContext(
        job, 1, tmp_path / "trial", "test", "one", target, work, "sha256:k6", fixture
    )
    assert controller._identity(context) == old_identity
    immutable_json(
        context.directory / "manifest.json",
        {
            "job": job.model_dump(mode="json"),
            "queue_id": "test",
            "trial_id": "one",
            "users": 1,
            "fixture_manifest_sha256": hashlib.sha256(fixture.manifest.read_bytes()).hexdigest(),
            "prepared_fixture": pin,
        },
    )
    restored = []

    def adapter(spec):
        def reset(context):
            doc = json.loads(spec.fixture.manifest.read_text())
            restored.append((spec.fixture.manifest.parent / doc["dump"]).read_bytes())

        return SimpleNamespace(reset=reset)

    monkeypatch.setattr(social, "social_adapter", adapter)
    trial_recovery.fixture(TrialContext.from_manifest(context.directory / "manifest.json"), "reset")
    assert restored == [b"enqueued archive"]
    (fixture.manifest.parent / "snapshot.dump").write_bytes(b"tampered retained archive")
    with pytest.raises(ValueError, match="prepared fixture checksum"):
        trial_recovery.fixture(
            TrialContext.from_manifest(context.directory / "manifest.json"), "reset"
        )
    assert restored == [b"enqueued archive"]


@pytest.mark.parametrize("reference", ["target", "dsn", "hook"])
@pytest.mark.parametrize("changed", [False, True])
def test_resolved_environment_resume_binding(harness, monkeypatch, reference, changed):
    from tools.bench.engine.types import Fixture, Hook, SecretRef, TestPolicy

    store, cfg, launches = harness
    monkeypatch.setenv("REVIEW_WORKERS", "1")
    ref = SecretRef(env="REVIEW_WORKERS")
    target = cfg.targets["app"]
    workload = cfg.workloads["load"]
    if reference == "target":
        target = target.model_copy(update={"env": {"WORKERS": ref}})
    else:
        fixture = (
            Fixture(dsn=ref)
            if reference == "dsn"
            else Fixture(reset=Hook(argv=["true"], env={"VALUE": ref}))
        )
        workload = workload.model_copy(update={"fixture": fixture})
    job = cfg.jobs[0].model_copy(
        update={
            "test": TestPolicy.model_validate(
                {
                    "type": "search",
                    "users": {"min": 1, "start": 1, "max": 4},
                    "limits": {"max_trials": 4, "max_elapsed": 120},
                }
            )
        }
    )
    other = QueueStore(store.path.parent / "bound.db")
    other.register(
        cfg.model_copy(
            update={"targets": {"app": target}, "workloads": {"load": workload}, "jobs": [job]}
        )
    )
    original = controller.run_trial

    def stop(ctx, callback):
        value = original(ctx, callback)
        other.request_stop("test")
        return value

    monkeypatch.setattr(controller, "run_trial", stop)
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "interrupted"
    monkeypatch.setenv("REVIEW_WORKERS", "8" if changed else "1")
    monkeypatch.setattr(controller, "run_trial", original)
    other.clear_stop("test")
    result = controller.run_queue(other, "test")
    assert result["queues"][0]["state"] == ("needs_attention" if changed else "completed")
    assert len(launches) == (1 if changed else 3)


@pytest.mark.parametrize("damage", ["missing-key", "corrupt-key", "legacy"])
def test_environment_receipt_damage_fails_closed(harness, monkeypatch, damage):
    from tools.bench.engine.types import SecretRef

    store, cfg, launches = harness
    monkeypatch.setenv("REVIEW_SECRET", "private-value-1234")
    target = cfg.targets["app"].model_copy(
        update={"env": {"TOKEN": SecretRef(env="REVIEW_SECRET")}}
    )
    other = QueueStore(store.path.parent / "damage.db")
    other.register(cfg.model_copy(update={"targets": {"app": target}}))
    original = controller.run_trial

    def stop(ctx, callback):
        result = original(ctx, callback)
        other.request_stop("test")
        return result.model_copy(update={"valid": False})

    monkeypatch.setattr(controller, "run_trial", stop)
    controller.run_queue(other, "test")
    key = controller._root(other, "test") / ".environment-key"
    assert key.stat().st_mode & 0o777 == 0o600
    state = other.controller_state("test")
    assert "private-value-1234" not in str(state)
    if damage == "missing-key":
        key.unlink()
    elif damage == "corrupt-key":
        key.write_bytes(b"corrupt")
    else:
        del state["jobs"]["one"]["environment_bindings"]
        other.checkpoint("test", state)
    monkeypatch.setattr(controller, "run_trial", original)
    other.clear_stop("test")
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "needs_attention"
    assert len(launches) == 1
    if damage == "missing-key":
        assert not key.exists()


def test_environment_change_between_preparation_and_first_launch(harness, monkeypatch):
    from tools.bench.engine.types import SecretRef

    store, cfg, launches = harness
    monkeypatch.setenv("REVIEW_WORKERS", "1")
    target = cfg.targets["app"].model_copy(
        update={"env": {"WORKERS": SecretRef(env="REVIEW_WORKERS")}}
    )
    other = QueueStore(store.path.parent / "race.db")
    other.register(cfg.model_copy(update={"targets": {"app": target}}))
    original = controller._prepare

    def change(*args, **kwargs):
        prepared = original(*args, **kwargs)
        monkeypatch.setenv("REVIEW_WORKERS", "8")
        return prepared

    monkeypatch.setattr(controller, "_prepare", change)
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "needs_attention"
    assert launches == []


def test_only_declared_environment_references_are_bound(harness, monkeypatch):
    from tools.bench.engine.types import SecretRef

    store, cfg, launches = harness
    monkeypatch.setenv("REVIEW_WORKERS", "1")
    target = cfg.targets["app"].model_copy(
        update={"env": {"WORKERS": SecretRef(env="REVIEW_WORKERS")}}
    )
    workload = cfg.workloads["load"].model_copy(
        update={"parameters": {"literal": {"env": "NOT_A_REFERENCE"}}}
    )
    other = QueueStore(store.path.parent / "opaque.db")
    other.register(
        cfg.model_copy(update={"targets": {"app": target}, "workloads": {"load": workload}})
    )
    original = controller.run_trial

    def stop(ctx, callback):
        value = original(ctx, callback)
        other.request_stop("test")
        return value.model_copy(update={"valid": False})

    monkeypatch.setattr(controller, "run_trial", stop)
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "interrupted"
    monkeypatch.setenv("AWS_SESSION_TOKEN", "new-ambient-token")
    monkeypatch.setattr(controller, "run_trial", original)
    other.clear_stop("test")
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "completed", (
        other.controller_state("test")["last_error"]
    )
    assert len(launches) == 2


@pytest.mark.parametrize("damage", ["changed", "missing-key"])
def test_pending_fixture_recovery_checks_binding_before_cleanup(harness, monkeypatch, damage):
    from tools.bench.engine.trial_preparation import prepare_trial
    from tools.bench.engine.types import Fixture, Hook, SecretRef
    from tools.bench.queue import recovery

    store, cfg, launches = harness
    monkeypatch.setenv("FIXTURE_DESTINATION", "original-database")
    fixture = Fixture(
        reset=Hook(argv=["restore"], env={"DATABASE": SecretRef(env="FIXTURE_DESTINATION")})
    )
    workload = cfg.workloads["load"].model_copy(update={"fixture": fixture})
    other = QueueStore(store.path.parent / "pending-fixture.db")
    other.register(cfg.model_copy(update={"workloads": {"load": workload}}))

    def crash(context, callback):
        context.directory.mkdir(parents=True)
        prepare_trial(context, execute=lambda *a, **kw: pytest.fail("unexpected Docker call"))
        raise KeyboardInterrupt()

    monkeypatch.setattr(controller, "run_trial", crash)
    with pytest.raises(KeyboardInterrupt):
        controller.run_queue(other, "test")
    monkeypatch.setattr(
        recovery,
        "reconcile_trial",
        lambda context: pytest.fail("cleanup must not start against changed references"),
    )
    if damage == "changed":
        monkeypatch.setenv("FIXTURE_DESTINATION", "different-database")
    else:
        (controller._root(other, "test") / ".environment-key").unlink()
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "needs_attention"
    assert other.controller_state("test")["jobs"]["one"]["pending"] is not None
    assert launches == []


def test_checked_values_used_for_target_and_fixture_hooks(harness, monkeypatch):
    from tools.bench.engine import trial_recovery, workloads
    from tools.bench.engine.trial_preparation import prepare_trial
    from tools.bench.engine.types import Fixture, Hook, SecretRef

    store, cfg, launches = harness
    monkeypatch.setenv("SHARED_VALUE", "checked-value")
    ref = SecretRef(env="SHARED_VALUE")
    target = cfg.targets["app"].model_copy(update={"env": {"TOKEN": ref}})
    fixture = Fixture(reset=Hook(argv=["true"], env={"TOKEN": ref}))
    workload = cfg.workloads["load"].model_copy(update={"fixture": fixture})
    other = QueueStore(store.path.parent / "frozen-values.db")
    other.register(
        cfg.model_copy(update={"targets": {"app": target}, "workloads": {"load": workload}})
    )
    original = controller.run_trial
    received = []
    monkeypatch.setattr(
        workloads, "execute_host", lambda host, argv, **kw: received.append(kw["env"])
    )

    def run(context, callback):
        context.directory.mkdir(parents=True)
        prepared = prepare_trial(
            context, execute=lambda *a, **kw: pytest.fail("unexpected Docker call")
        )
        monkeypatch.setenv("SHARED_VALUE", "later-value")
        assert prepared.target_environment == {"TOKEN": "checked-value"}
        trial_recovery.fixture(context, "reset")
        assert received == [{"TOKEN": "checked-value"}]
        monkeypatch.setenv("SHARED_VALUE", "checked-value")
        return original(context, callback)

    monkeypatch.setattr(controller, "run_trial", run)
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "completed"


def test_mount_receipt_survives_queued_preparation_and_blocks_resume(
    harness, tmp_path, monkeypatch
):
    from tools.bench.engine.targets import mount_source_receipts
    from tools.bench.engine.types import TargetMount

    store, cfg, launches = harness
    source = tmp_path / "CA file.pem"
    source.write_text("certificate")
    target = cfg.targets["app"].model_copy(
        update={
            "mounts": [TargetMount(source=str(source), destination="/certs/ca.pem", read_only=True)]
        }
    )
    mounted = QueueStore(store.path.parent / "mounted.db")
    mounted.register(cfg.model_copy(update={"targets": {"app": target}}))
    monkeypatch.setattr(
        controller,
        "resolve_target",
        lambda target, env, **kw: ResolvedTarget(
            target,
            target.image,
            "sha256:app",
            (),
            mount_sources=mount_source_receipts(target, env.backend),
        ),
    )
    original = controller.run_trial

    def stop(ctx, callback):
        assert ctx.prepared_target.mount_sources[0]["source"] == str(source)
        value = original(ctx, callback)
        mounted.request_stop("test")
        return value.model_copy(update={"valid": False, "invalid_reasons": ("stop requested",)})

    monkeypatch.setattr(controller, "run_trial", stop)
    assert controller.run_queue(mounted, "test")["queues"][0]["state"] == "interrupted"
    source.write_text("mutated certificate")
    mounted.clear_stop("test")
    monkeypatch.setattr(controller, "run_trial", original)
    result = controller.run_queue(mounted, "test")
    assert result["queues"][0]["state"] != "completed"
    assert len(launches) == 1


@pytest.mark.parametrize("operation", ["release", "restore", "restore_releasing"])
def test_retained_ssh_lease_paths_reconstructed_at_json_boundary(tmp_path, monkeypatch, operation):
    import json
    import subprocess

    from tools.bench.engine.types import Host
    from tools.bench.queue import leases

    host = Host(
        transport="ssh",
        host="backend.test",
        user="rocky",
        port=2222,
        workdir=Path("/remote workdir"),
        identity_file=Path("/keys/ssh identity"),
    )
    container_id = "sha256:lease"
    resources = {container_id}
    received = []

    def execute(actual_host, argv, **kwargs):
        assert actual_host == host
        assert isinstance(actual_host.workdir, Path)
        assert isinstance(actual_host.identity_file, Path)
        received.append(argv)
        if argv[1] == "info":
            output = "docker-daemon-id"
        elif argv[1] == "ps":
            output = "\n".join(resources)
        elif argv[1] == "inspect":
            assert container_id in resources
            output = json.dumps(
                [
                    {
                        "Id": container_id,
                        "Name": "/bench-queue-lease",
                        "Config": {
                            "Labels": {
                                "bench.queue": "queue",
                                "bench.controller": "owner",
                                "bench.role": "queue-lease",
                            }
                        },
                    }
                ]
            )
        elif argv[1] == "rm":
            resources.remove(container_id)
            output = ""
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(leases, "execute_host", execute)
    saved = []
    monkeypatch.setattr(controller, "_save", lambda *args: saved.append(args))
    state = {
        "owner": "owner",
        "leases": {
            "docker-daemon-id": {
                "host": host.model_dump(mode="json"),
                "receipt": {
                    "host_identity": "docker-daemon-id",
                    "container_id": container_id,
                    "queue_id": "queue",
                    "owner": "owner",
                },
                "releasing": operation == "restore_releasing",
            }
        },
    }
    # Mirror the SQLite checkpoint JSON round-trip, including Path -> string.
    state = json.loads(json.dumps(state))
    assert isinstance(state["leases"]["docker-daemon-id"]["host"]["workdir"], str)
    if operation == "release":
        controller._release_hosts(None, "queue", state)
    else:
        with leases.ControllerLock(tmp_path / "controller.lock") as lock:
            controller._restore_hosts(None, "queue", state, lock)
    assert received
    if operation == "restore":
        assert resources == {container_id}
        assert state["leases"]
        assert not any(argv[1] == "rm" for argv in received)
    else:
        assert not resources
        assert not state["leases"]
        assert saved
