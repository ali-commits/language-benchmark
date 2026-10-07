"""Crash-reconcilable Docker trial lifecycle with immutable evidence."""

import fcntl
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Literal

from ..records import checked_record
from .collectors import HOST_PROBE, Collection, Collector, collectors_for, health_reasons
from .evidence import Journal, immutable_json
from .owned_docker import OwnedDocker as _OwnedDocker
from .records import BaselinePoint, ContainerInfo, RunningContainer
from .targets import (
    HostExecutor,
    execute_host,
    resolve_endpoints,
    verify_resources,
    wait_ready,
)
from .trial_context import CleanupReceipt as CleanupReceipt
from .trial_context import TrialContext as TrialContext
from .trial_events import Events as Events
from .trial_events import parse_events as parse_events
from .trial_events import verify_occupancy as verify_occupancy
from .trial_preparation import prepare_trial
from .trial_recovery import FixtureAction, FixtureReceipt, cleanup, fixture
from .types import Host, Sample, TrialEvidence
from .workloads import transfer_tree


class OwnedDocker(_OwnedDocker):
    """Compatibility facade captures the current trial transport at construction."""

    def __init__(
        self, host: Host, labels: dict[str, str], execute: HostExecutor | None = None
    ) -> None:
        super().__init__(host, labels, execute or execute_host)


@contextmanager
def trial_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _fixture(context: TrialContext, action: FixtureAction) -> FixtureReceipt:
    return fixture(context, action)


def _cleanup(context: TrialContext, journal: Journal) -> CleanupReceipt:
    return cleanup(
        context, journal, docker_factory=OwnedDocker, fixture_action=_fixture, execute=execute_host
    )


def reconcile_trial(context: TrialContext) -> CleanupReceipt:
    with trial_lock(context.directory):
        if (context.directory / "result.json").exists():
            evidence = TrialEvidence.model_validate_json(
                (context.directory / "result.json").read_text()
            )
            return CleanupReceipt(evidence.cleanup_verified)
        journal = Journal(context.directory)
        if not (context.directory / "manifest.json").exists():
            raise ValueError("reconciliation requires retained trial manifest")
        return _cleanup(context, journal)


def _baseline(
    context: TrialContext,
    phase: Literal["stopped", "ready"],
    allowed: tuple[str, ...],
    journal: Journal,
    stop_requested: Callable[[], bool],
) -> None:
    policy = context.environment.baseline
    if not policy.enabled:
        journal.record("baseline_" + phase, "disabled")
        return
    docker = OwnedDocker(context.environment.backend, {})
    previous: BaselinePoint | None = None
    good = 0
    deadline = time.monotonic() + policy.timeout
    journal.record("baseline_" + phase, "intent")
    while time.monotonic() < deadline:
        if stop_requested():
            raise InterruptedError("stop requested")
        point = checked_record(
            json.loads(
                execute_host(
                    context.environment.backend, ["python3", "-c", HOST_PROBE], timeout=15
                ).stdout
            ),
            BaselinePoint,
        )
        running = docker.command("ps", "--no-trunc", "--format", "{{json .}}").stdout
        foreign = [
            p
            for p in (
                checked_record(json.loads(row), RunningContainer) for row in running.splitlines()
            )
            if p["ID"] not in allowed and p["Names"] not in policy.allowed_containers
        ]
        cpu = None
        if previous:
            delta = [b - a for a, b in zip(previous["cpu"], point["cpu"])]
            cpu = 1 - (delta[3] + delta[4]) / sum(delta) if sum(delta) > 0 else None
        previous = point
        okay = (
            cpu is not None
            and cpu <= (policy.stopped_cpu_max if phase == "stopped" else policy.ready_cpu_max)
            and point["memory_available"]
            >= (
                policy.stopped_memory_available
                if phase == "stopped"
                else policy.ready_memory_available
            )
            and not foreign
        )
        good = good + 1 if okay else 0
        journal.record(
            "baseline_" + phase,
            "sample",
            {
                "cpu": cpu,
                "memory_available": point["memory_available"],
                "foreign_containers": len(foreign),
                "okay": okay,
            },
        )
        if good >= policy.consecutive_samples:
            journal.record("baseline_" + phase, "done")
            return
        time.sleep(min(policy.interval, max(0, deadline - time.monotonic())))
    raise TimeoutError("clean baseline gate timed out")


def run_trial(
    context: TrialContext, stop_requested: Callable[[], bool] = lambda: False
) -> TrialEvidence:
    with trial_lock(context.directory):
        return _run_trial(context, stop_requested)


