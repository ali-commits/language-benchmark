import pytest
from tools.bench.engine.config import ConfigError, load_config, resolve_jobs
from tools.bench.engine.types import Criterion
from tests.unit.test_engine_config import document
from tools.bench.engine.stages import resolve_stages, fixed_populations, criterion_windows


def job(tmp_path, stages=None, test=None, **extra):
    row = dict(id='measure', targets=['api'], test=test or {'type':'fixed','users':[100,500,1000]}, **extra)
    if stages is not None:
        row['stages'] = stages
    return resolve_jobs(load_config(document(tmp_path,jobs=[row])))[0]


def stage(id, start, end, duration=1, evaluate=False):
    return {'id':id,'from':start,'to':end,'duration':duration,'evaluate':evaluate}


def test_populations(tmp_path):
    assert fixed_populations(job(tmp_path)) == (100,500,1000)
    policy = {'type':'stress','strategy':'sweep','users':{'start':100,'end':350,'step':100}}
    assert fixed_populations(job(tmp_path,test=policy)) == (100,200,300,350)


def test_curve_and_windows(tmp_path):
    half={'fraction_of_target':.5}
    stages=[stage('up',0,'target',180),stage('peak','target','target',120,True),stage('reduce','target',half,60),stage('recover',half,half,120),stage('down',half,0,60),stage('idle',0,0,30)]
    j=job(tmp_path,stages)
    resolved=resolve_stages(j,101)
    assert [(s.from_users,s.to_users) for s in resolved] == [(0,101),(101,101),(101,50),(50,50),(50,0),(0,0)]
    assert resolved[-1].end_seconds == 570
    c=Criterion(metric='duration',statistic='p99',operator='<=',value=10.)
    assert criterion_windows(resolved,c) == ((180.,300.),)


def test_absolute_idle_and_tolerance(tmp_path):
    j=job(tmp_path,[stage('hold',5,5),stage('down',5,0),stage('idle',0,0,2,True)],test={'type':'staged'},hold_tolerance={'fraction':.1,'grace':'2s'})
    assert fixed_populations(j)==(None,)
    assert resolve_stages(j,None)[-1].to_users==0
    assert j.hold_tolerance.fraction==.1 and j.hold_tolerance.grace==2
    assert job(tmp_path).hold_tolerance.grace==0


@pytest.mark.parametrize('stages,test', [
    ([stage('a',0,10),stage('b',11,11)],{'type':'staged'}),
    ([stage('a','target','target')],{'type':'staged'}),
    ([stage('a',0,'target')],{'type':'search','users':{'min':1,'start':10,'max':100},'limits':{'max_trials':2,'max_elapsed':10}}),
    ([stage('a',2,'target'),stage('hold','target','target',evaluate=True)],{'type':'search','users':{'min':1,'start':10,'max':100},'limits':{'max_trials':2,'max_elapsed':10}}),
])
def test_invalid_templates_caught_by_loading(tmp_path,stages,test):
    with pytest.raises(ConfigError): job(tmp_path,stages,test)


def test_idle_cannot_qualify_response(tmp_path):
    with pytest.raises(ConfigError):
        job(tmp_path,[stage('idle',0,0,evaluate=True)],test={'type':'staged'},criteria=[{'metric':'duration','statistic':'p99','operator':'<=','value':10}])


def test_candidate_ceiling(tmp_path):
    j=job(tmp_path,test={'type':'search','users':{'min':1,'start':10,'max':100},'limits':{'max_trials':2,'max_elapsed':10}})
    with pytest.raises(ValueError): resolve_stages(j,0)


def test_cli_rejects_discontinuity(tmp_path,capsys):
    from tools.bench.commands import main
    path=document(tmp_path,jobs=[{'id':'bad','targets':['api'],'test':{'type':'staged'},'stages':[stage('a',0,1),stage('b',2,2)]}])
    assert main(['validate',str(path)])==2
    assert 'adjacent' in capsys.readouterr().err


def test_numeric_tolerance_is_normalized(tmp_path):
    j=job(tmp_path,hold_tolerance=.03)
    assert j.hold_tolerance.fraction == .03
    assert j.hold_tolerance.grace == 0


