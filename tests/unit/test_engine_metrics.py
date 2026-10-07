import pytest
from tools.bench.engine.types import Criterion, Sample, TrialEvidence, ResolvedStage
from tools.bench.engine.metrics import evaluate
from tools.bench.engine.evidence import execution_identity, decision_identity
from tests.unit.test_engine_stages import job, stage


def evidence(*samples, duration=4.):
    return TrialEvidence(stages=(ResolvedStage(id='hold',from_users=1,to_users=1,duration=duration,evaluate=True,start_seconds=0.,end_seconds=duration),),samples=tuple(samples))


def criterion(metric='latency',statistic='p99',scope='workload',**kwargs):
    return Criterion(metric=metric,scope=scope,statistic=statistic,operator='<=',**({'value':100.}|kwargs))


def test_combines_raw_quantiles():
    result=evaluate(evidence(*(Sample(metric='latency',timestamp=float(i)/10,value=float(v)) for i,v in enumerate([1,2,3,100]))),(criterion(),))
    assert result.valid and result.passed
    assert result.results[0].value==pytest.approx(97.09)
    assert result.results[0].sample_count==4
    assert result.warnings


def test_rate_uses_own_denominator():
    samples=[Sample(metric=name,kind='rate',timestamp=1.,numerator=n,denominator=d) for name,n,d in [('success',90.,100.),('auth',2.,4.),('auth',1.,2.)]]
    result=evaluate(evidence(*samples),(criterion('auth','rate'),))
    assert result.results[0].value==.5


@pytest.mark.parametrize('samples',[[],[Sample(metric='latency',timestamp=1.,value=float('nan'))]])
def test_missing_and_nonfinite_are_invalid(samples):
    assert not evaluate(evidence(*samples),(criterion(),)).valid


def test_time_weighted_gauge_and_gap():
    samples=[Sample(metric='memory_used',scope='container',kind='gauge',timestamp=t,value=v,max_gap_seconds=3.) for t,v in [(0.,10.),(1.,30.)]]
    result=evaluate(evidence(*samples),(criterion('memory_used','mean','container'),))
    assert result.valid and result.results[0].value==25.
    assert not evaluate(evidence(*samples,duration=5.),(criterion('memory_used','mean','container'),)).valid


def test_interval_clipping_and_scope():
    sample=Sample(metric='cpu_used',scope='backend_host',kind='interval',timestamp=-1.,end_seconds=2.,value=.5,normalization='all_host_cpus')
    result=evaluate(evidence(sample,duration=2.),(criterion('cpu_used','mean','backend_host'),))
    assert result.valid and result.results[0].value==.5
    bad=sample.model_copy(update={'normalization':'container_quota'})
    assert not evaluate(evidence(bad,duration=2.),(criterion('cpu_used','mean','backend_host'),)).valid
    assert not evaluate(evidence(sample),(criterion('cpu_used','mean','container'),)).valid


def test_minimum_aliases():
    c=criterion(min_samples=2,min_coverage=.9)
    assert c.minimum_samples==2 and c.minimum_coverage==.9
    assert criterion().minimum_coverage==.95


def test_identity_separates_execution_and_decisions(tmp_path):
    j=job(tmp_path)
    changed=j.model_copy(update={'criteria':[criterion(value=30.)]})
    assert execution_identity(j,100)==execution_identity(changed,100)
    assert decision_identity(j,'default')!=decision_identity(changed,'default')
    timing=j.model_copy(update={'stages':[s.model_copy(update={'duration':s.duration+1}) for s in j.stages]})
    assert execution_identity(j,100)!=execution_identity(timing,100)
    before=execution_identity(j,100)
    j.workload.script.write_text('changed')
    assert execution_identity(j,100)!=before


def test_quantile_endpoints_and_real_failure():
    ev=evidence(Sample(metric='latency',timestamp=0.,value=10.),Sample(metric='latency',timestamp=1.,value=20.))
    result=evaluate(ev,(criterion(statistic='p0',value=9.),criterion(statistic='p100')))
    assert result.verdict=='fail'
    assert [r.value for r in result.results]==[10.,20.]


def test_overlapping_intervals_and_mixed_latency_invalid():
    samples=[Sample(metric='cpu_used',scope='container',kind='interval',timestamp=0.,end_seconds=4.,value=.1,normalization='container_quota'),Sample(metric='cpu_used',scope='container',kind='interval',timestamp=1.,end_seconds=3.,value=.2,normalization='container_quota')]
    assert evaluate(evidence(*samples),(criterion('cpu_used','mean','container'),)).verdict=='invalid'
    samples=[Sample(metric='latency',timestamp=1.,value=1.),Sample(metric='latency',timestamp=2.,value=2.,includes_failures=False)]
    assert evaluate(evidence(*samples),(criterion(),)).verdict=='invalid'


def test_common_success_applies_to_objective(tmp_path):
    j=job(tmp_path)
    common=criterion('success','rate',value=.99).model_copy(update={'operator':'>='})
    objective=criterion()
    ev=evidence(Sample(metric='success',kind='rate',timestamp=1.,numerator=1.,denominator=2.),Sample(metric='latency',timestamp=1.,value=1.))
    assert evaluate(ev,(common,objective)).verdict=='fail'


def test_identity_requires_script(tmp_path):
    j=job(tmp_path)
    j.workload.script.unlink()
    with pytest.raises(ValueError,match='not a file'):
        execution_identity(j,100)
