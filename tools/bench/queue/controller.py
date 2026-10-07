"""Serial, restartable queue controller. SQLite checkpoints precede side effects.

Foreground use requires neither systemd nor a daemon. The process lock protects
checkpoints; exact persisted host reservations protect aliases/state directories.
"""

import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from tools.bench.engine.docker_env import validate_container_environment
from tools.bench.engine.evidence import immutable_json
from tools.bench.engine.metrics import evaluate
from tools.bench.engine.search import (
    JobBudget,
    SearchPolicy,
    SearchState,
    next_probe,
    record_probe,
    reserve_attempt,
    search_result,
)
from tools.bench.engine.targets import (
    ResolvedTarget,
    execute_host,
    load_build_snapshot,
    prepared_execution_identity,
    resolve_target,
)
from tools.bench.engine.trial import TrialContext, reconcile_trial, run_trial
from tools.bench.engine.types import Host, TrialEvidence
from tools.bench.engine.workloads import PreparedWorkload, prepare_workload, tree_checksums

from .leases import ControllerLock, HostLease, LeaseReceipt
from .store import QueueConflict, QueueStore

QueueSnapshot = dict
MAX_RECOVERY_ATTEMPTS = 3


class _Stopped(Exception):
    pass


def _root(store, queue):
    return store.path.parent / "runs" / hashlib.sha256(queue.encode()).hexdigest()[:24]


def _save(store, queue, state, status=None):
    store.checkpoint(queue, state, state=status)


def _check_stop(store, queue):
    if store.stop_request(queue) is False:
        raise _Stopped()


def _inspect_image(host, image):
    try:
        result = json.loads(
            execute_host(host, ["docker", "image", "inspect", image], timeout=30).stdout
        )
        actual = result[0]["Id"]
    except Exception as exc:
        raise QueueConflict(
            "pinned image missing or unavailable; restore it before resume"
        ) from exc
    if image.startswith("sha256:") and actual != image:
        raise QueueConflict("pinned image identity changed")
    return actual


def _identity(context):
    return prepared_execution_identity(
        context.job,
        context.users,
        context.prepared_target,
        context.prepared_workload,
        generator_image_id=context.generator_image_id,
    )


def _prepare(store, queue, state, data, index, job):
    # Resolve refs without persisting values; validate before Docker preparation.
    try:
        env = {key: os.environ[ref.env] for key, ref in job.target.env.items()}
    except KeyError as exc:
        raise QueueConflict("required target environment reference is unset") from exc
    try:
        validate_container_environment(env)
    except ValueError as exc:
        raise QueueConflict(
            "target environment requires portable names and single-line values (no CR, LF or NUL)"
        ) from exc
    _check_stop(store, queue)
    key = str(index)
    prep = data["prepared"].get(key)
    directory = _root(store, queue) / data["directory"] / ("prepare-" + key)
    if prep is None:
        data["preparation_attempts"][key] = data["preparation_attempts"].get(key, 0) + 1
        if data["preparation_attempts"][key] > MAX_RECOVERY_ATTEMPTS:
            raise QueueConflict("preparation attempt budget exhausted")
        _save(store, queue, state, "preparing")
        # Each interrupted preparation gets a fresh path; never overwrite receipts.
        directory /= str(data["preparation_attempts"][key])
        retained_target = data.setdefault("target_pins", {}).get(key)
        if retained_target is None:
            target = resolve_target(
                job.target, job.environment, snapshot_directory=directory / "build"
            )
            retained_target = {
                "image": target.image_id,
                "requested_image": target.requested_image,
                "repo_digests": list(target.repo_digests),
                "build": str(target.build_snapshot.context.parent)
                if target.build_snapshot
                else None,
            }
            data["target_pins"][key] = retained_target
            _save(store, queue, state)
        target = ResolvedTarget(
            job.target,
            retained_target["requested_image"],
            retained_target["image"],
            tuple(retained_target["repo_digests"]),
            load_build_snapshot(Path(retained_target["build"]))
            if retained_target["build"]
            else None,
        )
        _check_stop(store, queue)
        cached = data["workloads"].get(job.workload_name)
        if cached is None:
            workload = prepare_workload(job.workload, directory / "workload")
            cached = {
                "directory": str(workload.directory),
                "script": str(workload.script),
                "checksums": workload.checksums,
                "identity": workload.identity,
            }
            data["workloads"][job.workload_name] = cached
        generator = _inspect_image(job.environment.generator, "grafana/k6:1.4.2")
        prep = {
            "image": target.image_id,
            "requested_image": target.requested_image,
            "repo_digests": list(target.repo_digests),
            "build": str(target.build_snapshot.context.parent) if target.build_snapshot else None,
            "workload": cached,
            "generator": generator,
        }
        data["prepared"][key] = prep
        _save(store, queue, state)
    build = load_build_snapshot(Path(prep["build"])) if prep["build"] else None
    target = ResolvedTarget(
        job.target, prep["requested_image"], prep["image"], tuple(prep["repo_digests"]), build
    )
    work = prep["workload"]
    workload = PreparedWorkload(
        Path(work["directory"]), Path(work["script"]), work["checksums"], work["identity"]
    )
    if tree_checksums(workload.directory) != workload.checksums:
        raise QueueConflict("prepared workload changed")
    _inspect_image(job.environment.backend, target.image_id)
    _inspect_image(job.environment.generator, prep["generator"])
    identity_users = (
        job.test.users.min if job.test.type == "search" else (job.test.users or [None])[0]
    )
    identity = prepared_execution_identity(
        job, identity_users, target, workload, generator_image_id=prep["generator"]
    )
    if prep.get("execution_identity", identity) != identity:
        raise QueueConflict("execution identity changed; cannot resume")
    prep["execution_identity"] = identity
    _save(store, queue, state)
    _check_stop(store, queue)
    return target, workload, prep["generator"]


