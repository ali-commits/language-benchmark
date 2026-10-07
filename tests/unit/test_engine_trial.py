import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.bench.engine.evidence import Journal, immutable_json
from tools.bench.engine.trial import OwnedDocker, parse_events, verify_occupancy
from tools.bench.engine.types import Host, OccupancyTolerance, ResolvedStage


def test_journal_preserves_intents_and_never_overwrites(tmp_path):
    journal = Journal(tmp_path)
    journal.record("deploy", "intent", {"name": "owned"})
    journal.record("deploy", "done", {"id": "abc"})
    assert [p["state"] for p in journal.entries()] == ["intent", "done"]
    with pytest.raises(FileExistsError):
        immutable_json(tmp_path / "journal/000000.json", {})


def test_event_reconciliation_keeps_interrupted_counts():
    events = [
        dict(type="metadata", load_start_epoch_ms=1),
        dict(type="operation_start", operation_id="a"),
        dict(type="journey_start", journey_id="j"),
        dict(type="journey_error", journey_id="j"),
    ]
    parsed = parse_events("\n".join("BENCH_EVENT " + json.dumps(e) for e in events))
    assert parsed.counts["interrupted_operations"] == 1
    assert parsed.counts["interrupted_journeys"] == 1
    assert "workload journey error" in parsed.reasons


def test_hold_occupancy_is_nonvacuous_and_coverage_required():
    stage = ResolvedStage(
        id="hold",
        from_users=4,
        to_users=4,
        duration=1.0,
        start_seconds=0.0,
        end_seconds=1.0,
        evaluate=True,
    )
    assert verify_occupancy([], (stage,), OccupancyTolerance())


def test_ambiguous_start_reconciles_by_labels_before_retry():
    labels = {"bench.queue": "q", "bench.job": "j", "bench.trial": "t"}
    info = {"Id": "abc", "Config": {"Labels": labels}, "State": {"Running": True}}
    calls = []

    def execute(host, argv, **kw):
        calls.append(argv)
        if argv[1] == "start":
            raise RuntimeError("response lost")
        return SimpleNamespace(stdout=json.dumps([info]))

    docker = OwnedDocker(Host(transport="local"), labels, execute=execute)
    assert docker.start("abc")["Id"] == "abc"
    assert sum(c[1] == "start" for c in calls) == 1


def test_foreign_container_survives_cleanup():
    calls = []

    def execute(host, argv, **kw):
        calls.append(argv)
        return SimpleNamespace(
            stdout=json.dumps([{"Id": "abc", "Config": {"Labels": {"bench.trial": "foreign"}}}])
        )

    docker = OwnedDocker(Host(transport="local"), {"bench.trial": "ours"}, execute=execute)
    with pytest.raises(ValueError, match="ownership"):
        docker.remove("abc")
    assert not any(c[1] == "rm" for c in calls)


def test_required_criterion_promotes_disabled_collector(tmp_path):
    from tools.bench.engine.collectors import collectors_for
    from tools.bench.engine.config import load_config, resolve_jobs
    from tools.bench.engine.types import Criterion, Telemetry

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    job = job.model_copy(
        update={
            "criteria": [
                Criterion(
                    metric="cpu_used", scope="container", statistic="max", operator="<=", value=0.8
                )
            ],
            "environment": job.environment.model_copy(
                update={"telemetry": Telemetry(container="off")}
            ),
        }
    )
    assert next(c for c in collectors_for(job) if c.scope == "container").mode == "required"


def test_missing_required_capability_fails_before_load(monkeypatch):
    from tools.bench.engine.collectors import Collector

    monkeypatch.setattr(Collector, "probe", lambda self: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError, match="required generator"):
        Collector("generator", Host(transport="local"), "required", 1.0).discover()
    optional = Collector("backend_host", Host(transport="local"), "optional", 1.0).discover()
    assert not optional.available and optional.reason


def test_scoped_cpu_normalizations_are_weighted_intervals():
    from tools.bench.engine.collectors import Collector

    host = Collector("backend_host", Host(transport="local"), "required", 1.0)
    host.raw = [
        {
            "epoch": 10.0,
            "cpu": [0, 0, 0, 10, 0, 0, 0, 0],
            "memory_total": 100,
            "memory_available": 80,
        },
        {
            "epoch": 11.0,
            "cpu": [5, 0, 0, 15, 0, 0, 0, 0],
            "memory_total": 100,
            "memory_available": 70,
        },
    ]
    sample = host.samples(10.0)[0]
    assert sample.value == 0.5 and sample.normalization == "all_host_cpus"
    assert sample.kind == "interval" and sample.end_seconds == 1.0
    container = Collector("container", Host(transport="local"), "required", 1.0, quota=2.0)
    container.raw = [
        {"epoch": 10.0, "cpu_stats": {"cpu_usage": {"total_usage": 0}}},
        {
            "epoch": 11.0,
            "cpu_stats": {"cpu_usage": {"total_usage": 1_000_000_000}},
            "memory_stats": {"usage": 20},
        },
    ]
    assert container.samples(10.0)[0].value == 0.5
    assert container.samples(10.0)[0].normalization == "container_quota"