def _run_trial(context: TrialContext, stop_requested: Callable[[], bool]) -> TrialEvidence:
    prepared = prepare_trial(context, execute=execute_host)
    directory, job = prepared.directory, context.job
    prepared_target, bundle, stages = prepared.target, prepared.bundle, prepared.stages
    generator_image_id, execution_id = prepared.generator_image_id, prepared.execution_id
    labels, target_env = prepared.labels, prepared.target_environment
    journal = Journal(directory)
    backend = OwnedDocker(job.environment.backend, labels)
    generator = OwnedDocker(job.environment.generator, labels)
    unique = hashlib.sha256(json.dumps(labels, sort_keys=True).encode()).hexdigest()[:20]
    collectors: tuple[Collector, ...] = ()
    collection: Collection | None = None
    reasons: list[str] = []
    samples: list[Sample] = []
    parsed: Events | None = None
    load: ContainerInfo | None = None
    leased = False
    try:
        if stop_requested():
            raise InterruptedError("stop requested")
        # Name uniqueness acts as a Docker-host-wide exclusive lease. It is held
        # through fixture restoration; a failed cleanup deliberately leaves it.
        journal.record("ownership", "intent")
        leased = True
        backend.create("bench-engine-lease", [prepared_target.image_id])
        if job.environment.backend != job.environment.generator:
            # k6 image must exist on generator, and will be pinned below.
            generator.command("image", "inspect", generator_image_id)
            generator.create("bench-engine-lease", [generator_image_id])
        journal.record("ownership", "done")
        collectors = collectors_for(job)
        for collector in collectors:
            collector.discover()
        journal.record(
            "capabilities",
            "done",
            {
                c.scope: {"mode": c.mode, "available": c.available, "reason": c.reason}
                for c in collectors
            },
        )
        if stop_requested():
            raise InterruptedError("stop requested")
        journal.record("fixture", "intent")
        _fixture(context, "prepare")
        _fixture(context, "reset")
        _fixture(context, "verify")
        journal.record("fixture", "done")
        _baseline(context, "stopped", (), journal, stop_requested)
        r = job.target.resources
        args = [
            "--cpus",
            str(r.cpus),
            "--memory",
            str(int(r.memory)),
            "--memory-swap",
            str(int(r.memory + r.swap)),
            "--ulimit",
            f"nofile={r.nofile}:{r.nofile}",
        ]
        if job.environment.network.mode == "host":
            args += ["--network", "host"]
        else:
            args += ["-p", f"0:{job.target.port}"]
            if job.environment.network.mode in ("bridge", "compose"):
                network_name = "bench-network-" + unique
                journal.record("network", "intent", {"name": network_name})
                network = backend.create_network(network_name)
                args += ["--network", network["Id"]]
                journal.record("network", "done", {"id": network["Id"]})
        for key, value in job.target.labels.items():
            args.extend(["--label", key + "=" + value])
        if job.workload.adapter == "social":
            args += ["-e", "RUN_ID=" + context.trial_id, "-e", "RUN_DIR=/tmp/bench-runtime"]
        args += [prepared_target.image_id, *(job.target.command or [])]
        if stop_requested():
            raise InterruptedError("stop requested")
        journal.record("deploy", "intent", {"name": "bench-target-" + unique})
        info = backend.create("bench-target-" + unique, args, env=target_env)
        identity = info["Id"]
        info = backend.start(identity)
        verify_resources(job.target, info)
        if info["Image"] != prepared_target.image_id:
            raise ValueError("deployed image identity mismatch")
        port = job.target.port
        if job.environment.network.mode != "host":
            bindings = info["NetworkSettings"]["Ports"][f"{job.target.port}/tcp"]
            if not bindings:
                raise ValueError("deployed container has no TCP host port")
            port = int(bindings[0]["HostPort"])
        endpoints = resolve_endpoints(job.target, job.environment, host_port=port)
        journal.record("deploy", "done", {"id": identity, "port": port})
        wait_ready(job.target, job.environment.backend, endpoints.readiness_url)
        if job.workload.adapter == "social":
            from .social import social_adapter

            social_adapter(job.workload).attest(context, identity)
        _baseline(context, "ready", (identity,), journal, stop_requested)
        for collector in collectors:
            if collector.scope == "container" and collector.mode != "off":
                collector.container_id = identity
                collector.discover()
        remote_bundle = str(
            (job.environment.generator.workdir or Path("/tmp")) / ("bench-execution-" + unique)
        )
        journal.record("stage_workload", "intent", {"path": remote_bundle})
        transfer_tree(bundle, job.environment.generator, remote_bundle)
        journal.record("stage_workload", "done")
        config = {
            "stages": [s.model_dump(mode="json") for s in stages],
            "baseUrl": endpoints.generator_url,
            "parameters": job.workload.parameters,
            "gracefulRampDown": job.graceful_ramp_down,
            "drain": job.drain,
            "telemetryInterval": min(0.1, job.environment.telemetry.interval),
        }
        if stop_requested():
            raise InterruptedError("stop requested")
        k6 = generator_image_id
        journal.record("load", "intent", {"name": "bench-load-" + unique, "image_id": k6})
        load = generator.create(
            "bench-load-" + unique,
            [
                "--network",
                "host",
                "--user",
                "0",
                "-v",
                remote_bundle + ":/scripts:ro",
                "-e",
                "BENCH_CONFIG=" + json.dumps(config),
                k6,
                "run",
                "--quiet",
                "--summary-mode=disabled",
                "--log-format",
                "raw",
                "--out",
                "json=/dev/stdout",
                "/scripts/runner.js",
            ],
        )
        collection = Collection(collectors, directory=directory)
        collection.start()
        generator.start(load["Id"])
        journal.record("load", "started", {"id": load["Id"]})
        deadline = (
            time.monotonic() + stages[-1].end_seconds + max(job.drain, job.graceful_ramp_down) + 30
        )
        while True:
            state = generator.inspect(load["Id"])["State"]
            if not state["Running"]:
                break
            if stop_requested():
                raise InterruptedError("stop requested")
            if time.monotonic() > deadline:
                raise TimeoutError("bounded load deadline expired")
            time.sleep(0.1)
        raw_result = generator.command("logs", load["Id"])
        raw = raw_result.stdout + raw_result.stderr
        immutable_json(directory / "load-log.json", {"log": raw})
        parsed = parse_events(raw)
        reasons.extend(parsed.reasons)
        if state.get("ExitCode") != 0 or state.get("OOMKilled"):
            reasons.append("load generator failed")
        if parsed.metadata.get("stages") != [s.model_dump(mode="json") for s in stages]:
            reasons.append("executed schedule mismatch")
        samples.extend(parsed.samples)
        reasons.extend(verify_occupancy(samples, stages, job.hold_tolerance))
        if job.workload.adapter == "social":
            from .social import social_adapter

            target_accounts = max(max(s.from_users, s.to_users) for s in stages)
            authentication = social_adapter(job.workload).authentication(samples, target_accounts)
            immutable_json(directory / "authentication.json", authentication)
            for stage in stages:
                if max(stage.from_users, stage.to_users) == 0:
                    continue
                stage_auth = social_adapter(job.workload).authentication(
                    [s for s in parsed.samples if s.timestamp < stage.end_seconds], target_accounts
                )
                samples.append(
                    Sample(
                        metric="authentication",
                        kind="rate",
                        timestamp=stage.end_seconds - 1e-9,
                        numerator=float(stage_auth["distinct_accounts"]),
                        denominator=float(target_accounts),
                        unit="fraction",
                    )
                )
            if authentication["distinct_accounts"] != target_accounts:
                reasons.append("incomplete distinct-account authentication")
        journal.record("load", "done", parsed.counts)
    except Exception as exc:
        # Never persist exception messages: argv/hooks may contain secrets.
        reasons.append("trial execution failed: " + type(exc).__name__)
        journal.record("execution", "failed", {"type": type(exc).__name__})
    finally:
        if load and not (directory / "load-log.json").exists():
            try:
                generator.inspect(load["Id"])
                # Stop before fetching so the retained log contains the entire
                # cancellation tail, then ownership-checked cleanup removes it.
                generator.command("stop", "--time", "1", load["Id"])
                result = generator.command("logs", load["Id"])
                raw = result.stdout + result.stderr
                immutable_json(directory / "load-log.json", {"log": raw})
                parsed = parse_events(raw)
                samples.extend(parsed.samples)
            except Exception:
                reasons.append("interrupted load log unavailable")
        if collection:
            try:
                collection.finish()
            except Exception:
                reasons.append("collector shutdown failed")
        if parsed and parsed.metadata.get("load_start_epoch_ms"):
            origin = parsed.metadata["load_start_epoch_ms"] / 1000
            for collector in collectors:
                if collector.scope == "database":
                    if not collector.available:
                        continue
                    from .collectors import rds_samples

                    try:
                        values = rds_samples(collector, origin, origin + stages[-1].end_seconds)
                    except Exception as exc:
                        collector.errors.append(type(exc).__name__)
                        values = []
                else:
                    try:
                        values = collector.samples(origin)
                    except Exception as exc:
                        collector.errors.append(type(exc).__name__)
                        values = []
                samples.extend(values)
                reasons.extend(
                    health_reasons(collector, values, job.environment.telemetry.generator_cpu_max)
                )
        immutable_json(
            directory / "collectors.json",
            {
                c.scope: {
                    "raw": c.raw,
                    "errors": c.errors,
                    "available": c.available,
                    "reason": c.reason,
                }
                for c in collectors
            },
        )
        receipt = _cleanup(context, journal) if leased else CleanupReceipt(True)
        reasons.extend(receipt.reasons)
    evidence = TrialEvidence(
        trial_id=context.trial_id,
        users=context.users,
        execution_id=execution_id,
        stages=stages,
        samples=tuple(samples),
        valid=not reasons,
        invalid_reasons=tuple(dict.fromkeys(reasons)),
        cleanup_verified=receipt.verified,
        manifest_path=directory / "manifest.json",
        samples_path=directory / "samples.json",
    )
    immutable_json(directory / "samples.json", [s.model_dump(mode="json") for s in samples])
    immutable_json(
        directory / ("result.json" if receipt.verified else "interrupted.json"),
        evidence.model_dump(mode="json"),
    )
    return evidence
