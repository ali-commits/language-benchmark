"""Pure, durable integer search. Caller checkpoints reservations before execution.

Times are injected Unix epoch seconds, durable across process and host reboots. Reclassification costs no new execution;
all actual launches, including invalid trials and confirmations, cost one attempt.
"""

from typing import Literal

from pydantic import Field, model_validator
from typing_extensions import Self

from .types import Criterion, Evaluation, Model, SearchUsers, TestPolicy, TrialEvidence


class SearchPolicy(Model):
    min_users: int = Field(default=1, ge=1)
    start: int = Field(ge=1)
    max_users: int | None = Field(default=None, ge=1)
    max_trials: int = Field(default=40, gt=0)
    max_elapsed: float = Field(default=3600.0, gt=0)
    expansion_factor: float = Field(default=2.0, gt=1)
    resolution: int = Field(default=1, gt=0)
    confirmation: Literal["majority_on_disagreement", "single_observation"] = (
        "majority_on_disagreement"
    )
    limiting_reason: str = "user_ceiling"

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.start < self.min_users or (
            self.max_users is not None and self.start > self.max_users
        ):
            raise ValueError("start outside bounds")
        return self

    @classmethod
    def from_test(cls, test: TestPolicy, *, feasible_ceilings: dict[str, int] | None = None) -> "SearchPolicy":
        if test.type != "search" or not isinstance(test.users, SearchUsers) or test.limits is None:
            raise ValueError("fixed tests do not use search confirmation")
        ceilings = dict(feasible_ceilings or {})
        if isinstance(test.users.max, int):
            ceilings["configured_max"] = test.users.max
        if test.limits.hard_users:
            ceilings["hard_users"] = test.limits.hard_users
        reason, ceiling = (
            min(ceilings.items(), key=lambda x: x[1]) if ceilings else ("user_ceiling", None)
        )
        if ceiling is not None and ceiling < test.users.min:
            raise ValueError("feasible ceiling below minimum")
        return cls(
            min_users=test.users.min,
            start=min(test.users.start, ceiling) if ceiling else test.users.start,
            max_users=ceiling,
            max_trials=test.limits.max_trials,
            max_elapsed=test.limits.max_elapsed,
            expansion_factor=test.expansion_factor,
            resolution=test.resolution,
            confirmation=test.confirmation,
            limiting_reason=reason,
        )


class JobBudget(Model):
    attempts: int = Field(default=0, ge=0)
    # Controller supplies time.time() values; zero is useful for injected tests.
    clock: Literal["unix_epoch"] = "unix_epoch"
    started_at: float = Field(default=0.0, ge=0)
    now: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def ordered_time(self) -> Self:
        if self.now < self.started_at:
            raise ValueError("clock precedes job start")
        return self

    def advance(self, now: float | None) -> "JobBudget":
        if now is None:
            return self
        updated = JobBudget(attempts=self.attempts, started_at=self.started_at, now=now)
        if updated.now < self.now:
            raise ValueError("clock moved backwards; refuse a new execution")
        return updated


class Observation(Model):
    users: int = Field(ge=1)
    trial_id: str
    evaluation: Evaluation
    reused: bool = False


class SearchState(Model):
    policy: SearchPolicy
    budget: JobBudget = Field(default_factory=JobBudget)
    observations: tuple[Observation, ...] = ()
    pending_trial_id: str | None = None
    pending_users: int | None = None


class SearchResult(Model):
    status: Literal[
        "searching", "complete", "lower_bound", "below_minimum", "incomplete", "unstable"
    ]
    passing_users: int | None = None
    failing_users: int | None = None
    reason: str | None = None
    contradictions: tuple[tuple[int, int], ...] = ()
    observations: tuple[Observation, ...] = ()
    attempts: int = 0
    confirmation: str


def _decisions(state: SearchState) -> tuple[dict[int, str], list[int]]:
    votes: dict[int, list[str]] = {}
    for o in state.observations:
        if o.evaluation.valid:
            votes.setdefault(o.users, []).append(o.evaluation.verdict)
    decisions = {}
    pending = []
    for users, values in votes.items():
        if state.policy.confirmation == "single_observation":
            decisions[users] = values[0]
        elif len(values) == 1 and values[0] == "pass":
            decisions[users] = "pass"
        elif values.count("pass") >= 2:
            decisions[users] = "pass"
        elif values.count("fail") >= 2:
            decisions[users] = "fail"
        else:
            pending.append(users)
    return decisions, pending


