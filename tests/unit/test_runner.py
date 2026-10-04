import pytest
from pathlib import Path
from bench.schedule import schedule_trials,validate_campaign
from bench.manifest import redact
from bench.runner import campaign_lock

def config():
    return {'mode':'local','cells':['baseline-3.10.22'],'rates':[25,50],'repeats':5,'warmup_seconds':120,'measure_seconds':300,'vus':200,'fixture':'fixture/manifest.json','database_allowlist':'language_benchmark','seed':42}

def test_schedule_is_block_randomized_and_repeatable():
    c=config();a=schedule_trials(c);b=schedule_trials(c)
    assert a==b and len(a)==10
    assert len({x['id'] for x in a})==10
    assert sum(x['rate']==25 for x in a)==5

def test_invalid_campaign_cannot_run_unbounded():
    c=config();c['rates']=[0]
    with pytest.raises(ValueError): validate_campaign(c)
    c=config();c['repeats']=1
    with pytest.raises(ValueError): validate_campaign(c)

def test_manifest_redacts_credentials():
    out=redact({'dsn':'postgresql://user:private-value-123@host/db','token':'private-value-123','nested':{'jwt_secret':'private-value-123'}})
    assert 'private-value-123' not in str(out.values())

def test_second_campaign_cannot_acquire_lock(tmp_path):
    with campaign_lock(tmp_path/'lock'):
        with pytest.raises(RuntimeError):
            with campaign_lock(tmp_path/'lock'):pass
