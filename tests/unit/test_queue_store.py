import multiprocessing
from pathlib import Path

import pytest

from tools.bench.engine.config import load_config
from tools.bench.queue.store import QueueConflict, QueueStore


def config(tmp_path):
    script = tmp_path / "load.js"
    script.write_text("export default function() {}")
    path = tmp_path / "queue.yaml"
    path.write_text(
        """version: 1
name: test
 targets: {}
""".replace(
            " targets: {}",
            f"""targets:
  app: {{image: example:1}}
workloads:
  load:
    script: {script}
    metrics: {{}}
environments:
  local:
    backend: {{transport: local}}
    generator: {{transport: local}}
defaults:
  environment: local
  workload: load
  stages:
    - {{id: hold, from: target, to: target, duration: 2s, evaluate: true}}
jobs:
  - {{id: one, targets: [app], test: {{type: fixed, users: 1}}}}""",
        )
    )
    return load_config(path)


def claim(path, output):
    value = QueueStore(Path(path)).claim_next("test", "worker")
    output.put(value.job_id if value else None)


def test_claim_race(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    store.register(config(tmp_path))
    ctx = multiprocessing.get_context("spawn")
    output = ctx.Queue()
    workers = [ctx.Process(target=claim, args=(str(store.path), output)) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
        assert worker.exitcode == 0
    assert [output.get() for _ in workers].count("one") == 1
    with pytest.raises(QueueConflict):
        store.update("test", "one", config(tmp_path).jobs[0], 1)


def test_revision_cancel_append_and_frozen_inputs(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    cfg = config(tmp_path)
    store.register(cfg)
    with pytest.raises(QueueConflict):
        store.append("test", cfg.jobs[0])
    receipt = store.update("test", "one", cfg.jobs[0], 1)
    assert receipt.revision == 2
    with pytest.raises(QueueConflict):
        store.update("test", "one", cfg.jobs[0], 1)
    store.cancel("test", "one")
    assert store.jobs("test")[0].state == "cancelled"
    store.append("test", cfg.jobs[0].model_copy(update={"id": "two"}))
    cfg.workloads["load"].script.write_text("changed")
    with pytest.raises(QueueConflict, match="input"):
        store.claim_next("test", "owner")
    assert store.jobs("test")[1].state == "pending"


def test_transaction_rolls_back(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    cfg = config(tmp_path)
    store.register(cfg)
    with store._connection() as conn:
        conn.execute(
            "CREATE TRIGGER deny_events BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
        )
    with pytest.raises(Exception, match="disk failure"):
        store.cancel("test", "one")
    assert store.jobs("test")[0].state == "pending"


def mutate(path, operation, output):
    store = QueueStore(Path(path))
    try:
        if operation == "cancel":
            value = store.cancel("test", "one")
        else:
            snapshot = store.snapshot("test")
            from tools.bench.engine.types import Job

            replacement = Job.model_validate_json(snapshot["jobs"][0]["definition"])
            value = store.update("test", "one", replacement, 1)
        output.put((operation, value.revision))
    except QueueConflict:
        output.put((operation, "conflict"))


@pytest.mark.parametrize("operation", ["update", "cancel"])
def test_claim_mutation_race(tmp_path, operation):
    store = QueueStore(tmp_path / "queue.db")
    store.register(config(tmp_path))
    ctx = multiprocessing.get_context("spawn")
    output = ctx.Queue()
    workers = [
        ctx.Process(target=claim, args=(str(store.path), output)),
        ctx.Process(target=mutate, args=(str(store.path), operation, output)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
        assert worker.exitcode == 0
    results = [output.get() for _ in workers]
    row = store.jobs("test")[0]
    if row.state == "active":
        active = store.active_job("test", "worker")
        assert active.revision in (2, 3)
        assert "one" in results
    else:
        assert row.state == "cancelled"
        assert None in results


def test_completed_append_stop_and_trial_references(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    cfg = config(tmp_path)
    store.register(cfg)
    first = store.claim_next("test", "owner")
    with pytest.raises(QueueConflict):
        store.finish("test", "one", "foreign")
    store.record_trial("test", "one", "owner", "trial", tmp_path / "trial")
    store.finish("test", "one", "owner", expected_revision=first.revision)
    assert store.claim_next("test", "owner") is None
    store.append("test", cfg.jobs[0].model_copy(update={"id": "two"}))
    store.request_stop("test", after_current=True)
    assert store.stop_request("test") is True
    assert store.claim_next("test", "owner") is None
    store.request_stop("test")
    assert store.stop_request("test") is False
    store.clear_stop("test")
    assert store.claim_next("test", "owner").job_id == "two"
    assert len(store.snapshot("test")["trials"]) == 1


def test_multi_target_logical_definition_and_source_independence(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    cfg = config(tmp_path)
    cfg = cfg.model_copy(
        update={
            "targets": {"app": cfg.targets["app"], "second": cfg.targets["app"]},
            "jobs": [cfg.jobs[0].model_copy(update={"targets": ["app", "second"]})],
        }
    )
    store.register(cfg)
    cfg.source.write_text("invalid YAML")
    claimed = store.claim_next("test", "owner")
    assert claimed.definition.targets == ["app", "second"]
    assert [j.target_name for j in claimed.resolved_jobs] == ["app", "second"]
