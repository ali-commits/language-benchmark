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


@pytest.mark.parametrize("crash", ["deploy", "load", "restore", "auth-0", "auth-85", "auth-95"])
def test_failure_cleanup_and_reconciliation(tmp_path, monkeypatch, crash):
    import tools.bench.engine.trial as module
    from tools.bench.engine.config import load_config, resolve_jobs
    from tools.bench.engine.workloads import prepare_workload

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    from tools.bench.engine.types import SecretRef

    secret = "target-env-secret-only"
    monkeypatch.setenv("TARGET_SECRET_REF", secret)
    job = job.model_copy(
        update={
            "target": job.target.model_copy(
                update={"env": {"BENCH_TOKEN": SecretRef(env="TARGET_SECRET_REF")}}
            )
        }
    )
    job = job.model_copy(
        update={
            "environment": job.environment.model_copy(
                update={"source_ips": ["10.0.0.10", "10.0.0.11"]}
            )
        }
    )
    monkeypatch.setattr(module, "validate_source_ips", lambda env: None)
    monkeypatch.setattr(
        "tools.bench.engine.trial_preparation.validate_source_ips", lambda env: None
    )
    auth_count = int(crash.split("-")[1]) if crash.startswith("auth-") else None
    if auth_count is not None:
        from tools.bench.engine.stages import resolve_stages
        from tools.bench.engine.types import Sample

        job = job.model_copy(
            update={"workload": job.workload.model_copy(update={"adapter": "social"})}
        )
        stages = resolve_stages(job, 100)
        account_samples = [
            Sample(
                metric="authenticated_account",
                timestamp=0.0,
                value=1.0,
                provider_fields={"account": str(i)},
            )
            for i in range(auth_count)
        ]
        monkeypatch.setattr(
            module,
            "parse_events",
            lambda raw: SimpleNamespace(
                samples=account_samples,
                reasons=[],
                counts={},
                metadata={
                    "stages": [stage.model_dump(mode="json") for stage in stages],
                    "load_start_epoch_ms": 1000,
                },
            ),
        )
        monkeypatch.setattr(module, "verify_occupancy", lambda *args: [])
        monkeypatch.setattr(
            "tools.bench.engine.social.social_adapter",
            lambda workload: SimpleNamespace(
                attest=lambda *args: None,
                authentication=lambda samples, target: {
                    "distinct_accounts": len(samples),
                    "target_accounts": target,
                    "fraction": len(samples) / target,
                },
            ),
        )
    prepared = prepare_workload(job.workload, tmp_path / "prepared")
    target = SimpleNamespace(
        image_id="sha256:target", build_snapshot=SimpleNamespace(identity="build"), mount_sources=[]
    )
    context = module.TrialContext(
        job,
        100 if auth_count is not None else 2,
        tmp_path / "trial",
        "q",
        crash,
        target,
        prepared,
        "sha256:k6",
    )
    resources = {}
    load_arguments = []

    class Docker:
        def __init__(self, host, labels):
            self.host = host

        def exists(self, identity):
            return identity in resources

        def create(self, name, args, **kwargs):
            if name.startswith("bench-load-"):
                load_arguments.append(args)
            if name.startswith("bench-target-"):
                assert secret not in json.dumps(args)
                assert "BENCH_TOKEN" not in args
                assert kwargs["env"] == {"BENCH_TOKEN": secret}
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
    if crash != "deploy":
        # Human progress/summary output must not split JSON metric writes on stdout.
        assert len(load_arguments) == 1
        position = load_arguments[0].index("--local-ips")
        assert load_arguments[0][position + 1] == "10.0.0.10,10.0.0.11"
        assert "--quiet" in load_arguments[0]
        assert "--summary-mode=disabled" in load_arguments[0]
        assert "json=/dev/stdout" in load_arguments[0]
    if auth_count is not None:
        from tools.bench.engine.metrics import evaluate
        from tools.bench.engine.types import Criterion

        assert evidence.valid, evidence.invalid_reasons
        result = evaluate(
            evidence,
            [Criterion(metric="authentication", statistic="rate", operator=">=", value=0.90)],
        )
        assert result.valid
        assert result.verdict == ("pass" if auth_count >= 90 else "fail")
        assert all(
            s.numerator == float(auth_count)
            for s in evidence.samples
            if s.metric == "authentication"
        )
    else:
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


