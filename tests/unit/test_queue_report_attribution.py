import pytest

from tests.unit.test_queue_controller import harness as harness
from tools.bench.engine.targets import ResolvedTarget
from tools.bench.queue import controller
from tools.bench.queue.store import QueueStore


@pytest.mark.parametrize("mode", ["fixed", "search"])
def test_report_exports_pinned_target_attribution(harness, monkeypatch, tmp_path, mode):
    import json

    from tools.bench.engine.report import write_queue_report
    from tools.bench.engine.trial_preparation import prepare_trial
    from tools.bench.engine.types import SecretRef, TestPolicy

    store, cfg, launches = harness
    names = ["orchid<script>", "copper"]
    monkeypatch.setenv("REPORT_SECRET", "secret-must-not-export")
    targets = {
        name: cfg.targets["app"].model_copy(
            update={"image": "example:latest", "env": {"TOKEN": SecretRef(env="REPORT_SECRET")}}
        )
        for i, name in enumerate(names)
    }
    job = cfg.jobs[0].model_copy(update={"targets": names})
    if mode == "search":
        job = job.model_copy(
            update={
                "test": TestPolicy.model_validate(
                    {
                        "type": "search",
                        "users": {"min": 1, "start": 1, "max": 2},
                        "limits": {"max_trials": 4, "max_elapsed": 120},
                    }
                )
            }
        )
    other = QueueStore(store.path.parent / "report-bound.db")
    other.register(cfg.model_copy(update={"targets": targets, "jobs": [job]}))
    images = iter(["sha256:0", "sha256:1"])
    monkeypatch.setattr(
        controller,
        "resolve_target",
        lambda target, env, **kw: ResolvedTarget(target, target.image, next(images), ()),
    )
    original = controller.run_trial

    def run(context, callback):
        context.directory.mkdir(parents=True)
        prepare_trial(context, execute=lambda *a, **kw: pytest.fail("unexpected Docker call"))
        return original(context, callback)

    monkeypatch.setattr(controller, "run_trial", run)
    assert controller.run_queue(other, "test")["queues"][0]["state"] == "completed", (
        other.controller_state("test")["last_error"]
    )
    output = write_queue_report(other, "test", tmp_path / "export")
    report = json.loads(output.with_suffix(".json").read_text())
    assert report["jobs"][0]["targets"] == [
        {"target": i, "target_name": name, "image_id": f"sha256:{i}"}
        for i, name in enumerate(names)
    ]
    for row in report["decisions"] + report["objectives"] + report["trials"]:
        assert row["target_name"] == names[row["target"]]
        assert row["image_id"] == f"sha256:{row['target']}"
    for row in report["trials"]:
        evidence = json.loads((output.parent / row["evidence"]).read_text())
        assert evidence["target_name"] == row["target_name"]
        assert evidence["image_id"] == row["image_id"]
    exported = "\n".join(p.read_text() for p in output.parent.rglob("*") if p.is_file())
    assert "secret-must-not-export" not in exported
    assert "REPORT_SECRET" not in exported
    assert str(store.path.parent) not in exported
    assert "orchid&lt;script&gt;" in output.read_text()
    assert "orchid<script>" not in output.read_text()
