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


def measured(s, users, values, verdict):
    from tools.bench.engine.types import Criterion, CriterionResult

    results = tuple(
        CriterionResult(
            criterion=Criterion(
                metric=metric, statistic=statistic, operator=operator, value=threshold
            ),
            valid=True,
            passed=(value <= threshold if operator == "<=" else value >= threshold),
            value=value,
        )
        for metric, statistic, operator, threshold, value in values
    )
    return record_probe(
        s, users, str(len(s.observations)), Evaluation(verdict=verdict, results=results)
    )


def hybrid_bracket(lower_values, upper_values):
    s = state(search_method="hybrid", confirmation="single_observation")
    s = measured(s, 2000, lower_values, "pass")
    return measured(s, 3000, upper_values, "fail")


def test_hybrid_secant_interpolates_latency():
    s = hybrid_bracket([("latency", "p99", "<=", 5.0, 3.0)], [("latency", "p99", "<=", 5.0, 8.0)])
    assert next_probe(s) == 2400
    binary = s.model_copy(
        update={"policy": s.policy.model_copy(update={"search_method": "binary"})}
    )
    assert next_probe(binary) == 2500


def test_hybrid_lower_bound_and_all_criteria_limiting():
    s = hybrid_bracket(
        [("success", "rate", ">=", 0.99, 1.0)], [("success", "rate", ">=", 0.99, 0.96)]
    )
    assert next_probe(s) == 2250
    s = hybrid_bracket(
        [("latency", "p99", "<=", 5.0, 3.0), ("authenticated", "rate", ">=", 0.99, 1.0)],
        [("latency", "p99", "<=", 5.0, 8.0), ("authenticated", "rate", ">=", 0.99, 0.95)],
    )
    # All criteria are aggregated by their minimum margin, not latency alone.
    assert next_probe(s) == 2200


@pytest.mark.parametrize("upper", [None, 5.0])
def test_hybrid_missing_or_inconsistent_falls_back(upper):
    s = hybrid_bracket([("latency", "p95", "<=", 5.0, 3.0)], [("latency", "p95", "<=", 5.0, 8.0)])
    result = s.observations[-1].evaluation.results[0].model_copy(update={"value": upper})
    observation = s.observations[-1].model_copy(
        update={"evaluation": Evaluation(verdict="fail", results=(result,))}
    )
    s = s.model_copy(update={"observations": (s.observations[0], observation)})
    assert next_probe(s) == 2500


def test_hybrid_selector_mismatch_invalid_and_opposing_votes():
    s = state(search_method="hybrid")
    s = measured(s, 2000, [("latency", "p99", "<=", 5.0, 3.0)], "pass")
    s = measured(s, 3000, [("latency", "p99", "<=", 5.0, 1.0)], "pass")
    s = measured(s, 3000, [("latency", "p99", "<=", 5.0, 8.0)], "fail")
    s = measured(s, 3000, [("latency", "p99", "<=", 5.0, 8.0)], "fail")
    assert next_probe(s) == 2400
    s = vote(s, 2500, "invalid")
    assert next_probe(s) == 2400
    with pytest.raises(ValueError):
        record_probe(s, 2500, "0", Evaluation(verdict="invalid"))
    upper = s.observations[2].evaluation.results[0]
    changed = upper.model_copy(
        update={"criterion": upper.criterion.model_copy(update={"statistic": "p95"})}
    )
    observations = list(s.observations)
    for index in (2, 3):
        observations[index] = observations[index].model_copy(
            update={"evaluation": Evaluation(verdict="fail", results=(changed,))}
        )
    assert next_probe(s.model_copy(update={"observations": tuple(observations)})) == 2500