def test_explicit_resource_idle_window(tmp_path):
    j=job(tmp_path,[stage('idle',0,0,evaluate=True)],test={'type':'staged'})
    c=Criterion(metric='memory_used',scope='container',statistic='max',operator='<=',value=100.)
    assert criterion_windows(resolve_stages(j,None),c)==((0.,1.),)


@pytest.mark.parametrize('duration', [0,-1,float('inf')])
def test_invalid_duration(tmp_path,duration):
    with pytest.raises(ConfigError): job(tmp_path,[stage('hold',1,1,duration)])


@pytest.mark.parametrize('fraction',[float('nan'),float('inf'),1.01,-.01])
def test_invalid_fraction(tmp_path,fraction):
    with pytest.raises(ConfigError): job(tmp_path,[stage('hold',{'fraction_of_target':fraction},'target')])


def test_duplicate_ids(tmp_path):
    with pytest.raises(ConfigError): job(tmp_path,[stage('hold',1,1),stage('hold',1,1)])


def test_cli_prints_concrete_stages(tmp_path,capsys):
    from tools.bench.commands import main
    assert main(['validate',str(document(tmp_path))]) == 0
    output=capsys.readouterr().out
    assert 'hold: 10 -> 10' in output and 'hold: 20 -> 20' in output


def test_search_equivalent_target_and_zero_expressions(tmp_path):
    one={'fraction_of_target':1.}
    zero={'fraction_of_target':0.}
    j=job(tmp_path,[stage('up',zero,'target'),stage('hold',one,one,evaluate=True),stage('down','target',0),stage('idle',zero,0)],test={'type':'search','users':{'min':1,'start':10,'max':100},'limits':{'max_trials':2,'max_elapsed':10}})
    assert resolve_stages(j,10)[1].to_users == 10


def search_policy(minimum, maximum, hard=None):
    limits={'max_trials':2,'max_elapsed':10}
    if hard is not None: limits['hard_users']=hard
    return {'type':'search','users':{'min':minimum,'start':minimum,'max':maximum},'limits':limits}


def test_singleton_mixed_expression(tmp_path):
    j=job(tmp_path,[stage('up',0,10),stage('hold','target','target',evaluate=True)],test=search_policy(10,10))
    assert resolve_stages(j,10)[0].to_users==10


def test_bounded_distinct_fractions(tmp_path):
    a={'fraction_of_target':.49}; b={'fraction_of_target':.5}
    j=job(tmp_path,[stage('up',0,a),stage('more',b,'target'),stage('hold','target','target',evaluate=True)],test=search_policy(7,7))
    assert resolve_stages(j,7)[0].to_users==3


def test_fraction_disagreement_inside_matching_extremes(tmp_path):
    a={'fraction_of_target':.4}; b={'fraction_of_target':.45}
    with pytest.raises(ConfigError):
        job(tmp_path,[stage('up',0,a),stage('more',b,'target'),stage('hold','target','target',evaluate=True)],test=search_policy(1,10))


@pytest.mark.parametrize('maximum,hard,candidate',[(10,None,11),(100,10,11),('auto',10,11)])
def test_search_upper_ceilings(tmp_path,maximum,hard,candidate):
    j=job(tmp_path,test=search_policy(1,maximum,hard))
    with pytest.raises(ValueError,match='ceiling'): resolve_stages(j,candidate)


def test_distinct_fractions_equal_over_huge_interval(tmp_path):
    a={'fraction_of_target':.000000000000000001}
    b={'fraction_of_target':.000000000000000002}
    j=job(tmp_path,[stage('quiet',0,a),stage('up',b,'target'),stage('hold','target','target',evaluate=True)],test=search_policy(1,100000000000000000))
    assert resolve_stages(j,100000000000000000)[0].to_users == 0


def test_fraction_binding_is_decimal_exact(tmp_path):
    fraction={'fraction_of_target':.58}
    j=job(tmp_path,[stage('hold',fraction,fraction)])
    assert resolve_stages(j,100)[0].from_users == 58