def search_result(state: SearchState) -> SearchResult:
    decisions, pending = _decisions(state)
    passes = [u for u, v in decisions.items() if v == "pass"]
    fails = [u for u, v in decisions.items() if v == "fail"]
    passing = max(passes, default=None)
    failing = min(fails, default=None)
    contradictions = tuple((f, p) for f in sorted(fails) for p in sorted(passes) if f < p)
    status: Literal["searching", "complete", "lower_bound", "below_minimum", "incomplete", "unstable"] = "searching"
    reason = None
    if contradictions:
        status = "unstable"
        reason = "contradictory_measured_counts"
    elif not pending:
        if failing == state.policy.min_users and passing is None:
            status = "below_minimum"
        elif (
            passing is not None
            and failing is not None
            and failing - passing <= state.policy.resolution
        ):
            status = "complete"
        elif passing is not None and passing == state.policy.max_users:
            status = "lower_bound"
            reason = state.policy.limiting_reason
    if status == "searching" and (
        state.budget.attempts >= state.policy.max_trials
        or state.budget.now - state.budget.started_at >= state.policy.max_elapsed
    ):
        status = "incomplete"
        reason = (
            "trial_budget" if state.budget.attempts >= state.policy.max_trials else "elapsed_budget"
        )
    return SearchResult(
        status=status,
        passing_users=passing,
        failing_users=failing,
        reason=reason,
        contradictions=contradictions,
        observations=state.observations,
        attempts=state.budget.attempts,
        confirmation=state.policy.confirmation,
    )


def next_probe(
    state: SearchState, policy: SearchPolicy | None = None, *, now: float | None = None
) -> int | None:
    if policy is not None and policy != state.policy:
        raise ValueError("policy differs from checkpoint")
    if now is not None:
        state = state.model_copy(update={"budget": state.budget.advance(now)})
    if state.pending_trial_id is not None or search_result(state).status != "searching":
        return None
    decisions, pending = _decisions(state)
    if pending:
        return pending[0]
    result = search_result(state)
    lo = result.passing_users
    hi = result.failing_users
    if lo is None and hi is None:
        return state.policy.start
    numerator, denominator = state.policy.expansion_factor.as_integer_ratio()
    if lo is None:
        assert hi is not None
        return max(state.policy.min_users, min(hi - 1, hi * denominator // numerator))
    if hi is not None:
        return (lo + hi) // 2
    candidate = max(lo + 1, (lo * numerator + denominator - 1) // denominator)
    return (
        min(candidate, state.policy.max_users) if state.policy.max_users is not None else candidate
    )


def reserve_attempt(state: SearchState, users: int, trial_id: str, *, now: float) -> SearchState:
    if not trial_id or any(o.trial_id == trial_id for o in state.observations):
        raise ValueError("trial ID must be new")
    if next_probe(state, now=now) != users:
        raise ValueError("probe not allowed")
    return state.model_copy(
        update={
            "pending_trial_id": trial_id,
            "pending_users": users,
            "budget": state.budget.advance(now).model_copy(
                update={"attempts": state.budget.attempts + 1}
            ),
        }
    )


def record_probe(
    state: SearchState,
    users: int,
    trial_id: str,
    evaluation: Evaluation,
    *,
    reused: bool = False,
    now: float | None = None,
) -> SearchState:
    clock_budget = state.budget.advance(now)
    if not trial_id or any(o.trial_id == trial_id for o in state.observations):
        raise ValueError("trial ID must be unique")
    if users < state.policy.min_users or (
        state.policy.max_users is not None and users > state.policy.max_users
    ):
        raise ValueError("users outside feasible bounds")
    valid_votes = [o for o in state.observations if o.users == users and o.evaluation.valid]
    if evaluation.valid and valid_votes:
        values = [o.evaluation.verdict for o in valid_votes]
        if (
            state.policy.confirmation == "single_observation"
            or len(values) >= 3
            or values.count("pass") >= 2
            or values.count("fail") >= 2
        ):
            raise ValueError("candidate already decided; no fourth vote")
    if state.pending_trial_id is not None and (
        state.pending_trial_id != trial_id or state.pending_users != users or reused
    ):
        raise ValueError("result differs from reservation")
    cost = 0 if reused or state.pending_trial_id is not None else 1
    if cost and (
        state.budget.attempts >= state.policy.max_trials
        or clock_budget.now - clock_budget.started_at >= state.policy.max_elapsed
    ):
        raise ValueError("job budget exhausted")
    budget = clock_budget.model_copy(update={"attempts": state.budget.attempts + cost})
    return state.model_copy(
        update={
            "observations": state.observations
            + (Observation(users=users, trial_id=trial_id, evaluation=evaluation, reused=reused),),
            "budget": budget,
            "pending_trial_id": None,
            "pending_users": None,
        }
    )


def reuse_evidence(
    state: SearchState, evidence: TrialEvidence, *, execution_id: str, criteria: tuple[Criterion, ...]
) -> SearchState:
    """Reevaluate immutable samples for this objective; never inherit stored labels."""
    from .metrics import evaluate

    if not execution_id or evidence.execution_id != execution_id:
        raise ValueError("incompatible execution identity")
    if evidence.users is None:
        raise ValueError("evidence missing users")
    return record_probe(
        state, evidence.users, evidence.trial_id, evaluate(evidence, criteria), reused=True
    )
