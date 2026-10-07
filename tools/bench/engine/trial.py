"""Crash-reconcilable Docker trial lifecycle with immutable evidence."""

import fcntl
import hashlib
import json
import math
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .collectors import HOST_PROBE, Collection, collectors_for, health_reasons
from .evidence import Journal, immutable_json
from .stages import resolve_stages
from .targets import (
    execute_host,
    owner_labels,
    prepared_execution_identity,
    resolve_endpoints,
    resolve_target,
    verify_resources,
    wait_ready,
)
from .types import ResolvedJob, Sample, TrialEvidence
from .workloads import prepare_workload, run_hook, transfer_tree

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class TrialContext:
    job: ResolvedJob
    users: int | None
    directory: Path
    queue_id: str
    trial_id: str
    prepared_target: object | None = None
    prepared_workload: object | None = None
    generator_image_id: str | None = None

    @property
    def environment(self):
        return self.job.environment

    @property
    def job_id(self):
        return self.job.id

    @classmethod
    def from_manifest(cls, path):
        path = Path(path)
        data = json.loads(path.read_text())
        # Reconciliation never resolves images, builds or re-reads mutable sources.
        return cls(
            ResolvedJob.model_validate_json(json.dumps(data["job"])),
            data["users"],
            path.parent,
            data["queue_id"],
            data["trial_id"],
        )


@dataclass(frozen=True)
class CleanupReceipt:
    verified: bool
    removed: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass
class Events:
    samples: list
    metadata: dict
    counts: dict
    reasons: list


def parse_events(raw):
    samples, metadata, reasons = [], {}, []
    native_vus = []
    starts = {"operation": set(), "journey": set()}
    ends = {"operation": set(), "journey": set()}
    for line in raw.splitlines():
        if not line.startswith("BENCH_EVENT "):
            if line.startswith("{"):
                try:
                    point = json.loads(line)
                    if point.get("type") == "Point" and point.get("metric") == "vus":
                        native_vus.append(point["data"])
                except (ValueError, KeyError, TypeError):
                    reasons.append("malformed native k6 metric")
            continue
        try:
            event = json.loads(line[len("BENCH_EVENT ") :])
            kind = event["type"]
            if kind == "metadata":
                if metadata:
                    raise ValueError("duplicate metadata")
                metadata = event
            elif kind == "sample":
                sample = Sample.model_validate(event["sample"])
                if any(
                    value is not None and not math.isfinite(value)
                    for value in (
                        sample.timestamp,
                        sample.value,
                        sample.numerator,
                        sample.denominator,
                        sample.end_seconds,
                        sample.max_gap_seconds,
                    )
                ):
                    raise ValueError("nonfinite workload sample")
                if sample.kind == "rate" and (
                    sample.numerator is None
                    or sample.denominator is None
                    or sample.denominator <= 0
                    or not 0 <= sample.numerator <= sample.denominator
                ):
                    raise ValueError("invalid workload rate")
                samples.append(sample)
            elif kind == "journey_error":
                reasons.append("workload journey error")
            elif kind in ("operation_start", "operation_end", "journey_start", "journey_end"):
                category, state = kind.split("_")
                destination = starts if state == "start" else ends
                identity = event[category + "_id"]
                if identity in destination[category]:
                    raise ValueError("duplicate event identity")
                destination[category].add(identity)
        except (ValueError, KeyError, TypeError):
            reasons.append("malformed workload event")
    if metadata.get("load_start_epoch_ms") is not None:
        from datetime import datetime

        for point in native_vus:
            try:
                timestamp = (
                    datetime.fromisoformat(point["time"].replace("Z", "+00:00")).timestamp()
                    - metadata["load_start_epoch_ms"] / 1000
                )
                value = float(point["value"])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("invalid native VUs")
                samples.append(
                    Sample(
                        metric="active_users",
                        scope="generator",
                        kind="gauge",
                        timestamp=timestamp,
                        value=value,
                        unit="count",
                        max_gap_seconds=1.5,
                        collection_interval=1.0,
                        provider_fields={"provider": "k6_native_vus"},
                    )
                )
            except (ValueError, KeyError, TypeError):
                reasons.append("malformed native occupancy gauge")
    counts = {}
    for category in starts:
        counts["started_" + category + "s"] = len(starts[category])
        counts["completed_" + category + "s"] = len(ends[category])
        counts["interrupted_" + category + "s"] = len(starts[category] - ends[category])
        if ends[category] - starts[category]:
            reasons.append("completion without matching start")
    if not metadata or not isinstance(metadata.get("load_start_epoch_ms"), (int, float)):
        reasons.append("missing load origin metadata")
    if not starts["operation"] or not ends["operation"]:
        reasons.append("no completed workload operations")
    return Events(samples, metadata, counts, list(dict.fromkeys(reasons)))