def test_hybrid_zero_threshold_nonfinite_and_resolution_progress():
    s = hybrid_bracket([("delta", "mean", "<=", 0.0, -2.0)], [("delta", "mean", "<=", 0.0, 3.0)])
    assert next_probe(s) == 2400
    result = s.observations[1].evaluation.results[0].model_copy(update={"value": float("inf")})
    upper = s.observations[1].model_copy(
        update={"evaluation": Evaluation(verdict="fail", results=(result,))}
    )
    assert next_probe(s.model_copy(update={"observations": (s.observations[0], upper)})) == 2500
    s = state(search_method="hybrid", confirmation="single_observation", resolution=1)
    for users, value, verdict in [(2000, 3.0, "pass"), (2002, 8.0, "fail")]:
        s = measured(s, users, [("latency", "p99", "<=", 5.0, value)], verdict)
    assert next_probe(s) == 2001
    s = measured(s, 2001, [("latency", "p99", "<=", 5.0, 4.0)], "pass")
    assert search_result(s).status == "complete"
    assert next_probe(s) is None


def test_legacy_search_policy_without_method_is_binary():
    policy = SearchPolicy.model_validate_json('{"start":1000}')
    assert policy.search_method == "binary"
    assert (
        SearchState.model_validate_json(
            SearchState(policy=policy).model_dump_json()
        ).policy.search_method
        == "binary"
    )


def test_hybrid_timeout_like_value_is_guarded_without_inferring_censoring():
    s = hybrid_bracket([("latency", "p95", "<=", 5.0, 3.0)], [("latency", "p95", "<=", 5.0, 5.001)])
    # No timeout metadata exists: do not infer censorship from an arbitrary cutoff.
    assert next_probe(s) == 2800


def test_hybrid_near_lower_root_is_guarded():
    s = hybrid_bracket([("latency", "p99", "<=", 5.0, 4.999)], [("latency", "p99", "<=", 5.0, 8.0)])
    assert next_probe(s) == 2200


@pytest.mark.parametrize("update", [{"unit": "s"}, {"scope": "container"}, {"stages": ["other"]}])
def test_hybrid_mismatched_scope_units_or_selector_falls_back(update):
    s = hybrid_bracket([("latency", "p99", "<=", 5.0, 3.0)], [("latency", "p99", "<=", 5.0, 8.0)])
    result = s.observations[1].evaluation.results[0]
    result = result.model_copy(update={"criterion": result.criterion.model_copy(update=update)})
    upper = s.observations[1].model_copy(
        update={"evaluation": Evaluation(verdict="fail", results=(result,))}
    )
    assert next_probe(s.model_copy(update={"observations": (s.observations[0], upper)})) == 2500


def test_hybrid_invalid_reduction_and_equality_use_midpoint():
    s = hybrid_bracket([("latency", "p99", "<=", 5.0, 3.0)], [("latency", "p99", "<=", 5.0, 8.0)])
    result = s.observations[1].evaluation.results[0].model_copy(update={"valid": False})
    upper = s.observations[1].model_copy(
        update={"evaluation": Evaluation(verdict="fail", results=(result,))}
    )
    assert next_probe(s.model_copy(update={"observations": (s.observations[0], upper)})) == 2500
    same = hybrid_bracket([("count", "mean", "==", 5.0, 5.0)], [("count", "mean", "==", 5.0, 8.0)])
    assert next_probe(same) == 2500


def test_hybrid_median_supporting_noise_and_no_repeated_decided_candidate():
    s = state(search_method="hybrid")
    s = measured(s, 2000, [("latency", "p99", "<=", 5.0, 3.0)], "pass")
    s = measured(s, 3000, [("latency", "p99", "<=", 5.0, 7.0)], "fail")
    s = measured(s, 3000, [("latency", "p99", "<=", 5.0, 9.0)], "fail")
    assert next_probe(s) == 2400
    s = measured(s, 2400, [("latency", "p99", "<=", 5.0, 4.0)], "pass")
    candidate = next_probe(s)
    assert 2400 < candidate < 3000
    assert candidate not in {2000, 2400, 3000}


def test_hybrid_no_bracket_keeps_geometric_expansion_and_halving():
    s = state(search_method="hybrid", confirmation="single_observation")
    assert next_probe(vote(s, 1000, "pass")) == 2000
    assert next_probe(vote(s, 1000, "fail")) == 500