def _reserve_hosts(store, queue, state, job, target, generator):
    for host, image in (
        (job.environment.backend, target.image_id),
        (job.environment.generator, generator),
    ):
        lease = HostLease(host, queue, state["owner"])
        identity = lease.identity()
        if identity in state["leases"]:
            continue
        receipt = lease.acquire(image)
        state["leases"][identity] = {
            "host": host.model_dump(mode="json"),
            "receipt": asdict(receipt),
            "releasing": False,
        }
        _save(store, queue, state)


def _release_hosts(store, queue, state):
    for identity, retained in list(state["leases"].items()):
        lease = HostLease(Host.model_validate(retained["host"]), queue, state["owner"])
        receipt = LeaseReceipt(**retained["receipt"])
        retained["releasing"] = True
        _save(store, queue, state, "cleaning")
        # A prior successful removal may have crashed before this checkpoint.
        if lease.identity() != receipt.host_identity:
            raise QueueConflict("reservation host identity changed")
        if lease.docker.exists(receipt.container_id):
            lease.release(receipt, cleanup_verified=True)
        del state["leases"][identity]
        _save(store, queue, state)


def _restore_hosts(store, queue, state, lock):
    for retained in state["leases"].values():
        lease = HostLease(Host.model_validate(retained["host"]), queue, state["owner"])
        receipt = LeaseReceipt(**retained["receipt"])
        if retained["releasing"]:
            if lease.identity() != receipt.host_identity:
                raise QueueConflict("reservation host identity changed")
            if not lease.docker.exists(receipt.container_id):
                continue
        lease.restore(receipt, controller_lock=lock, cleanup_verified=True)
    # Complete any durable release before dispatch; don't reuse missing receipts.
    if any(v["releasing"] for v in state["leases"].values()):
        _release_hosts(store, queue, state)


def _recover_pending(store, queue, state, data):
    pending = data.get("pending")
    if not pending:
        return
    directory = Path(pending["path"])
    result = directory / "result.json"
    if result.exists():
        evidence = TrialEvidence.model_validate_json(result.read_text())
        if not evidence.cleanup_verified:
            raise QueueConflict("final result lacks verified cleanup")
    else:
        attempts = pending.get("recovery_attempts", 0)
        if attempts >= MAX_RECOVERY_ATTEMPTS:
            raise QueueConflict("cleanup recovery budget exhausted")
        pending["recovery_attempts"] = attempts + 1
        _save(store, queue, state, "cleaning")
        manifest = directory / "manifest.json"
        if manifest.exists():
            receipt = reconcile_trial(TrialContext.from_manifest(manifest))
            if not receipt.verified:
                raise QueueConflict("trial cleanup requires attention")
        # Before manifest publication lifecycle has acquired no trial resources.
        evidence = TrialEvidence(
            trial_id=pending["id"],
            users=pending["users"],
            execution_id=pending["identity"],
            valid=False,
            invalid_reasons=("interrupted before finalized measurement",),
            cleanup_verified=True,
        )
        if not (directory / "recovered.json").exists():
            immutable_json(directory / "recovered.json", evidence.model_dump(mode="json"))
    if (
        evidence.execution_id != pending["identity"]
        or evidence.trial_id != pending["id"]
        or evidence.users != pending["users"]
    ):
        raise QueueConflict("trial evidence identity differs from reservation")
    pending["evidence"] = evidence.model_dump(mode="json")
    _save(store, queue, state)


