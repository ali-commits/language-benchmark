import os

import pytest

from tools.bench import commands
from tools.bench.engine.config import load_config
from tools.bench.queue.store import QueueStore


def template(tmp_path):
    path = tmp_path / 'path with spaces' / 'comparison.yaml'
    assert commands.main(['init', '--output', str(path)]) == 0
    return path


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
    path.write_text(path.read_text().replace('build: {context: ./comparison-assets/}', 'image: test:local'))
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
    path.write_text(path.read_text().replace('build: {context: ./comparison-assets/}', 'image: test:local'))
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
    path.write_text(path.read_text().replace('build: {context: ./comparison-assets/}', 'image: test:local'))
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
    path.write_text(path.read_text().replace('build: {context: ./comparison-assets/}', 'image: test:local'))
    state = tmp_path / 'state'
    store = QueueStore(state / 'queue.db')
    store.register(load_config(path))
    store.claim_next('http-demo', 'test-owner')
    assert commands.main(['queue', 'stop', 'http-demo', '--timeout', '0', '--state-dir', str(state)]) == 1
    assert 'cleanup has not yet been verified' in capsys.readouterr().err


def test_detached_start_explicit_environment_path(tmp_path, monkeypatch):
    from tools.bench.queue import services
    path = template(tmp_path)
    path.write_text(path.read_text().replace('build: {context: ./comparison-assets/}', 'image: test:local'))
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
