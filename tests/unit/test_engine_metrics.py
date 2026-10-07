import pytest

from tests.unit.test_engine_stages import job
from tools.bench.engine.evidence import decision_identity, execution_identity
from tools.bench.engine.metrics import evaluate
from tools.bench.engine.types import Criterion, ResolvedStage, Sample, TrialEvidence


def evidence(*samples, duration=4.0):
    return TrialEvidence(
        stages=(
            ResolvedStage(
                id="hold",
                from_users=1,
                to_users=1,
                duration=duration,
                evaluate=True,
                start_seconds=0.0,
                end_seconds=duration,
            ),
        ),
        samples=tuple(samples),
    )


def criterion(metric="latency", statistic="p99", scope="workload", **kwargs):
    return Criterion(
        metric=metric,
        scope=scope,
        statistic=statistic,
        operator="<=",
        **({"value": 100.0} | kwargs),
    )


def test_combines_raw_quantiles():
    result = evaluate(
        evidence(
            *(
                Sample(metric="latency", timestamp=float(i) / 10, value=float(v))
                for i, v in enumerate([1, 2, 3, 100])
            )
        ),
        (criterion(),),
    )
    assert result.valid and result.passed
    assert result.results[0].value == pytest.approx(97.09)
    assert result.results[0].sample_count == 4
    assert result.warnings


def test_rate_uses_own_denominator():
    samples = [
        Sample(metric=name, kind="rate", timestamp=1.0, numerator=n, denominator=d)
        for name, n, d in [("success", 90.0, 100.0), ("auth", 2.0, 4.0), ("auth", 1.0, 2.0)]
    ]
    result = evaluate(evidence(*samples), (criterion("auth", "rate"),))
    assert result.results[0].value == 0.5


@pytest.mark.parametrize(
    "samples", [[], [Sample(metric="latency", timestamp=1.0, value=float("nan"))]]
)
def test_missing_and_nonfinite_are_invalid(samples):
    assert not evaluate(evidence(*samples), (criterion(),)).valid


def test_time_weighted_gauge_and_gap():
    samples = [
        Sample(
            metric="memory_used",
            scope="container",
            kind="gauge",
            timestamp=t,
            value=v,
            max_gap_seconds=3.0,
        )
        for t, v in [(0.0, 10.0), (1.0, 30.0)]
    ]
    result = evaluate(evidence(*samples), (criterion("memory_used", "mean", "container"),))
    assert result.valid and result.results[0].value == 25.0
    assert not evaluate(
        evidence(*samples, duration=5.0), (criterion("memory_used", "mean", "container"),)
    ).valid


def test_interval_clipping_and_scope():
    sample = Sample(
        metric="cpu_used",
        scope="backend_host",
        kind="interval",
        timestamp=-1.0,
        end_seconds=2.0,
        value=0.5,
        normalization="all_host_cpus",
    )
    result = evaluate(
        evidence(sample, duration=2.0), (criterion("cpu_used", "mean", "backend_host"),)
    )
    assert result.valid and result.results[0].value == 0.5
    bad = sample.model_copy(update={"normalization": "container_quota"})
    assert not evaluate(
        evidence(bad, duration=2.0), (criterion("cpu_used", "mean", "backend_host"),)
    ).valid
    assert not evaluate(evidence(sample), (criterion("cpu_used", "mean", "container"),)).valid


def test_minimum_aliases():
    c = criterion(min_samples=2, min_coverage=0.9)
    assert c.minimum_samples == 2 and c.minimum_coverage == 0.9
    assert criterion().minimum_coverage == 0.95


def test_identity_separates_execution_and_decisions(tmp_path):
    j = job(tmp_path)
    changed = j.model_copy(update={"criteria": [criterion(value=30.0)]})
    assert execution_identity(j, 100) == execution_identity(changed, 100)
    assert decision_identity(j, "default") != decision_identity(changed, "default")
    timing = j.model_copy(
        update={"stages": [s.model_copy(update={"duration": s.duration + 1}) for s in j.stages]}
    )
    assert execution_identity(j, 100) != execution_identity(timing, 100)
    before = execution_identity(j, 100)
    j.workload.script.write_text("changed")
    assert execution_identity(j, 100) != before


