"""Serial, restartable queue controller. SQLite checkpoints precede side effects.

Foreground use requires neither systemd nor a daemon. The process lock protects
checkpoints; exact persisted host reservations protect aliases/state directories.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from tools.bench.engine.evidence import immutable_json
from tools.bench.engine.metrics import evaluate
from tools.bench.engine.records import DockerImage
from tools.bench.engine.search import (
    JobBudget,
    SearchPolicy,
    SearchState,
    next_probe,
    record_probe,
    reserve_attempt,
    reuse_evidence,
    search_result,
)
from tools.bench.engine.stages import fixed_populations
from tools.bench.engine.targets import (
    ResolvedTarget,
    execute_host,
    prepared_execution_identity,
    resolve_target,
)
from tools.bench.engine.trial import TrialContext, run_trial
from tools.bench.engine.trial_context import PreparedFixture
from tools.bench.engine.types import Host, ResolvedJob, TrialEvidence
from tools.bench.engine.workloads import PreparedWorkload
from tools.bench.records import checked_record

from .inputs import prepared_fixture
from .leases import ControllerLock, HostLease, LeaseReceipt
from .preparation import MAX_RECOVERY_ATTEMPTS as MAX_RECOVERY_ATTEMPTS
from .preparation import prepare
from .records import (
    Budget,
    ControllerState,
    Decision,
    FrozenInputs,
    JobCheckpoint,
    JobResult,
    LeaseRecord,
    SearchCheckpoint,
)
from .records import QueueSnapshot as QueueSnapshot
from .recovery import recover_pending
from .store import ClaimedJob, QueueConflict, QueueStore

StopRequested = Callable[[], bool]


class _Stopped(Exception):
    pass


def _root(store: QueueStore, queue: str) -> Path:
    return store.path.parent / "runs" / hashlib.sha256(queue.encode()).hexdigest()[:24]


def _save(store: QueueStore, queue: str, state: ControllerState, status: str | None = None) -> None:
    store.checkpoint(queue, state, state=status)


def _check_stop(store: QueueStore, queue: str, interrupt_requested: StopRequested = lambda: False) -> None:
    if interrupt_requested() or store.stop_request(queue) is False:
        raise _Stopped()


def _inspect_image(host: Host, image: str) -> str:
    try:
        result = json.loads(
            execute_host(host, ["docker", "image", "inspect", image], timeout=30).stdout
        )
        actual = checked_record(result, list[DockerImage])[0]["Id"]
    except Exception as exc:
        raise QueueConflict(
            "pinned image missing or unavailable; restore it before resume"
        ) from exc
    if image.startswith("sha256:") and actual != image:
        raise QueueConflict("pinned image identity changed")
    return actual


def _identity(context: TrialContext) -> str:
    assert context.prepared_target is not None and context.prepared_workload is not None
    assert context.generator_image_id is not None
    return prepared_execution_identity(
        context.job,
        context.users,
        context.prepared_target,
        context.prepared_workload,
        generator_image_id=context.generator_image_id,
        fixture_manifest=context.fixture_manifest,
    )


def _prepare(store: QueueStore, queue: str, state: ControllerState, data: JobCheckpoint, index: int, job: ResolvedJob, interrupt_requested: StopRequested = lambda: False, *, expected_inputs: FrozenInputs) -> tuple[ResolvedTarget, PreparedWorkload, str]:
    return prepare(
        data, index, job, expected_inputs=expected_inputs,
        root=_root(store, queue),
        save=lambda status: _save(store, queue, state, status),
        check_stop=lambda: _check_stop(store, queue, interrupt_requested),
        resolve=resolve_target, inspect_image=_inspect_image,
        execution_identity=prepared_execution_identity,
    )

def _reserve_hosts(store: QueueStore, queue: str, state: ControllerState, job: ResolvedJob, target: ResolvedTarget, generator: str) -> None:
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
            "receipt": checked_record(asdict(receipt), LeaseRecord),
            "releasing": False,
        }
        _save(store, queue, state)


def _release_hosts(store: QueueStore, queue: str, state: ControllerState) -> None:
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


def _restore_hosts(store: QueueStore, queue: str, state: ControllerState, lock: ControllerLock) -> None:
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


def _recover_pending(store: QueueStore, queue: str, state: ControllerState, data: JobCheckpoint) -> None:
    recover_pending(data, save=lambda status: _save(store, queue, state, status))

def _retry_key(data: JobCheckpoint, job: ResolvedJob, users: int | None, objective: str) -> str:
    population = users if job.test.type == "search" else data["cursor"]
    return f"{data['target']}:{objective}:{population}"


def _decide(store: QueueStore, queue: str, state: ControllerState, data: JobCheckpoint, job: ResolvedJob, interrupt_requested: StopRequested = lambda: False) -> None:
    pending = data["pending"]
    assert pending is not None and "evidence" in pending
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
    retry_key = _retry_key(data, job, pending["users"], pending["objective"])
    cancelled_now = interrupt_requested() or store.stop_request(queue) is False
    interrupted = pending.get("interrupted", False) or cancelled_now
    evaluation = evaluate(evidence, criteria)
    # Evidence invalidity is authoritative even for an empty criteria set.
    if not evidence.valid:
        from tools.bench.engine.types import Evaluation

        evaluation = Evaluation(verdict="invalid", reasons=evidence.invalid_reasons)
    if job.test.type == "search":
        search = SearchState.model_validate_json(json.dumps(data["search"]))
        assert pending["users"] is not None
        search = record_probe(search, pending["users"], pending["id"], evaluation, now=time.time())
        data["search"] = checked_record(search.model_dump(mode="json"), SearchCheckpoint)
        data["budget"] = checked_record(search.budget.model_dump(mode="json"), Budget)
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
    retries = data.setdefault("invalid_retries", {})
    interruptions = data.setdefault("interruptions", {})
    if evaluation.valid:
        retries.pop(retry_key, None)
    elif interrupted:
        interruptions[retry_key] = interruptions.get(retry_key, 0) + 1
    else:
        retries[retry_key] = retries.get(retry_key, 0) + 1
    data.setdefault("raw_trials", {})[pending["id"]] = {
        "target": data["target"],
        "objective": pending["objective"],
        "path": pending["path"],
        "identity": pending["identity"],
    }
    data["pending"] = None
    _save(store, queue, state)
    if not evaluation.valid:
        if cancelled_now:
            raise _Stopped()
        if interruptions.get(retry_key, 0) > MAX_RECOVERY_ATTEMPTS:
            raise QueueConflict("interruption recovery budget exhausted")
        if retries.get(retry_key, 0) > job.test.invalid_retries:
            raise QueueConflict("invalid retry budget exhausted; needs attention")
        # Cleanup-verified invalid attempts retry the same candidate. Search
        # reservation/time limits remain authoritative and charge every launch.


def _fixed_users(job: ResolvedJob) -> list[int | None]:
    return [u for u in fixed_populations(job) for _ in range(job.test.repetitions)]


def _reuse_probes(store: QueueStore, queue: str, data: JobCheckpoint, job: ResolvedJob, target: ResolvedTarget, workload: PreparedWorkload, generator: str, objective: str, search: SearchState, fixture: PreparedFixture | None = None) -> tuple[SearchState, list[Decision]]:
    """Reclassify immutable raw probes; previous objective labels are irrelevant."""
    retained = dict(data.get("raw_trials", {}))
    # Older controller checkpoints retained raw paths in the trial table only.
    paths = {row["id"]: row["path"] for row in store.snapshot(queue)["trials"]}
    for decision in data["decisions"]:
        trial_id = decision["trial_id"]
        if trial_id not in retained and trial_id in paths:
            retained[trial_id] = {"target": decision["target"], "objective": decision["objective"], "path": paths[trial_id]}
    decisions: list[Decision] = []
    criteria = tuple(job.criteria + job.objectives.get(objective, []))
    for trial_id, source in retained.items():
        if source["target"] != data["target"] or any(
            o.trial_id == trial_id for o in search.observations
        ):
            continue
        path = Path(source["path"]) / "result.json"
        if not path.exists():
            continue
        evidence = TrialEvidence.model_validate_json(path.read_text())
        if not evidence.valid or not evidence.cleanup_verified:
            continue
        expected = prepared_execution_identity(
            job, evidence.users, target, workload, generator_image_id=generator,
            fixture_manifest=fixture.verify() if fixture else None
        )
        if evidence.trial_id != trial_id or evidence.execution_id != expected:
            continue
        try:
            updated = reuse_evidence(search, evidence, execution_id=expected, criteria=criteria)
        except ValueError as exc:
            # The new criteria can settle an endpoint before all historical
            # confirmations are consumed. Never add a fourth/decided vote.
            if str(exc) == "candidate already decided; no fourth vote":
                continue
            raise
        search = updated
        decisions.append(
            {
                "trial_id": trial_id,
                "target": data["target"],
                "objective": objective,
                "evaluation": search.observations[-1].evaluation.model_dump(mode="json"),
                "reused": True,
                "source_objective": source["objective"],
            }
        )
    return search, decisions


def _execute_job(store: QueueStore, queue: str, state: ControllerState, claimed: ClaimedJob, interrupt_requested: StopRequested = lambda: False) -> None:
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
            "budget": checked_record(JobBudget(started_at=now, now=now).model_dump(mode="json"), Budget),
            "decisions": [],
            "results": [],
            "fixed_attempts": {},
        }
        state["jobs"][claimed.job_id] = data
        _save(store, queue, state)
    while data["target"] < len(claimed.resolved_jobs):
        job = claimed.resolved_jobs[data["target"]]
        target, workload, generator = _prepare(
            store, queue, state, data, data["target"], job, interrupt_requested, expected_inputs=claimed.inputs
        )
        fixture = prepared_fixture(data["prepared"][str(data["target"])]["workload"])
        # Verify engine/fixture/prepared identity before consuming retained evidence.
        if data["pending"]:
            p = data["pending"]
            context = TrialContext(
                job, p["users"], Path(p["path"]), queue, p["id"], target, workload, generator, fixture
            )
            if _identity(context) != p["identity"]:
                raise QueueConflict("execution identity changed; cannot resume")
            _decide(store, queue, state, data, job, interrupt_requested)
        objectives = list(job.objectives) or ["default"]
        while data["objective"] < len(objectives):
            objective = objectives[data["objective"]]
            if job.test.type == "search":
                if data["search"] is None:
                    search = SearchState(
                        policy=_search_policy(job, workload, fixture),
                        budget=JobBudget.model_validate(data["budget"]),
                    )
                    search, reused_decisions = _reuse_probes(
                        store, queue, data, job, target, workload, generator, objective, search, fixture
                    )
                    data["decisions"].extend(reused_decisions)
                else:
                    search = SearchState.model_validate_json(json.dumps(data["search"]))
                search = search.model_copy(update={"budget": search.budget.advance(time.time())})
                data["search"] = checked_record(search.model_dump(mode="json"), SearchCheckpoint)
                data["budget"] = checked_record(search.budget.model_dump(mode="json"), Budget)
                _save(store, queue, state)
                users = next_probe(search)
                if users is None:
                    data["results"].append(
                        checked_record({
                            "target": data["target"],
                            "objective": objective,
                            "observed_only": bool(search.observations)
                            and all(o.reused for o in search.observations),
                            **search_result(search).model_dump(mode="json"),
                        }, JobResult)
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
            _check_stop(store, queue, interrupt_requested)
            retry_key = _retry_key(data, job, users, objective)
            if data.get("invalid_retries", {}).get(retry_key, 0) > job.test.invalid_retries:
                raise QueueConflict("invalid retry budget exhausted; needs attention")
            if data.get("interruptions", {}).get(retry_key, 0) > MAX_RECOVERY_ATTEMPTS:
                raise QueueConflict("interruption recovery budget exhausted")
            _reserve_hosts(store, queue, state, job, target, generator)
            trial_id = uuid4().hex
            directory = _root(store, queue) / data["directory"] / trial_id
            context = TrialContext(
                job, users, directory, queue, trial_id, target, workload, generator, fixture
            )
            identity = _identity(context)
            if job.test.type == "search":
                assert users is not None
                data["search"] = checked_record(reserve_attempt(
                    search, users, trial_id, now=time.time()
                ).model_dump(mode="json"), SearchCheckpoint)
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

            def stop_requested() -> bool:
                nonlocal stopping
                requested = interrupt_requested() or store.stop_request(queue) is False
                if requested and not stopping:
                    stopping = True
                    assert data["pending"] is not None
                    data["pending"]["interrupted"] = True
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
            assert data["pending"] is not None
            data["pending"]["evidence"] = evidence.model_dump(mode="json")
            _save(store, queue, state)
            _decide(store, queue, state, data, job, interrupt_requested)
        data["target"] += 1
        data["cursor"] = 0
        data["objective"] = 0
        data["search"] = None
        _save(store, queue, state)
    store.finish(queue, claimed.job_id, state["owner"])


def _open_state(store: QueueStore, queue: str) -> ControllerState:
    state = store.controller_state(queue)
    if state is None:
        state = {"owner": uuid4().hex, "jobs": {}, "leases": {}, "last_error": None}
        _save(store, queue, state)
    return state


def _reconcile(store: QueueStore, queue: str, state: ControllerState, lock: ControllerLock) -> None:
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


def run_queue(store: QueueStore, queue: str, *, interrupt_requested: StopRequested = lambda: False) -> QueueSnapshot:
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
                if interrupt_requested() or store.stop_request(queue) is not None:
                    status = "interrupted"
                    break
                claimed = store.active_job(queue, state["owner"]) or store.claim_next(
                    queue, state["owner"]
                )
                if claimed is None:
                    break
                clean = False
                _execute_job(store, queue, state, claimed, interrupt_requested)
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


def _search_policy(job: ResolvedJob, prepared: PreparedWorkload | None = None, fixture: PreparedFixture | None = None) -> SearchPolicy:
    ceilings = None
    if job.workload.adapter == 'social':
        from ..engine.social import preflight_social
        spec = job.workload
        if prepared is not None:
            root = spec.script.resolve().parent
            spec = spec.model_copy(update={"script": prepared.script, "dependencies": [prepared.directory / p.resolve().relative_to(root) for p in spec.dependencies]})
        if fixture is not None and spec.fixture is not None:
            spec = spec.model_copy(update={"fixture": spec.fixture.model_copy(update={"manifest": fixture.verify()})})
        capacity = preflight_social(spec)
        ceilings = {'fixture_accounts': capacity['account_capacity']}
    return SearchPolicy.from_test(job.test, feasible_ceilings=ceilings)