def verify_occupancy(samples, stages, tolerance):
    """Require actual hold gauges with coverage and bounds; never pass an empty hold."""
    observations = sorted(
        (s for s in samples if s.scope == "generator" and s.metric == "active_users"),
        key=lambda s: s.timestamp,
    )
    reasons = []
    for stage in stages:
        if stage.from_users != stage.to_users:
            continue
        begin, end = stage.start_seconds + tolerance.grace, stage.end_seconds
        if begin >= end:
            reasons.append(f"occupancy grace consumes hold {stage.id}")
            continue
        covered = 0.0
        for i, sample in enumerate(observations):
            gap = sample.max_gap_seconds
            if (
                sample.kind != "gauge"
                or gap is None
                or gap <= 0
                or sample.value is None
                or not math.isfinite(sample.value)
            ):
                reasons.append("invalid occupancy gauge")
                continue
            stop = min(
                sample.timestamp + gap,
                observations[i + 1].timestamp if i + 1 < len(observations) else end,
            )
            overlap = max(0.0, min(end, stop) - max(begin, sample.timestamp))
            if overlap:
                covered += overlap
                if abs(sample.value - stage.to_users) > max(
                    1e-9, stage.to_users * tolerance.fraction
                ):
                    reasons.append(f"hold occupancy outside tolerance: {stage.id}")
        if covered / (end - begin) < 0.95:
            reasons.append(f"insufficient occupancy coverage: {stage.id}")
    return list(dict.fromkeys(reasons))


class OwnedDocker:
    def __init__(self, host, labels, execute=None):
        self.host, self.labels = host, labels
        self.execute = execute or execute_host

    def command(self, *argv, **kw):
        return self.execute(self.host, ["docker", *argv], **kw)

    def inspect(self, identity):
        info = json.loads(self.command("inspect", identity).stdout)[0]
        if any(
            info.get("Config", {}).get("Labels", {}).get(k) != v for k, v in self.labels.items()
        ):
            raise ValueError("resource ownership mismatch")
        return info

    def by_name(self, name):
        ids = self.command("ps", "-aq", "--no-trunc", "--filter", f"name=^/{name}$").stdout.split()
        return self.inspect(ids[0]) if ids else None

    def create(self, name, arguments):
        if self.by_name(name):
            raise ValueError("existing resource requires explicit reconciliation")
        flags = [flag for k, v in self.labels.items() for flag in ("--label", k + "=" + v)]
        try:
            identity = self.command("create", "--name", name, *flags, *arguments).stdout.strip()
        except Exception:
            # A successful remote create may lose its SSH response. Never issue
            # a duplicate create until actual owner-labelled state is known.
            found = self.by_name(name)
            if found:
                return found
            raise
        return self.inspect(identity)

    def start(self, identity):
        self.inspect(identity)
        try:
            self.command("start", identity)
        except Exception:
            info = self.inspect(identity)
            if info.get("State", {}).get("Running") or info.get("State", {}).get(
                "StartedAt", ""
            ).startswith(("20",)):
                return info
            raise
        return self.inspect(identity)

    def remove(self, identity):
        self.inspect(identity)
        self.command("rm", "-f", identity)

    def owned(self):
        args = [
            v for k, value in self.labels.items() for v in ("--filter", "label=" + k + "=" + value)
        ]
        return self.command("ps", "-aq", "--no-trunc", *args).stdout.split()

    def inspect_network(self, identity):
        info = json.loads(self.command("network", "inspect", identity).stdout)[0]
        if any(info.get("Labels", {}).get(key) != value for key, value in self.labels.items()):
            raise ValueError("network ownership mismatch")
        return info

    def create_network(self, name):
        flags = [
            flag for key, value in self.labels.items() for flag in ("--label", key + "=" + value)
        ]
        try:
            identity = self.command("network", "create", *flags, name).stdout.strip()
        except Exception:
            return self.inspect_network(name)
        return self.inspect_network(identity)

    def remove_network(self, identity):
        self.inspect_network(identity)
        self.command("network", "rm", identity)

    def owned_networks(self):
        flags = [
            flag
            for key, value in self.labels.items()
            for flag in ("--filter", "label=" + key + "=" + value)
        ]
        return self.command("network", "ls", "-q", *flags).stdout.split()


@contextmanager
def trial_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _fixture(context, action):
    fixture = context.job.workload.fixture
    if fixture and fixture.manifest:
        manifest = json.loads((context.directory / "manifest.json").read_text())
        actual = hashlib.sha256(fixture.manifest.read_bytes()).hexdigest()
        if actual != manifest.get("fixture_manifest_sha256"):
            raise ValueError("fixture identity changed since trial preparation")
    if context.job.workload.adapter == "social":
        from .social import social_adapter

        return getattr(social_adapter(context.job.workload), action)(context)
    if fixture:
        if fixture.adapter != "hooks":
            raise ValueError("unknown fixture adapter")
        hook = getattr(fixture, action, None)
        if hook:
            return run_hook(hook, context)


