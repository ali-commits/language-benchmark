import os

import pytest

from tools.bench import commands
from tools.bench.engine.config import load_config
from tools.bench.queue.store import QueueStore


def template(tmp_path):
    path = tmp_path / 'path with spaces' / 'comparison.yaml'
    assert commands.main(['init', '--output', str(path)]) == 0
    return path


def image_config(path):
    import yaml
    data = yaml.safe_load(path.read_text())
    data['targets']['http'].pop('build')
    data['targets']['http']['image'] = 'test:local'
    path.write_text(yaml.safe_dump(data))


def test_init_validate_and_yaml12(tmp_path, capsys):
    path = template(tmp_path)
    assert load_config(path).jobs[0].test.users == [100, 500, 1000]
    path.write_text(path.read_text().replace('database: "off"', 'database: off'))
    assert commands.main(['validate', str(path), '--show-effective']) == 0
    assert 'http-fixed' in capsys.readouterr().out
    assert commands.main(['init', '--output', str(path)]) == 2


def test_queue_mutations_and_changed_cwd(tmp_path, monkeypatch, capsys):
    path = template(tmp_path)
    # Image registration avoids Docker/build input export in this unit test.
    image_config(path)
    state = tmp_path / 'state with spaces'
    monkeypatch.setattr(commands, 'foreground', lambda store, queue: 0)
    assert commands.main(['queue', 'start', str(path), '--state-dir', str(state)]) == 0
    monkeypatch.chdir(tmp_path)
    for action in ('list', 'status', 'logs'):
        argv = ['queue', action] + ([] if action == 'list' else ['http-demo'])
        assert commands.main([*argv, '--state-dir', str(state)]) == 0
    job = tmp_path / 'job.yaml'
    job.write_text('id: extra\ntargets: [http]\ntest: {type: fixed, users: [2]}\n')
    assert commands.main(['queue', 'append', 'http-demo', '--job', str(job), '--state-dir', str(state)]) == 0
    job.write_text(job.read_text().replace('[2]', '[3]'))
    update = ['queue', 'update', 'http-demo', 'extra', '--file', str(job), '--state-dir', str(state)]
    assert commands.main([*update, '--expected-revision', '99']) == 2
    assert commands.main(update) == 0
    assert commands.main(['queue', 'remove', 'http-demo', 'extra', '--state-dir', str(state)]) == 0
    assert commands.main(['queue', 'resume', 'http-demo', '--state-dir', str(state)]) == 0
    assert commands.main(['queue', 'status', 'http-demo', '--json', '--state-dir', str(state)]) == 0
    captured = capsys.readouterr().out
    assert 'definition' not in captured
    assert QueueStore(state / 'queue.db').jobs('http-demo')[-1].state == 'cancelled'


def test_env_file_literal_private_and_restored(tmp_path, monkeypatch):
    path = tmp_path / 'private.env'
    path.write_text('BENCH_TOKEN=$HOME `do-not-execute` #literal\n')
    path.chmod(0o600)
    monkeypatch.delenv('BENCH_TOKEN', raising=False)
    with commands.environment_file(path):
        assert os.environ['BENCH_TOKEN'] == '$HOME `do-not-execute` #literal'
    assert 'BENCH_TOKEN' not in os.environ
    path.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        with commands.environment_file(path):
            pass


def test_foreground_signal_is_durable(tmp_path, monkeypatch):
    import signal

    from tools.bench.queue import controller
    path = template(tmp_path)
    image_config(path)
    store = QueueStore(tmp_path / 'state' / 'queue.db')
    store.register(load_config(path))
    original = signal.getsignal(signal.SIGINT)
    def run(store, queue):
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert store.stop_request(queue) is False
        store.checkpoint(queue, {}, state='interrupted')
        return store.snapshot(queue)
    monkeypatch.setattr(controller, 'run_queue', run)
    assert commands.foreground(store, 'http-demo') == 1
    assert signal.getsignal(signal.SIGINT) == original


def test_doctor_dispatch_and_failure(tmp_path, monkeypatch):
    path = template(tmp_path)
    monkeypatch.setattr(commands, 'doctor', lambda config: 0)
    assert commands.main(['doctor', str(path)]) == 0
    assert commands.main(['validate', str(tmp_path / 'missing.yaml')]) == 2