@pytest.mark.parametrize("failure", ["controller_crash", "lost_response", "unexpected_resource"])
def test_cleanup_recovers_after_successful_lease_removal(tmp_path, monkeypatch, failure):
    import tools.bench.engine.trial as module
    from tools.bench.engine.config import load_config, resolve_jobs

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    context = module.TrialContext(job, 2, tmp_path, "q", "cleanup-recovery")
    journal = Journal(tmp_path)
    journal.record("fixture", "done")
    resources = {"lease-id": {"Id": "lease-id", "Name": "/bench-engine-lease"}}
    calls = []

    class ControllerCrash(BaseException):
        pass

    class Docker:
        def __init__(self, host, labels):
            self.host = host

        def owned(self):
            return list(resources)

        def owned_networks(self):
            return []

        def inspect(self, identity):
            return resources[identity]

        def exists(self, identity):
            return identity in resources

        def remove(self, identity):
            del resources[identity]
            if failure != "lost_response":
                raise ControllerCrash()
            raise RuntimeError("SSH response lost after removal")

    monkeypatch.setattr(module, "OwnedDocker", Docker)
    monkeypatch.setattr(module, "_fixture", lambda context, action: calls.append(action))
    if failure != "lost_response":
        with pytest.raises(ControllerCrash):
            module._cleanup(context, journal)
        assert not resources
        if failure == "unexpected_resource":
            resources["new-target-id"] = {"Id": "new-target-id", "Name": "/bench-target-new"}
        receipt = module._cleanup(context, journal)
    else:
        receipt = module._cleanup(context, journal)
    if failure == "unexpected_resource":
        assert not receipt.verified
        assert "new-target-id" in resources
    else:
        assert receipt.verified, receipt.reasons
    assert calls == ["reset", "verify"]
    assert any(e["action"] == "release" and e["state"] == "intent" for e in journal.entries())


def test_missing_lease_without_durable_restore_receipt_stays_blocked(tmp_path, monkeypatch):
    import tools.bench.engine.trial as module
    from tools.bench.engine.config import load_config, resolve_jobs

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    context = module.TrialContext(job, 2, tmp_path, "q", "missing-receipt")
    journal = Journal(tmp_path)
    journal.record("fixture", "done")
    # Old/partial state is not proof that fixture verification completed.
    journal.record("restore", "intent")

    class Docker:
        def __init__(self, *args):
            pass

        def owned(self):
            return []

        def owned_networks(self):
            return []

    monkeypatch.setattr(module, "OwnedDocker", Docker)
    monkeypatch.setattr(
        module, "_fixture", lambda *args: pytest.fail("must not restore without ownership")
    )
    assert not module._cleanup(context, journal).verified


@pytest.mark.parametrize("transport", ["local", "ssh"])
def test_owned_docker_create_secrets_use_transport_stdin(transport):
    secret = "credential-that-must-not-appear-in-argv"
    calls = []
    labels = {"bench.trial": "secret-test"}

    def execute(host, argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == "ps":
            return SimpleNamespace(stdout="")
        if argv[0] == "python3":
            return SimpleNamespace(stdout="container-id")
        return SimpleNamespace(
            stdout=json.dumps([{"Id": "container-id", "Config": {"Labels": labels}}])
        )

    docker = OwnedDocker(
        Host(transport=transport, host="test.example" if transport == "ssh" else None),
        labels,
        execute=execute,
    )
    docker.create("test-container", ["sha256:image"], env={"TOKEN": secret})
    assert all(secret not in json.dumps(argv) for argv, kwargs in calls)
    helper = next(kwargs for argv, kwargs in calls if argv[0] == "python3")
    assert json.loads(helper["input_data"])["environment"] == {"TOKEN": secret}
    assert not helper.get("env")


def run_baseline_samples(
    tmp_path, monkeypatch, phase, states, *, timeout=20.0, stop_requested=lambda: False
):
    import tools.bench.engine.trial as module
    from tools.bench.engine.config import load_config, resolve_jobs
    from tools.bench.engine.types import Baseline

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    policy = Baseline(
        enabled=True,
        interval=1.0,
        timeout=timeout,
        consecutive_samples=2,
        stopped_cpu_max=0.15,
        ready_cpu_max=0.2,
        stopped_memory_available=100.0,
        ready_memory_available=50.0,
        allowed_containers=["allowed-metrics"],
    )
    job = job.model_copy(
        update={"environment": job.environment.model_copy(update={"baseline": policy})}
    )
    context = module.TrialContext(job, 2, tmp_path, "q", "baseline")
    clock = [0.0]
    samples = []
    counters = [0, 0, 0, 0, 0, 0, 0, 0]
    current = [None]

    def execute(host, argv, **kwargs):
        if argv[0] == "python3":
            state = states[min(len(samples), len(states) - 1)]
            cpu, memory, containers = state
            counters[0] += round(cpu * 100)
            counters[3] += 100 - round(cpu * 100)
            current[0] = containers
            point = {"epoch": clock[0], "cpu": list(counters), "memory_available": memory}
            samples.append(point)
            return SimpleNamespace(stdout=json.dumps(point))
        return SimpleNamespace(
            stdout="\n".join(
                json.dumps({"ID": identity, "Names": name}) for identity, name in current[0]
            )
        )

    monkeypatch.setattr(module, "execute_host", execute)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + duration)
    )
    journal = Journal(tmp_path)
    module._baseline(
        context, phase, ("target-id",) if phase == "ready" else (), journal, stop_requested
    )
    return journal.entries(), samples