def _cleanup(context, journal):
    labels = owner_labels(context.queue_id, context.job_id, context.trial_id)
    removed, reasons, leases = [], [], []
    entries = journal.entries()
    if entries and entries[-1]["action"] == "cleanup" and entries[-1]["state"] == "done":
        return CleanupReceipt(True)
    journal.record("cleanup", "intent")
    hosts = [context.environment.generator]
    if context.environment.backend != context.environment.generator:
        hosts.append(context.environment.backend)
    for host in hosts:
        docker = OwnedDocker(host, labels)
        try:
            for identity in docker.owned():
                info = docker.inspect(identity)
                if info.get("Name") == "/bench-engine-lease":
                    leases.append((docker, identity))
                else:
                    if (
                        info.get("Name", "").startswith("/bench-load-")
                        and not (context.directory / "load-log.json").exists()
                    ):
                        docker.command("stop", "--time", "1", identity)
                        result = docker.command("logs", identity)
                        immutable_json(
                            context.directory / "load-log.json",
                            {"log": result.stdout + result.stderr},
                        )
                    docker.remove(identity)
                    removed.append(identity)
        except Exception as exc:
            reasons.append("owned container cleanup failed: " + type(exc).__name__)
    for host in hosts:
        docker = OwnedDocker(host, labels)
        try:
            for identity in docker.owned_networks():
                docker.remove_network(identity)
                removed.append(identity)
        except Exception as exc:
            reasons.append("owned network cleanup failed: " + type(exc).__name__)
    fixture_started = any(entry["action"] == "fixture" for entry in entries)
    if not reasons and fixture_started and not leases:
        reasons.append("fixture reconciliation requires retained ownership lease")
    if not reasons and fixture_started:
        try:
            journal.record("restore", "intent")
            _fixture(context, "reset")
            _fixture(context, "verify")
            journal.record("restore", "done")
        except Exception as exc:
            reasons.append("fixture restore failed: " + type(exc).__name__)
    if not reasons:
        for entry in entries:
            if entry["action"] != "stage_workload" or entry["state"] != "intent":
                continue
            path = entry["details"]["path"]
            code = "import pathlib,json,sys,shutil; p=pathlib.Path(sys.argv[1]); expected=json.loads(sys.argv[2]);\nif p.exists():\n if p.is_symlink() or json.loads((p/'.owner.json').read_text())!=expected: raise RuntimeError('staged ownership mismatch')\n shutil.rmtree(p)"
            try:
                execute_host(
                    context.environment.generator, ["python3", "-c", code, path, json.dumps(labels)]
                )
            except Exception:
                reasons.append("staged workload cleanup failed")
    if not reasons:
        for docker, identity in leases:
            try:
                docker.remove(identity)
                removed.append(identity)
            except Exception as exc:
                reasons.append("lease cleanup failed: " + type(exc).__name__)
    for host in hosts:
        try:
            if OwnedDocker(host, labels).owned() or OwnedDocker(host, labels).owned_networks():
                reasons.append("owned resources remain")
        except Exception:
            reasons.append("cleanup verification unavailable")
    receipt = CleanupReceipt(not reasons, tuple(removed), tuple(reasons))
    journal.record(
        "cleanup",
        "done" if receipt.verified else "failed",
        {"removed": removed, "reasons": reasons},
    )
    return receipt


def reconcile_trial(context):
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