def test_hybrid_serialized_policy_and_method_from_new_config():
    from tools.bench.engine.types import Limits, SearchUsers, TestPolicy

    test = TestPolicy(
        type="search",
        users=SearchUsers(start=1000, max="auto"),
        limits=Limits(max_trials=10, max_elapsed=100.0),
    )
    assert SearchPolicy.from_test(test).search_method == "upper_probe"
    s = hybrid_bracket([("latency", "p99", "<=", 5.0, 3.0)], [("latency", "p99", "<=", 5.0, 8.0)])
    assert next_probe(SearchState.model_validate_json(s.model_dump_json())) == 2400


def test_upper_probe_example_and_confirmation():
    s = SearchState(policy=SearchPolicy(start=2000, resolution=50, search_method="upper_probe"))
    assert next_probe(s) == 2000
    s = vote(s, 2000, "fail")
    assert next_probe(s) == 2000
    s = vote(s, 2000, "fail")
    assert next_probe(s) == 1000
    s = vote(s, 1000, "pass")
    assert next_probe(s) == 1900
    s = vote(s, 1900, "fail")
    assert next_probe(s) == 1900
    s = vote(s, 1900, "fail")
    assert next_probe(s) == 1450
    s = vote(s, 1450, "pass")
    assert next_probe(s) == 1850
    s = vote(s, 1850, "pass")
    assert next_probe(s) is None
    assert search_result(s).passing_users == 1850
    assert search_result(s).failing_users == 1900


def test_upper_probe_invalid_and_disagreement_do_not_skip_confirmation():
    s = state(search_method="upper_probe", resolution=50)
    s = vote(s, 2000, "fail")
    s = vote(s, 2000, "fail")
    s = vote(s, 1000, "pass")
    s = vote(s, 1900, "invalid")
    assert next_probe(s) == 1900
    s = vote(s, 1900, "fail")
    assert next_probe(s) == 1900
    s = vote(s, 1900, "pass")
    assert next_probe(s) == 1900
    s = vote(s, 1900, "fail")
    assert next_probe(s) == 1450
    assert next_probe(SearchState.model_validate_json(s.model_dump_json())) == 1450


@pytest.mark.parametrize("resolution", [1, 2, 50])
def test_upper_probe_integer_progress_and_resolution(resolution):
    huge = 10**100
    s = SearchState(
        policy=SearchPolicy(
            start=huge,
            search_method="upper_probe",
            resolution=resolution,
            confirmation="single_observation",
        )
    )
    s = vote(s, huge + resolution + 1, "fail")
    s = vote(s, huge, "pass")
    candidate = next_probe(s)
    assert huge < candidate < huge + resolution + 1
    assert candidate == huge + 1
    s = vote(s, candidate, "pass")
    assert next_probe(s) is None


def test_upper_probe_midpoint_failure_and_unbracketed_expansion():
    s = state(search_method="upper_probe", confirmation="single_observation")
    assert next_probe(vote(s, 1000, "pass")) == 2000
    assert next_probe(vote(s, 1000, "fail")) == 500
    s = vote(s, 2000, "fail")
    s = vote(s, 1000, "pass")
    s = vote(s, 1900, "fail")
    s = vote(s, 1450, "fail")
    assert next_probe(s) == 1225


def test_upper_probe_budget_and_reservation_remain_durable():
    from tools.bench.engine.search import reserve_attempt

    s = state(search_method="upper_probe", max_trials=4)
    s = vote(s, 2000, "fail")
    s = vote(s, 2000, "fail")
    s = vote(s, 1000, "pass")
    s = reserve_attempt(s, 1900, "upper-reserved", now=0)
    assert next_probe(SearchState.model_validate_json(s.model_dump_json())) is None
    s = record_probe(s, 1900, "upper-reserved", Evaluation(verdict="pass"), now=0)
    assert search_result(s).status == "incomplete"
    assert next_probe(s) is None
