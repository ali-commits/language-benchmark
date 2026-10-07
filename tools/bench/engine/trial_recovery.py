"""Durable fixture restoration and exact-ID ownership lease release."""

import hashlib
import json
from typing import Callable, Literal

from ..models import FixtureManifest
from ..records import checked_record
from .evidence import Journal, immutable_json
from .owned_docker import OwnedDocker
from .records import JournalEntry, Manifest, ReleaseDetails, RestoreDetails, StageDetails
from .targets import HostExecutor, owner_labels
from .trial_context import CleanupReceipt, TrialContext
from .types import Host
from .workloads import HookReceipt, run_hook

FixtureAction = Literal["prepare", "reset", "verify"]
FixtureReceipt = FixtureManifest | HookReceipt | None
FixtureCallback = Callable[[TrialContext, FixtureAction], FixtureReceipt]
DockerFactory = Callable[[Host, dict[str, str]], OwnedDocker]


def fixture(context: TrialContext, action: FixtureAction) -> FixtureReceipt:
    workload = context.fixture_workload
    fixture = workload.fixture
    if fixture and fixture.manifest:
        manifest = checked_record(
            json.loads((context.directory / "manifest.json").read_text()), Manifest
        )
        actual = hashlib.sha256(fixture.manifest.read_bytes()).hexdigest()
        if actual != manifest.get("fixture_manifest_sha256"):
            raise ValueError("fixture identity changed since trial preparation")
    if context.job.workload.adapter == "social":
        from .social import social_adapter

        adapter = social_adapter(workload)
        if action == "reset":
            return adapter.reset(context)
        if action == "prepare":
            adapter.prepare(context)
        else:
            adapter.verify(context)
        return None
    if fixture:
        if fixture.adapter != "hooks":
            raise ValueError("unknown fixture adapter")
        hook = {"prepare": fixture.prepare, "reset": fixture.reset, "verify": fixture.verify}[
            action
        ]
        if hook:
            return run_hook(
                hook,
                context,
                resolved_env=context.bound_environment.values
                if context.bound_environment
                else None,
            )
    return None


def fixture_receipt_identity(context: TrialContext) -> str:
    fixture = context.job.workload.fixture
    return hashlib.sha256(
        json.dumps(fixture.model_dump(mode="json") if fixture else None, sort_keys=True).encode()
    ).hexdigest()


def finish_release(
    context: TrialContext,
    journal: Journal,
    checkpoint: JournalEntry,
    *,
    docker_factory: DockerFactory,
) -> CleanupReceipt:
    """Resume only a durable, verified restore checkpoint; never reopen fixtures."""
    labels = owner_labels(context.queue_id, context.job_id, context.trial_id)
    hosts = [context.environment.generator]
    if context.environment.backend != context.environment.generator:
        hosts.append(context.environment.backend)
    entries = journal.entries()
    removed, reasons = [], []
    try:
        details = checked_record(checkpoint["details"], ReleaseDetails)
        if details["owner"] != labels or details["fixture_identity"] != fixture_receipt_identity(
            context
        ):
            raise ValueError("release identity mismatch")
        assignments = []
        for lease in details["leases"]:
            host = next(host for host in hosts if host.model_dump(mode="json") == lease["host"])
            assignments.append((host, lease["id"]))
        if len(
            {
                (json.dumps(host.model_dump(mode="json"), sort_keys=True), identity)
                for host, identity in assignments
            }
        ) != len(assignments):
            raise ValueError("duplicate release identity")
        fixture_started = any(
            entry["action"] == "fixture" for entry in entries[: checkpoint["sequence"]]
        )
        if fixture_started:
            restore_sequence = details["restore_sequence"]
            if restore_sequence is None:
                raise ValueError("release lacks verified restore receipt")
            restore = entries[restore_sequence]
            restore_details = checked_record(restore["details"], RestoreDetails)
            if (
                restore["sequence"] >= checkpoint["sequence"]
                or restore["action"] != "restore"
                or restore["state"] != "done"
                or restore_details["owner"] != labels
                or restore_details["fixture_identity"] != details["fixture_identity"]
                or (context.environment.backend, restore_details["backend_lease_id"])
                not in assignments
            ):
                raise ValueError("release lacks verified restore receipt")
        if any(
            entry["action"] in {"fixture", "deploy", "load", "network", "stage_workload", "restore"}
            for entry in entries[checkpoint["sequence"] + 1 :]
        ):
            raise ValueError("mutation after release checkpoint")
        # Before any release, verify that no workload resources remain or appeared
        # after the checkpoint. Unknown resource IDs cannot inherit its receipt.
        for host in hosts:
            docker = docker_factory(host, labels)
            expected = {identity for owner_host, identity in assignments if owner_host == host}
            if set(docker.owned()) - expected or docker.owned_networks():
                raise ValueError("unexpected resource after restore checkpoint")
            for identity in expected:
                if docker.exists(identity):
                    info = docker.inspect(identity)
                    if info.get("Name") != "/bench-engine-lease":
                        raise ValueError("release resource is not ownership lease")
        for host, identity in assignments:
            docker = docker_factory(host, labels)
            if not docker.exists(identity):
                continue
            journal.record(
                "lease_release", "intent", {"host": host.model_dump(mode="json"), "id": identity}
            )
            try:
                docker.remove(identity)
            except Exception:
                # Successful SSH removal can lose its response. Actual immutable-ID
                # absence is sufficient here because restoration is durable already.
                if docker.exists(identity):
                    raise
            if docker.exists(identity):
                raise ValueError("released lease still exists")
            removed.append(identity)
            journal.record(
                "lease_release", "done", {"host": host.model_dump(mode="json"), "id": identity}
            )
        for host in hosts:
            docker = docker_factory(host, labels)
            if docker.owned() or docker.owned_networks():
                raise ValueError("owned resources remain after release")
    except Exception as exc:
        reasons.append("lease release reconciliation failed: " + type(exc).__name__)
    receipt = CleanupReceipt(not reasons, tuple(removed), tuple(reasons))
    journal.record(
        "cleanup",
        "done" if receipt.verified else "failed",
        {"removed": removed, "reasons": reasons},
    )
    return receipt