def _decide(store, queue, state, data, job):
    pending = data["pending"]
    evidence = TrialEvidence.model_validate_json(json.dumps(pending["evidence"]))
    if not evidence.cleanup_verified:
        raise QueueConflict("cleanup not verified")
    # Publish evidence status before clearing the outstanding decision. A crash
    # at either checkpoint then replays the same finalized evidence safely.
    store.record_trial(
        queue,
        data["job_id"],
        state["owner"],
        pending["id"],
        Path(pending["path"]),
        state="finalized",
    )
    criteria = tuple(job.criteria + job.objectives.get(pending["objective"], []))
    evaluation = evaluate(evidence, criteria)
    # Evidence invalidity is authoritative even for an empty criteria set.
    if not evidence.valid:
        from tools.bench.engine.types import Evaluation

        evaluation = Evaluation(verdict="invalid", reasons=evidence.invalid_reasons)
    if job.test.type == "search":
        search = SearchState.model_validate_json(json.dumps(data["search"]))
        search = record_probe(search, pending["users"], pending["id"], evaluation, now=time.time())
        data["search"] = search.model_dump(mode="json")
        data["budget"] = search.budget.model_dump(mode="json")
    elif evaluation.valid:
        data["cursor"] += 1
        if evaluation.verdict == "fail" and job.test.stop_on_failure:
            data["cursor"] = len(_fixed_users(job))
    data["decisions"].append(
        {
            "trial_id": pending["id"],
            "target": data["target"],
            "objective": pending["objective"],
            "evaluation": evaluation.model_dump(mode="json"),
        }
    )
    data["pending"] = None
    _save(store, queue, state)
    if not evidence.cleanup_verified:
        raise QueueConflict("cleanup not verified")
    if not evaluation.valid:
        if store.stop_request(queue) is False:
            raise _Stopped()
        raise QueueConflict("invalid infrastructure evidence; explicit resume required")


def _fixed_users(job):
    return [u for u in (job.test.users or [None]) for _ in range(job.test.repetitions)]


def _execute_job(store, queue, state, claimed):
    data = state["jobs"].get(claimed.job_id)
    if data is None:
        now = time.time()
        data = {
            "job_id": claimed.job_id,
            "directory": uuid4().hex,
            "target": 0,
            "cursor": 0,
            "objective": 0,
            "prepared": {},
            "workloads": {},
            "preparation_attempts": {},
            "pending": None,
            "search": None,
            "budget": JobBudget(started_at=now, now=now).model_dump(mode="json"),
            "decisions": [],
            "results": [],
            "fixed_attempts": {},
        }
        state["jobs"][claimed.job_id] = data
        _save(store, queue, state)
    while data["target"] < len(claimed.resolved_jobs):
        job = claimed.resolved_jobs[data["target"]]
        target, workload, generator = _prepare(store, queue, state, data, data["target"], job)
        # Verify engine/fixture/prepared identity before consuming retained evidence.
        if data["pending"]:
            p = data["pending"]
            context = TrialContext(
                job, p["users"], Path(p["path"]), queue, p["id"], target, workload, generator
            )
            if _identity(context) != p["identity"]:
                raise QueueConflict("execution identity changed; cannot resume")
            _decide(store, queue, state, data, job)
        objectives = list(job.objectives) or ["default"]
        while data["objective"] < len(objectives):
            objective = objectives[data["objective"]]
            if job.test.type == "search":
                if data["search"] is None:
                    search = SearchState(
                        policy=SearchPolicy.from_test(job.test),
                        budget=JobBudget.model_validate(data["budget"]),
                    )
                else:
                    search = SearchState.model_validate_json(json.dumps(data["search"]))
                search = search.model_copy(update={"budget": search.budget.advance(time.time())})
                data["search"] = search.model_dump(mode="json")
                data["budget"] = search.budget.model_dump(mode="json")
                _save(store, queue, state)
                users = next_probe(search)
                if users is None:
                    data["results"].append(
                        {
                            "target": data["target"],
                            "objective": objective,
                            **search_result(search).model_dump(mode="json"),
                        }
                    )
                    data["objective"] += 1
                    data["search"] = None
                    _save(store, queue, state)
                    continue
            else:
                values = _fixed_users(job)
                if data["cursor"] >= len(values):
                    data["objective"] = len(objectives)
                    break
                users = values[data["cursor"]]
            _check_stop(store, queue)
            _reserve_hosts(store, queue, state, job, target, generator)
            trial_id = uuid4().hex
            directory = _root(store, queue) / data["directory"] / trial_id
            context = TrialContext(
                job, users, directory, queue, trial_id, target, workload, generator
            )
            identity = _identity(context)
            if job.test.type == "search":
                data["search"] = reserve_attempt(
                    search, users, trial_id, now=time.time()
                ).model_dump(mode="json")
            else:
                key = f"{data['target']}:{data['cursor']}"
                count = data["fixed_attempts"].get(key, 0)
                if count >= 1 + job.test.invalid_retries + MAX_RECOVERY_ATTEMPTS:
                    raise QueueConflict("fixed trial attempt budget exhausted")
                data["fixed_attempts"][key] = count + 1
            data["pending"] = {
                "id": trial_id,
                "users": users,
                "path": str(directory),
                "identity": identity,
                "objective": objective,
            }
            _save(store, queue, state, "running")
            store.record_trial(queue, claimed.job_id, state["owner"], trial_id, directory)
            stopping = False

            def stop_requested():
                nonlocal stopping
                requested = store.stop_request(queue) is False
                if requested and not stopping:
                    stopping = True
                    _save(store, queue, state, "stopping")
                return requested

            evidence = run_trial(context, stop_requested)
            if not evidence.cleanup_verified:
                _save(store, queue, state, "needs_attention")
                raise QueueConflict("trial cleanup not verified")
            # run_trial publishes immutable result.json before returning. Test and
            # custom runners must meet the same publication contract.
            if not (directory / "result.json").exists():
                immutable_json(directory / "result.json", evidence.model_dump(mode="json"))
            data["pending"]["evidence"] = evidence.model_dump(mode="json")
            _save(store, queue, state)
            _decide(store, queue, state, data, job)
        data["target"] += 1
        data["cursor"] = 0
        data["objective"] = 0
        data["search"] = None
        _save(store, queue, state)
    store.finish(queue, claimed.job_id, state["owner"])