def test_baseline_dirty_samples_reset_consecutive_clean_count(tmp_path, monkeypatch):
    states = [
        (0.0, 200.0, []),
        (0.1, 200.0, []),
        (0.3, 200.0, []),
        (0.1, 200.0, []),
        (0.1, 50.0, []),
        (0.1, 200.0, []),
        (0.1, 200.0, [("foreign-id", "foreign")]),
        (0.1, 200.0, [("metrics-id", "allowed-metrics")]),
        (0.1, 200.0, []),
    ]
    entries, samples = run_baseline_samples(tmp_path, monkeypatch, "stopped", states)
    assert len(samples) == 9
    assert [entry["details"]["okay"] for entry in entries if entry["state"] == "sample"] == [
        False,
        True,
        False,
        True,
        False,
        True,
        False,
        True,
        True,
    ]
    assert entries[-1]["state"] == "done"


def test_baseline_ready_uses_ready_thresholds_and_owned_target_allowance(tmp_path, monkeypatch):
    entries, samples = run_baseline_samples(
        tmp_path, monkeypatch, "ready", [(0.18, 75.0, [("target-id", "target")])]
    )
    assert len(samples) == 3 and entries[-1]["state"] == "done"


def test_baseline_stopped_times_out_on_same_ready_only_conditions(tmp_path, monkeypatch):
    with pytest.raises(TimeoutError, match="baseline"):
        run_baseline_samples(tmp_path, monkeypatch, "stopped", [(0.18, 75.0, [])], timeout=3.0)
    entries = Journal(tmp_path).entries()
    assert len([e for e in entries if e["state"] == "sample"]) == 3
    assert not any(e["state"] == "done" for e in entries)


def test_baseline_cancellation_interrupts_sampling(tmp_path, monkeypatch):
    checks = []

    def stop():
        checks.append(True)
        return len(checks) == 3

    with pytest.raises(InterruptedError, match="stop requested"):
        run_baseline_samples(
            tmp_path, monkeypatch, "stopped", [(0.9, 200.0, [])], stop_requested=stop
        )
    assert len([e for e in Journal(tmp_path).entries() if e["state"] == "sample"]) == 2


@pytest.mark.parametrize("transport", ["local", "ssh"])
def test_real_transport_keeps_docker_secret_out_of_all_argv(monkeypatch, transport):
    import subprocess

    import tools.bench.engine.targets as targets

    secret = "transport-only-secret"
    labels = {"bench.trial": "secret-transport"}
    seen = []

    def run(argv, **kwargs):
        assert secret not in json.dumps(argv)
        if transport == "ssh":
            payload = json.loads(kwargs["input"])
            child_argv, child_env, child_input = payload["argv"], payload["env"], payload["stdin"]
        else:
            child_argv, child_env, child_input = argv, kwargs["env"], kwargs["input"]
        assert secret not in json.dumps(child_argv)
        if child_argv[1] == "ps":
            output = ""
        elif child_argv[0] == "python3":
            assert secret not in child_env.values()
            assert json.loads(child_input)["environment"]["TOKEN"] == secret
            seen.append(child_argv)
            output = "container-id"
        else:
            output = json.dumps([{"Id": "container-id", "Config": {"Labels": labels}}])
        if transport == "ssh":
            output = json.dumps({"returncode": 0, "stdout": output, "stderr": ""})
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(targets.subprocess, "run", run)
    host = Host(transport=transport, host="test.example" if transport == "ssh" else None)
    OwnedDocker(host, labels).create("test-container", ["sha256:image"], env={"TOKEN": secret})
    assert len(seen) == 1