def test_report_dispatch(tmp_path, monkeypatch):
    import sys
    import types
    calls = []
    monkeypatch.setitem(sys.modules, 'tools.bench.engine.report', types.SimpleNamespace(
        write_queue_report=lambda store, queue, output: calls.append((store.path, queue, output))))
    assert commands.main(['report', 'queue', '--state-dir', str(tmp_path), '--output', str(tmp_path / 'report.html')]) == 0
    assert calls[0][1] == 'queue'


def test_stop_pending_after_current_and_resume(tmp_path, monkeypatch):
    path = template(tmp_path)
    image_config(path)
    state = tmp_path / 'state'
    monkeypatch.setattr(commands, 'foreground', lambda store, queue: 0)
    assert commands.main(['queue', 'start', str(path), '--state-dir', str(state)]) == 0
    assert commands.main(['queue', 'stop', 'http-demo', '--after-current', '--timeout', '0', '--state-dir', str(state)]) == 0
    store = QueueStore(state / 'queue.db')
    assert store.stop_request('http-demo') is True
    assert commands.main(['queue', 'resume', 'http-demo', '--state-dir', str(state)]) == 0
    assert store.stop_request('http-demo') is None


def test_stop_active_timeout_is_nonzero(tmp_path, monkeypatch, capsys):
    path = template(tmp_path)
    image_config(path)
    state = tmp_path / 'state'
    store = QueueStore(state / 'queue.db')
    store.register(load_config(path))
    store.claim_next('http-demo', 'test-owner')
    assert commands.main(['queue', 'stop', 'http-demo', '--timeout', '0', '--state-dir', str(state)]) == 1
    assert 'cleanup has not yet been verified' in capsys.readouterr().err


def test_detached_start_explicit_environment_path(tmp_path, monkeypatch):
    from tools.bench.queue import services
    path = template(tmp_path)
    image_config(path)
    secret = tmp_path / 'private.env'
    secret.write_text('EXAMPLE_TOKEN=private-value\n')
    secret.chmod(0o600)
    calls = []
    def install(queue, state, *, environment_file):
        calls.append((queue, state, environment_file))
        return services.ServiceReceipt(queue, 'unit', tmp_path / 'unit', state, True)
    monkeypatch.setattr(services, 'install_service', install)
    monkeypatch.setattr(services, 'start_service', lambda *args: calls.append('start'))
    assert commands.main(['queue', 'start', str(path), '--state-dir', str(tmp_path / 'state'), '--detach', '--env-file', str(secret)]) == 0
    assert calls[0][2] == secret
    assert calls[1] == 'start'


def test_init_yaml_sensitive_path(tmp_path):
    path = tmp_path / 'comparison: demo #1.yaml'
    assert commands.main(['init', '--output', str(path)]) == 0
    assert load_config(path).targets['http'].build.context.name == 'comparison: demo #1-assets'


def test_social_read_only_preflight_rejects_inputs(tmp_path, monkeypatch):
    import hashlib
    import json

    from tools.bench.engine.social import preflight_social
    from tools.bench.engine.types import Fixture, SecretRef, Workload
    manifest = tmp_path / 'fixture.json'
    dump = tmp_path / 'dump.bin'
    dump.write_bytes(b'fixture archive stub')
    manifest.write_text(json.dumps(dict(schema_revision='1', checksum=hashlib.sha256(dump.read_bytes()).hexdigest(), dump='dump.bin', identity='test-id', counts={'users': 2}, fingerprints={'users': 'hash'})))
    actors = tmp_path / 'actors.json'
    script = tmp_path / 'social.js'
    script.write_text('export default function() {}')
    actors.write_text('{"actors": []}')
    monkeypatch.setenv('BENCH_DATABASE_URL', 'postgresql://test:test@localhost/language_benchmark_test')
    workload = Workload(adapter='social', script=script, dependencies=[actors], metrics={}, fixture=Fixture(adapter='social', dsn=SecretRef(env='BENCH_DATABASE_URL'), manifest=manifest, allowed_database='language_benchmark_test', runtime_cell='go-net-http'))
    with pytest.raises(ValueError, match='actors'):
        preflight_social(workload, 2)
    actors.write_text(json.dumps({'actors': [{'username':'one','password':'x'}, {'username':'ONE','password':'y'}]}))
    with pytest.raises(ValueError, match='unique'):
        preflight_social(workload, 2)
    actors.write_text(json.dumps({'actors': [{'username':'one','password':'x'}, {'username':'two','password':'y'}]}))
    assert preflight_social(workload, 2)['runtime_cell'] == 'go-net-http'
    with pytest.raises(ValueError, match='capacity'):
        preflight_social(workload, 3)
    manifest.write_text('{}')
    with pytest.raises(ValueError, match='manifest'):
        preflight_social(workload, 2)