def test_quantile_endpoints_and_real_failure():
    ev = evidence(
        Sample(metric="latency", timestamp=0.0, value=10.0),
        Sample(metric="latency", timestamp=1.0, value=20.0),
    )
    result = evaluate(ev, (criterion(statistic="p0", value=9.0), criterion(statistic="p100")))
    assert result.verdict == "fail"
    assert [r.value for r in result.results] == [10.0, 20.0]


def test_overlapping_intervals_and_mixed_latency_invalid():
    samples = [
        Sample(
            metric="cpu_used",
            scope="container",
            kind="interval",
            timestamp=0.0,
            end_seconds=4.0,
            value=0.1,
            normalization="container_quota",
        ),
        Sample(
            metric="cpu_used",
            scope="container",
            kind="interval",
            timestamp=1.0,
            end_seconds=3.0,
            value=0.2,
            normalization="container_quota",
        ),
    ]
    assert (
        evaluate(evidence(*samples), (criterion("cpu_used", "mean", "container"),)).verdict
        == "invalid"
    )
    samples = [
        Sample(metric="latency", timestamp=1.0, value=1.0),
        Sample(metric="latency", timestamp=2.0, value=2.0, includes_failures=False),
    ]
    assert evaluate(evidence(*samples), (criterion(),)).verdict == "invalid"


def test_common_success_applies_to_objective(tmp_path):
    common = criterion("success", "rate", value=0.99).model_copy(update={"operator": ">="})
    objective = criterion()
    ev = evidence(
        Sample(metric="success", kind="rate", timestamp=1.0, numerator=1.0, denominator=2.0),
        Sample(metric="latency", timestamp=1.0, value=1.0),
    )
    assert evaluate(ev, (common, objective)).verdict == "fail"


def test_identity_requires_script(tmp_path):
    j = job(tmp_path)
    j.workload.script.unlink()
    with pytest.raises(ValueError, match="not a file"):
        execution_identity(j, 100)


def test_invalid_result_retains_observed_count():
    result = evaluate(
        evidence(Sample(metric="latency", timestamp=1.0, value=float("nan"))), (criterion(),)
    )
    assert result.results[0].sample_count == 1


def test_mixed_declared_units_without_criterion_unit_invalid():
    ev = evidence(
        Sample(metric="latency", timestamp=1.0, value=1.0, unit="ms"),
        Sample(metric="latency", timestamp=2.0, value=1.0, unit="s"),
    )
    assert evaluate(ev, (criterion(statistic="mean"),)).verdict == "invalid"


def test_explicit_unit_requires_sample_unit():
    ev = evidence(Sample(metric="latency", timestamp=1.0, value=1.0))
    assert evaluate(ev, (criterion(unit="ms"),)).verdict == "invalid"


@pytest.mark.parametrize("is_file", [False, True])
def test_missing_or_file_build_context_invalid(tmp_path, is_file):
    from tools.bench.engine.types import Build, Target

    j = job(tmp_path)
    context = tmp_path / "context"
    if is_file:
        context.write_text("not a directory")
    j = j.model_copy(update={"target": Target(build=Build(context=context))})
    with pytest.raises(ValueError, match="build context.*directory"):
        execution_identity(j, 100)


def test_mixed_temporal_kinds_invalid():
    ev = evidence(
        Sample(
            metric="memory_used",
            scope="container",
            kind="gauge",
            timestamp=0.0,
            value=1.0,
            max_gap_seconds=2.0,
        ),
        Sample(
            metric="memory_used",
            scope="container",
            kind="interval",
            timestamp=2.0,
            end_seconds=4.0,
            value=1.0,
        ),
    )
    assert evaluate(ev, (criterion("memory_used", "mean", "container"),)).verdict == "invalid"