def test_cleanup_resumes_partial_two_host_release_without_repeating_restore(tmp_path, monkeypatch):
    import tools.bench.engine.trial as module
    from tools.bench.engine.config import load_config, resolve_jobs

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    generator = Host(transport="ssh", host="generator.example")
    job = job.model_copy(
        update={"environment": job.environment.model_copy(update={"generator": generator})}
    )
    context = module.TrialContext(job, 2, tmp_path, "q", "two-host-release")
    journal = Journal(tmp_path)
    journal.record("fixture", "done")
    resources = {"local": {"backend-lease"}, "generator.example": {"generator-lease"}}
    crash = [True]
    fixture_calls = []

    class ControllerCrash(BaseException):
        pass

    class Docker:
        def __init__(self, host, labels):
            self.host = host
            self.key = host.host or "local"

        def owned(self):
            return list(resources[self.key])

        def owned_networks(self):
            return []

        def exists(self, identity):
            return identity in resources[self.key]

        def inspect(self, identity):
            return {"Id": identity, "Name": "/bench-engine-lease"}

        def remove(self, identity):
            resources[self.key].remove(identity)
            if crash[0]:
                crash[0] = False
                raise ControllerCrash()

    monkeypatch.setattr(module, "OwnedDocker", Docker)
    monkeypatch.setattr(module, "_fixture", lambda context, action: fixture_calls.append(action))
    with pytest.raises(ControllerCrash):
        module._cleanup(context, journal)
    assert resources == {"local": {"backend-lease"}, "generator.example": set()}
    assert module._cleanup(context, journal).verified
    assert fixture_calls == ["reset", "verify"]
    assert not any(resources.values())


@pytest.mark.parametrize("value", ["line one\nline two", "line one\rline two", "value\0suffix"])
def test_container_environment_rejects_unrepresentable_values_before_mutation(value):
    calls = []
    docker = OwnedDocker(
        Host(transport="local"),
        {"bench.trial": "invalid-env"},
        execute=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    with pytest.raises(ValueError, match="CR, LF or NUL"):
        docker.create("test-container", ["sha256:image"], env={"TOKEN": value})
    assert not calls


def test_container_env_helper_keeps_client_environment_and_closes_private_fd(monkeypatch):
    import io
    import os
    import shutil
    import subprocess
    import sys

    from tools.bench.engine.docker_env import CREATE_WITH_ENV

    values = {
        "DOCKER_HOST": "tcp://target-only:1",
        "PATH": "/target-only",
        "TOKEN": r"literal\nvalue λ",
    }
    original = dict(os.environ)
    descriptors = []
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"environment": values, "timeout": 1}))
    )
    monkeypatch.setattr(sys, "argv", ["helper", "--name", "test", "image"])
    monkeypatch.setattr(shutil, "which", lambda command: "/trusted/docker")

    def run(argv, **kwargs):
        assert argv[0] == "/trusted/docker"
        assert dict(os.environ) == original
        assert "env" not in kwargs
        assert not any(value in json.dumps(argv) for value in values.values())
        descriptor = kwargs["pass_fds"][0]
        descriptors.append(descriptor)
        assert os.fstat(descriptor).st_mode & 0o777 == 0o600
        path = argv[argv.index("--env-file") + 1]
        with open(path, encoding="utf-8") as stream:
            contents = stream.read()
        assert contents == "".join(key + "=" + value + "\n" for key, value in values.items())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as finished:
        exec(compile(CREATE_WITH_ENV, "<container-env-helper>", "exec"), {})
    assert finished.value.code == 0
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