def test_social_search_uses_account_capacity(monkeypatch):
    from types import SimpleNamespace

    from tools.bench.engine import social
    from tools.bench.engine.types import TestPolicy
    from tools.bench.queue.controller import _search_policy
    monkeypatch.setattr(social, 'preflight_social', lambda workload: {'account_capacity': 37})
    test = TestPolicy.model_validate({'type': 'search', 'users': {'min': 1, 'start': 100, 'max': 'auto'}, 'limits': {'max_trials': 10, 'max_elapsed': 100.0}})
    policy = _search_policy(SimpleNamespace(workload=SimpleNamespace(adapter='social'), test=test))
    assert policy.max_users == policy.start == 37
    assert policy.limiting_reason == 'fixture_accounts'


def test_extracted_wheel_social_doctor(tmp_path):
    import subprocess
    import sys
    import zipfile
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    wheel_dir = tmp_path / 'wheel'
    subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(wheel_dir)], cwd=root, check=True, capture_output=True)
    package = tmp_path / 'installed'
    with zipfile.ZipFile(next(wheel_dir.glob('*.whl'))) as archive:
        archive.extractall(package)
    script = tmp_path / 'social-check.py'
    script.write_text(r"""import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import yaml
from tools.bench import commands
from tools.bench.engine.resources import runtime_root
from tools.bench.native_runtime import load_runtime_cell, LABEL_PREFIX, LABEL_FIELDS

with tempfile.TemporaryDirectory(prefix='social-wheel-') as root:
    root=Path(root)
    output=root/'social.yaml'
    assert commands.main(['init','--template','social','--output',str(output)])==0
    data=yaml.safe_load(output.read_text())
    data['jobs']=data['jobs'][:1]
    data['jobs'][0]['test']['users']=[2]
    output.write_text(yaml.safe_dump(data))
    assets=root/'social-assets'
    dump=assets/'dump.bin';dump.write_bytes(b'archive stub')
    (assets/'fixture.json').write_text(json.dumps(dict(schema_revision='1',checksum=hashlib.sha256(dump.read_bytes()).hexdigest(),dump='dump.bin',identity='test-id',counts={'users':2},fingerprints={'users':'hash'})))
    (assets/'actors.json').write_text(json.dumps({'actors':[{'username':'one','password':'p'},{'username':'two','password':'q'}]}))
    os.environ['BENCH_DATABASE_URL']='postgresql://test:test@localhost/language_benchmark_test'
    cell=load_runtime_cell('go-net-http',runtime_root())
    assert str(Path(os.environ['BENCH_TEST_PACKAGE_ROOT'])) in str(runtime_root())
    image={'Id':'sha256:'+'1'*64,'Os':'linux','Architecture':'amd64','Config':{'Labels':{LABEL_PREFIX+field.replace('_','-'):str(getattr(cell,field)) for field in LABEL_FIELDS}}}
    def execute(host,argv,**kwargs):
        return SimpleNamespace(stdout=json.dumps([image]) if argv[:3]==['docker','image','inspect'] else '{}')
    def discover(self):
        self.available=True
        return self
    with patch('tools.bench.engine.targets.execute_host',execute), patch('tools.bench.engine.collectors.Collector.discover',discover):
        assert commands.main(['doctor',str(output)])==0
    print('PACKAGED SOCIAL DOCTOR PASSED',runtime_root())
""")
    env = dict(os.environ, PYTHONPATH=str(package), BENCH_TEST_PACKAGE_ROOT=str(package))
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'PACKAGED SOCIAL DOCTOR PASSED' in result.stdout