def _open_state(store, queue):
    state = store.controller_state(queue)
    if state is None:
        state = {"owner": uuid4().hex, "jobs": {}, "leases": {}, "last_error": None}
        _save(store, queue, state)
    return state


def _reconcile(store, queue, state, lock):
    for data in state["jobs"].values():
        if data.get("pending"):
            _recover_pending(store, queue, state, data)
    _restore_hosts(store, queue, state, lock)


def reconcile_queue(store: QueueStore, queue: str) -> QueueSnapshot:
    """Reconcile retained trials without dispatching or clearing a stop request."""
    with ControllerLock(store.path.with_suffix(".controller.lock")) as lock:
        state = _open_state(store, queue)
        try:
            _reconcile(store, queue, state, lock)
            _release_hosts(store, queue, state)
            _save(store, queue, state, "interrupted")
        except Exception as exc:
            state["last_error"] = str(exc) if isinstance(exc, QueueConflict) else type(exc).__name__
            _save(store, queue, state, "needs_attention")
    return store.snapshot(queue)


def run_queue(store: QueueStore, queue: str) -> QueueSnapshot:
    """Run until completed, deliberately paused, or attention is required.

    Explicit resume calls clear_stop before this function. Active jobs retain
    ownership/cursors on interruption; pending edits remain store-controlled.
    Errors are sanitized in durable state; detailed secret-bearing hook output
    is never retained. A needs_attention snapshot is never a successful run.
    """
    with ControllerLock(store.path.with_suffix(".controller.lock")) as lock:
        state = _open_state(store, queue)
        clean = False
        status = "completed"
        try:
            _reconcile(store, queue, state, lock)
            clean = True
            while True:
                if store.stop_request(queue) is not None:
                    status = "interrupted"
                    break
                claimed = store.active_job(queue, state["owner"]) or store.claim_next(
                    queue, state["owner"]
                )
                if claimed is None:
                    break
                clean = False
                _execute_job(store, queue, state, claimed)
                clean = True
        except _Stopped:
            status = "interrupted"
            # Stops are only raised outside the lifecycle, after verified cleanup.
            clean = True
        except Exception as exc:
            status = "needs_attention"
            state["last_error"] = str(exc) if isinstance(exc, QueueConflict) else type(exc).__name__
            # Reconcile before releasing ownership, even for prepare/clock errors.
            try:
                _reconcile(store, queue, state, lock)
                clean = True
            except Exception:
                clean = False
        except BaseException:
            status = "interrupted"
            raise
        finally:
            if clean:
                try:
                    _release_hosts(store, queue, state)
                except Exception as exc:
                    status = "needs_attention"
                    state["last_error"] = (
                        str(exc) if isinstance(exc, QueueConflict) else type(exc).__name__
                    )
            _save(store, queue, state, status)
    return store.snapshot(queue)