@pytest.mark.parametrize("crash", ["deploy", "load", "restore"])
def test_failure_cleanup_and_reconciliation(tmp_path, monkeypatch, crash):
    import tools.bench.engine.trial as module
    from tools.bench.engine.config import load_config, resolve_jobs
    from tools.bench.engine.workloads import prepare_workload

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    prepared = prepare_workload(job.workload, tmp_path / "prepared")
    target = SimpleNamespace(
        image_id="sha256:target", build_snapshot=SimpleNamespace(identity="build")
    )
    context = module.TrialContext(
        job, 2, tmp_path / "trial", "q", crash, target, prepared, "sha256:k6"
    )
    resources = {}

    class Docker:
        def __init__(self, *args):
            pass

        def create(self, name, args):
            info = {
                "Id": name,
                "Name": "/" + name,
                "Image": "sha256:target",
                "State": {"Running": False, "ExitCode": 0},
                "Config": {"Labels": {}},
                "NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": "9999"}]}},
            }
            resources[name] = info
            return info

        def start(self, identity):
            resources[identity]["State"]["Running"] = True
            if identity.startswith("bench-load") and crash == "load":
                raise RuntimeError("crash during load")
            return resources[identity]

        def inspect(self, identity):
            if identity.startswith("bench-load"):
                resources[identity]["State"]["Running"] = False
            return resources[identity]

        def remove(self, identity):
            del resources[identity]

        def owned_networks(self):
            return []

        def create_network(self, name):
            return {"Id": name}

        def owned(self):
            return list(resources)

        def command(self, *args):
            return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(module, "OwnedDocker", Docker)
    monkeypatch.setattr(module, "verify_resources", lambda *args: None)
    monkeypatch.setattr(module, "collectors_for", lambda job: ())
    monkeypatch.setattr(module, "transfer_tree", lambda *args: None)
    monkeypatch.setattr(
        module,
        "wait_ready",
        lambda *args: (_ for _ in ()).throw(TimeoutError()) if crash == "deploy" else None,
    )
    resets = []

    def fixture(context, action):
        if action == "reset":
            resets.append(action)
            if crash == "restore" and len(resets) == 2:
                raise RuntimeError("crash during restore")

    monkeypatch.setattr(module, "_fixture", fixture)
    evidence = module.run_trial(context)
    assert not evidence.valid
    if crash == "restore":
        assert not evidence.cleanup_verified
        assert "bench-engine-lease" in resources
        assert not (context.directory / "result.json").exists()
        monkeypatch.setattr(module, "_fixture", lambda *args: None)
        receipt = module.reconcile_trial(
            module.TrialContext.from_manifest(context.directory / "manifest.json")
        )
        assert receipt.verified and not resources
        assert (context.directory / "interrupted.json").exists()
    else:
        assert evidence.cleanup_verified and not resources


def test_submillisecond_schedule_rejected_without_deployment(tmp_path):
    from tools.bench.engine.config import load_config, resolve_jobs
    from tools.bench.engine.trial import TrialContext, run_trial
    from tools.bench.engine.types import Stage

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    job = job.model_copy(
        update={"stages": [Stage(id="hold", from_users=1, to_users=1, duration=0.0001)]}
    )
    with pytest.raises(ValueError, match="millisecond"):
        run_trial(TrialContext(job, 1, tmp_path, "q", "t"))


def test_distinct_authentication_does_not_count_repeated_logins(tmp_path):
    from tools.bench.engine.social import social_adapter
    from tools.bench.engine.types import Fixture, Sample, SecretRef, Workload

    workload = Workload(
        adapter="social",
        script=tmp_path / "social.js",
        metrics={},
        fixture=Fixture(
            adapter="social",
            dsn=SecretRef(env="TEST_DSN"),
            manifest=tmp_path / "manifest.json",
            allowed_database="language_benchmark_test",
            runtime_cell="go-net-http",
        ),
    )
    samples = [
        Sample(
            metric="authenticated_account",
            timestamp=float(i),
            value=1.0,
            provider_fields={"account": name},
        )
        for i, name in enumerate(["Alice", "alice", "Bob"])
    ]
    assert social_adapter(workload).authentication(samples, 4) == {
        "distinct_accounts": 2,
        "target_accounts": 4,
        "fraction": 0.5,
    }


