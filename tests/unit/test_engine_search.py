import pytest

from tools.bench.engine.search import (
    SearchPolicy,
    SearchState,
    next_probe,
    record_probe,
    search_result,
)
from tools.bench.engine.types import Evaluation


def state(**kwargs):
    return SearchState(policy=SearchPolicy(start=1000, **kwargs))


def vote(s, users, verdict, **kwargs):
    return record_probe(s, users, str(len(s.observations)), Evaluation(verdict=verdict), **kwargs)


def test_expands_and_bisects():
    s = state()
    for users in [1000, 2000, 4000]:
        assert next_probe(s) == users
        s = vote(s, users, "pass" if users < 4000 else "fail")
    assert next_probe(s) == 4000
    s = vote(s, 4000, "fail")
    assert next_probe(s) == 3000


@pytest.mark.parametrize(
    "votes,passing", [("fail fail", False), ("fail pass pass", True), ("fail pass fail", False)]
)
def test_majority_and_no_fourth(votes, passing):
    s = state()
    for v in votes.split():
        s = vote(s, 1000, v)
    assert (search_result(s).passing_users == 1000) == passing
    with pytest.raises(ValueError):
        vote(s, 1000, "pass")


def test_downward_and_minimum_failure():
    s = state(min_users=100, confirmation="single_observation")
    s = vote(s, 1000, "fail")
    assert next_probe(s) == 500
    s = vote(s, 100, "fail")
    assert next_probe(s) is None
    assert search_result(s).status == "below_minimum"
    assert search_result(s).passing_users is None


def test_ceiling_budget_deadline_and_serialization():
    s = state(max_users=1000)
    s = vote(s, 1000, "pass")
    assert search_result(s).status == "lower_bound"
    assert SearchState.model_validate_json(s.model_dump_json()) == s
    s = state(max_trials=40)
    for i in range(40):
        s = vote(s, 1000, "invalid")
    assert next_probe(s) is None
    assert search_result(s).status == "incomplete"
    assert next_probe(state(max_elapsed=5.0), now=5.0) is None


def test_invalid_and_duplicates_do_not_move_bounds():
    s = vote(state(), 1000, "invalid")
    assert next_probe(s) == 1000
    assert search_result(s).passing_users is None
    with pytest.raises(ValueError):
        record_probe(s, 1000, "0", Evaluation(verdict="pass"))


def test_contradictions_are_visible():
    s = state(confirmation="single_observation")
    s = vote(s, 1000, "fail")
    s = vote(s, 2000, "pass")
    assert search_result(s).status == "unstable"
    assert search_result(s).contradictions


def test_reservation_is_durable_and_deadline_allows_result():
    from tools.bench.engine.search import reserve_attempt

    s = reserve_attempt(state(max_elapsed=5.0), 1000, "launch", now=4.0)
    s = SearchState.model_validate_json(s.model_dump_json())
    assert s.budget.attempts == 1 and next_probe(s) is None
    s = record_probe(s, 1000, "launch", Evaluation(verdict="pass"), now=6.0)
    assert s.budget.attempts == 1 and search_result(s).status == "incomplete"


def test_reuse_reclassifies_raw_data_without_execution_cost():
    from tools.bench.engine.search import reuse_evidence
    from tools.bench.engine.types import Criterion, ResolvedStage, Sample, TrialEvidence

    raw = TrialEvidence(
        trial_id="shared",
        execution_id="same",
        users=1000,
        stages=(
            ResolvedStage(
                id="hold",
                from_users=1000,
                to_users=1000,
                duration=1.0,
                evaluate=True,
                start_seconds=0.0,
                end_seconds=1.0,
            ),
        ),
        samples=(Sample(metric="latency", timestamp=0.5, value=10.0),),
    )
    passing = reuse_evidence(
        state(),
        raw,
        execution_id="same",
        criteria=(Criterion(metric="latency", statistic="max", operator="<=", value=20.0),),
    )
    failing = reuse_evidence(
        state(),
        raw,
        execution_id="same",
        criteria=(Criterion(metric="latency", statistic="max", operator="<=", value=5.0),),
    )
    assert passing.observations[0].evaluation.verdict == "pass"
    assert failing.observations[0].evaluation.verdict == "fail"
    assert passing.budget.attempts == failing.budget.attempts == 0
    with pytest.raises(ValueError):
        reuse_evidence(state(), raw, execution_id="different", criteria=())


def test_changed_failing_endpoint_reopens_expansion():
    s = vote(state(), 1000, "fail")
    s = vote(s, 1000, "pass")
    s = vote(s, 1000, "pass")
    assert next_probe(s) == 2000


def test_budget_is_shared_between_objectives():
    first = vote(state(max_trials=1), 1000, "invalid")
    second = SearchState(policy=first.policy, budget=first.budget)
    assert next_probe(second) is None


def test_policy_rejects_start_outside_bounds():
    with pytest.raises(ValueError):
        SearchPolicy(start=10, min_users=20)
    with pytest.raises(ValueError):
        SearchPolicy(start=20, max_users=10)


def test_feasible_limits_and_fixed_policy():
    from tools.bench.engine.types import Limits, SearchUsers, TestPolicy

    policy = SearchPolicy.from_test(
        TestPolicy(
            type="search",
            users=SearchUsers(start=1000, max="auto"),
            limits=Limits(max_trials=4, max_elapsed=20.0),
        ),
        feasible_ceilings={"accounts": 500, "generator": 800},
    )
    assert policy.start == policy.max_users == 500 and policy.limiting_reason == "accounts"
    with pytest.raises(ValueError):
        SearchPolicy.from_test(TestPolicy(type="fixed", users=1000))


def test_large_finite_expansion_clamps_without_float_overflow():
    s = vote(state(max_users=2000, expansion_factor=1e308), 1000, "pass")
    assert next_probe(s) == 2000
    auto = vote(state(expansion_factor=1e308), 1000, "pass")
    assert isinstance(next_probe(auto), int) and next_probe(auto) > 1000


def test_epoch_deadline_survives_reboot_and_rejects_bad_clock():
    from tools.bench.engine.search import JobBudget, reserve_attempt

    s = SearchState(
        policy=SearchPolicy(start=1000, max_elapsed=10.0),
        budget=JobBudget(started_at=1700000000.0, now=1700000001.0),
    )
    restored = SearchState.model_validate_json(s.model_dump_json())
    assert next_probe(restored, now=1700000010.0) is None
    for bad in [float("nan"), float("inf"), 1699999999.0]:
        with pytest.raises(ValueError):
            next_probe(restored, now=bad)
        with pytest.raises(ValueError):
            reserve_attempt(restored, 1000, "bad", now=bad)
        with pytest.raises(ValueError):
            record_probe(restored, 1000, "bad", Evaluation(verdict="pass"), now=bad)
    with pytest.raises(ValueError):
        JobBudget(started_at=5.0, now=4.0)
