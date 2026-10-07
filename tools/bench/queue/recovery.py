"""Recover one durable pending reservation before any new dispatch."""

from collections.abc import Callable
from pathlib import Path

from tools.bench.engine.evidence import immutable_json
from tools.bench.engine.trial import TrialContext, reconcile_trial
from tools.bench.engine.types import TrialEvidence

from .preparation import MAX_RECOVERY_ATTEMPTS
from .records import JobCheckpoint
from .store import QueueConflict


def recover_pending(data: JobCheckpoint, *, save: Callable[[str | None], None]) -> None:
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
        save("cleaning")
        manifest = directory / "manifest.json"
        if manifest.exists():
            try:
                context = TrialContext.from_manifest(manifest)
            except ValueError as exc:
                raise QueueConflict(
                    "trial preparation receipt unavailable; restore original declared environment "
                    "and private binding key before resume"
                ) from exc
            receipt = reconcile_trial(context)
            if not receipt.verified:
                raise QueueConflict("trial cleanup requires attention")
        pending["interrupted"] = True
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
    save(None)