def cleanup(
    context: TrialContext,
    journal: Journal,
    *,
    docker_factory: DockerFactory,
    fixture_action: FixtureCallback,
    execute: HostExecutor,
) -> CleanupReceipt:
    labels = owner_labels(context.queue_id, context.job_id, context.trial_id)
    removed: list[str] = []
    reasons: list[str] = []
    leases: list[tuple[OwnedDocker, str]] = []
    entries = journal.entries()
    if entries and entries[-1]["action"] == "cleanup" and entries[-1]["state"] == "done":
        return CleanupReceipt(True)
    journal.record("cleanup", "intent")
    checkpoint = next(
        (
            entry
            for entry in reversed(entries)
            if entry["action"] == "release" and entry["state"] == "intent"
        ),
        None,
    )
    if checkpoint is not None:
        return finish_release(context, journal, checkpoint, docker_factory=docker_factory)
    hosts = [context.environment.generator]
    if context.environment.backend != context.environment.generator:
        hosts.append(context.environment.backend)
    for host in hosts:
        docker = docker_factory(host, labels)
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
        docker = docker_factory(host, labels)
        try:
            for identity in docker.owned_networks():
                docker.remove_network(identity)
                removed.append(identity)
        except Exception as exc:
            reasons.append("owned network cleanup failed: " + type(exc).__name__)
    fixture_started = any(entry["action"] == "fixture" for entry in entries)
    backend_lease = next(
        (identity for docker, identity in leases if docker.host == context.environment.backend),
        None,
    )
    restore_sequence = None
    if not reasons and fixture_started and backend_lease is None:
        reasons.append("fixture reconciliation requires retained ownership lease")
    if not reasons and fixture_started:
        try:
            journal.record("restore", "intent")
            fixture_action(context, "reset")
            fixture_action(context, "verify")
            restore = journal.record(
                "restore",
                "done",
                {
                    "owner": labels,
                    "backend_lease_id": backend_lease,
                    "fixture_identity": fixture_receipt_identity(context),
                },
            )
            restore_sequence = restore["sequence"]
        except Exception as exc:
            reasons.append("fixture restore failed: " + type(exc).__name__)
    if not reasons:
        for entry in entries:
            if entry["action"] != "stage_workload" or entry["state"] != "intent":
                continue
            path = checked_record(entry["details"], StageDetails)["path"]
            code = "import pathlib,json,sys,shutil; p=pathlib.Path(sys.argv[1]); expected=json.loads(sys.argv[2]);\nif p.exists():\n if p.is_symlink() or json.loads((p/'.owner.json').read_text())!=expected: raise RuntimeError('staged ownership mismatch')\n shutil.rmtree(p)"
            try:
                execute(
                    context.environment.generator, ["python3", "-c", code, path, json.dumps(labels)]
                )
            except Exception:
                reasons.append("staged workload cleanup failed")
    if not reasons:
        checkpoint = journal.record(
            "release",
            "intent",
            {
                "owner": labels,
                "fixture_identity": fixture_receipt_identity(context),
                "restore_sequence": restore_sequence,
                "leases": [
                    {"host": docker.host.model_dump(mode="json"), "id": identity}
                    for docker, identity in leases
                ],
            },
        )
        receipt = finish_release(context, journal, checkpoint, docker_factory=docker_factory)
        return CleanupReceipt(receipt.verified, tuple(removed) + receipt.removed, receipt.reasons)
    for host in hosts:
        try:
            if (
                docker_factory(host, labels).owned()
                or docker_factory(host, labels).owned_networks()
            ):
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