def _baseline(context, phase, allowed, journal, stop_requested):
    policy = context.environment.baseline
    if not policy.enabled:
        journal.record("baseline_" + phase, "disabled")
        return
    docker = OwnedDocker(context.environment.backend, {})
    previous, good = None, 0
    deadline = time.monotonic() + policy.timeout
    journal.record("baseline_" + phase, "intent")
    while time.monotonic() < deadline:
        if stop_requested():
            raise InterruptedError("stop requested")
        point = json.loads(
            execute_host(
                context.environment.backend, ["python3", "-c", HOST_PROBE], timeout=15
            ).stdout
        )
        running = docker.command("ps", "--no-trunc", "--format", "{{json .}}").stdout
        foreign = [
            p
            for p in (json.loads(row) for row in running.splitlines())
            if p["ID"] not in allowed and p["Names"] not in policy.allowed_containers
        ]
        cpu = None
        if previous:
            delta = [b - a for a, b in zip(previous["cpu"], point["cpu"])]
            cpu = 1 - (delta[3] + delta[4]) / sum(delta) if sum(delta) > 0 else None
        previous = point
        okay = (
            cpu is not None
            and cpu <= getattr(policy, phase + "_cpu_max")
            and point["memory_available"] >= getattr(policy, phase + "_memory_available")
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


def _bundle(context, prepared):
    bundle = context.directory / "execution"
    bundle.mkdir()
    shutil.copytree(prepared.directory, bundle / "workload")
    for name in ("runner.js", "metrics.js"):
        shutil.copy(ROOT / "benchmarks/k6/engine" / name, bundle / name)
    runner = bundle / "runner.js"
    entry = "./workload/" + prepared.script.relative_to(prepared.directory).as_posix()
    runner.write_text(runner.read_text().replace("'./workload.js'", json.dumps(entry)))
    immutable_json(
        bundle / ".owner.json", owner_labels(context.queue_id, context.job_id, context.trial_id)
    )
    return bundle


def run_trial(
    context: TrialContext, stop_requested: Callable[[], bool] = lambda: False
) -> TrialEvidence:
    with trial_lock(context.directory):
        return _run_trial(context, stop_requested)


def _run_trial(context, stop_requested):
    directory = context.directory.resolve()
    if (directory / "manifest.json").exists():
        raise ValueError("trial already exists: reconcile and use a new trial identity")
    job = context.job
    stages = resolve_stages(job, context.users)
    for value in [*(s.duration for s in stages), job.drain, job.graceful_ramp_down]:
        if abs(value * 1000 - round(value * 1000)) > 1e-7:
            raise ValueError("k6 durations require exact millisecond precision")
    prepared_target = context.prepared_target or resolve_target(
        job.target, job.environment, snapshot_directory=directory / "build"
    )
    prepared_workload = context.prepared_workload or prepare_workload(
        job.workload, directory / "workload"
    )
    from .targets import tree_checksums

    if tree_checksums(prepared_workload.directory) != prepared_workload.checksums:
        raise ValueError("prepared workload checksum mismatch")
    bundle = _bundle(context, prepared_workload)
    generator_image_id = (
        context.generator_image_id
        or json.loads(
            execute_host(
                job.environment.generator, ["docker", "image", "inspect", "grafana/k6:1.4.2"]
            ).stdout
        )[0]["Id"]
    )
    execution_id = prepared_execution_identity(
        job,
        context.users,
        prepared_target,
        prepared_workload,
        generator_image_id=generator_image_id,
    )
    labels = owner_labels(context.queue_id, context.job_id, context.trial_id)
    if any(key.startswith("bench.") for key in job.target.labels):
        raise ValueError("target cannot override reserved owner labels")
    manifest = {
        "version": 1,
        "queue_id": context.queue_id,
        "trial_id": context.trial_id,
        "users": context.users,
        "job": job.model_dump(mode="json"),
        "execution_id": execution_id,
        "image_id": prepared_target.image_id,
        "generator_image_id": generator_image_id,
        "workload_identity": prepared_workload.identity,
        "fixture_manifest_sha256": hashlib.sha256(
            job.workload.fixture.manifest.read_bytes()
        ).hexdigest()
        if job.workload.fixture and job.workload.fixture.manifest
        else None,
        "wrapper_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.glob("*.js")
        },
        "colocated_generator": job.environment.backend == job.environment.generator,
    }
    immutable_json(directory / "manifest.json", manifest)
    journal = Journal(directory)
    backend = OwnedDocker(job.environment.backend, labels)
    generator = OwnedDocker(job.environment.generator, labels)
    unique = hashlib.sha256(json.dumps(labels, sort_keys=True).encode()).hexdigest()[:20]
    collectors, collection, reasons, samples, parsed = (), None, [], [], None
    load = None
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
        for key, ref in job.target.env.items():
            args.extend(["-e", key + "=" + os.environ[ref.env]])
        for key, value in job.target.labels.items():
            args.extend(["--label", key + "=" + value])
        if job.workload.adapter == "social":
            args += ["-e", "RUN_ID=" + context.trial_id, "-e", "RUN_DIR=/tmp/bench-runtime"]
        args += [prepared_target.image_id, *(job.target.command or [])]
        if stop_requested():
            raise InterruptedError("stop requested")
        journal.record("deploy", "intent", {"name": "bench-target-" + unique})
        info = backend.create("bench-target-" + unique, args)
        identity = info["Id"]
        info = backend.start(identity)
        verify_resources(job.target, info)
        if info["Image"] != prepared_target.image_id:
            raise ValueError("deployed image identity mismatch")
        port = (
            job.target.port
            if job.environment.network.mode == "host"
            else int(info["NetworkSettings"]["Ports"][f"{job.target.port}/tcp"][0]["HostPort"])
        )
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