def test_saturated_hold_invalid_even_when_resource_criteria_could_pass():
    from tools.bench.engine.types import Sample

    stage = ResolvedStage(
        id="hold",
        from_users=4,
        to_users=4,
        duration=1.0,
        start_seconds=0.0,
        end_seconds=1.0,
        evaluate=True,
    )
    samples = [
        Sample(
            metric="active_users",
            scope="generator",
            kind="gauge",
            timestamp=0.0,
            value=1.0,
            max_gap_seconds=1.0,
        )
    ]
    assert "hold occupancy outside tolerance: hold" in verify_occupancy(
        samples, (stage,), OccupancyTolerance()
    )


def test_baseline_configuration_does_not_relax_legacy_gate():
    from tools.bench.baseline import wait_backend_baseline
    from tools.bench.engine.types import Baseline

    policy = Baseline(interval=0.01, timeout=0.1, consecutive_samples=1, stopped_cpu_max=0.5)
    assert policy.interval == 0.01
    with pytest.raises(ValueError, match="unsupported baseline"):
        wait_backend_baseline(
            {}, {"enabled": True, "interval_seconds": 0.01}, "stopped", Path("/unused")
        )


def test_native_k6_vus_gauges_align_to_scenario_origin():
    raw = "\n".join(
        [
            "BENCH_EVENT " + json.dumps({"type": "metadata", "load_start_epoch_ms": 1000}),
            json.dumps(
                {
                    "type": "Point",
                    "metric": "vus",
                    "data": {"time": "1970-01-01T00:00:02Z", "value": 100.0, "tags": {}},
                }
            ),
        ]
    )
    parsed = parse_events(raw)
    assert len(parsed.samples) == 1
    sample = parsed.samples[0]
    assert sample.metric == "active_users" and sample.timestamp == 1.0 and sample.value == 100.0
    assert sample.provider_fields["provider"] == "k6_native_vus"


def test_native_occupancy_nonfinite_is_invalid():
    raw = "\n".join(
        [
            "BENCH_EVENT " + json.dumps({"type": "metadata", "load_start_epoch_ms": 1000}),
            json.dumps(
                {
                    "type": "Point",
                    "metric": "vus",
                    "data": {"time": "1970-01-01T00:00:02Z", "value": float("nan")},
                }
            ),
        ]
    )
    assert "malformed native occupancy gauge" in parse_events(raw).reasons


def test_workload_nonfinite_evidence_is_invalid_without_workload_criterion():
    raw = "BENCH_EVENT " + json.dumps(
        {"type": "sample", "sample": {"metric": "latency", "timestamp": 1.0, "value": float("nan")}}
    )
    assert "malformed workload event" in parse_events(raw).reasons


def test_collector_points_survive_controller_interruption(tmp_path):
    import time

    from tools.bench.engine.collectors import Collection, Collector

    collector = Collector("generator", Host(transport="local"), "required", 0.01, available=True)
    collector.probe = lambda: {
        "epoch": time.time(),
        "cpu": [1, 2, 3, 4, 5, 6, 7, 8],
        "memory_total": 100,
        "memory_available": 50,
    }
    collection = Collection((collector,), directory=tmp_path)
    collection.start()
    deadline = time.monotonic() + 1
    try:
        while not list(tmp_path.rglob("*.json")) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert list(tmp_path.rglob("*.json"))
    finally:
        collection.finish()


def test_generator_cpu_saturation_invalidates_evidence():
    from tools.bench.engine.collectors import Collector, health_reasons
    from tools.bench.engine.types import Sample

    collector = Collector("generator", Host(transport="local"), "required", 1.0)
    samples = [
        Sample(
            metric="cpu_used",
            scope="generator",
            kind="interval",
            timestamp=0.0,
            end_seconds=1.0,
            value=0.99,
        )
    ]
    assert "load generator saturated" in health_reasons(collector, samples, 0.98)


def test_foreign_network_is_never_removed():
    calls = []

    def execute(host, argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(
            stdout=json.dumps([{"Id": "network", "Labels": {"bench.trial": "foreign"}}])
        )

    docker = OwnedDocker(Host(transport="local"), {"bench.trial": "ours"}, execute=execute)
    with pytest.raises(ValueError, match="ownership"):
        docker.remove_network("network")
    assert not any("rm" in call for call in calls)
