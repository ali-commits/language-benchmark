from pathlib import Path

import pytest
import yaml

from tools.bench.engine.config import ConfigError, load_config, resolve_jobs, parse_quantity


def document(tmp_path, **updates):
    (tmp_path / 'api.js').write_text('// journey')
    data = dict(version=1, name='example', targets={'api': {'image': 'api:1', 'port': 8000}},
                workloads={'http': {'adapter': 'k6', 'script': './api.js', 'metrics': {'latency': {'name': 'duration', 'type': 'trend', 'unit': 'ms'}}}},
                environments={'local': {'backend': {'transport': 'local'}, 'generator': {'transport': 'local'}, 'network': {'mode': 'compose'}}},
                defaults={'workload': 'http', 'environment': 'local', 'stages': [{'id': 'hold', 'from': 'target', 'to': 'target', 'duration': '5m', 'evaluate': True}]},
                jobs=[{'id': 'measure', 'targets': ['api'], 'test': {'type': 'fixed', 'users': [10, 20]}}])
    data.update(updates)
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_units():
    assert parse_quantity('5m', 'seconds') == 300
    assert parse_quantity('1GB', 'bytes') == 1_000_000_000
    assert parse_quantity('1GiB', 'bytes') == 1_073_741_824
    assert parse_quantity('50%', 'fraction') == .5


def test_relative_and_fixed(tmp_path, monkeypatch):
    path = document(tmp_path)
    monkeypatch.chdir('/')
    config = load_config(path)
    assert config.workloads['http'].script == tmp_path / 'api.js'
    job = resolve_jobs(config)[0]
    assert job.test.users == [10, 20]
    assert job.stages[0].duration == 300


@pytest.mark.parametrize('text', ['version: 1\nversion: 1', 'version: 1\nunknown: true'])
def test_bad_yaml(tmp_path, text):
    path = tmp_path / 'bad.yaml'
    path.write_text(text)
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize('users', [True, [1, True], [2, 2], [], 0, float('nan')])
def test_bad_users(tmp_path, users):
    path = document(tmp_path, jobs=[{'id': 'bad', 'targets': ['api'], 'test': {'type': 'fixed', 'users': users}}])
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_environment_secret(tmp_path, monkeypatch):
    monkeypatch.delenv('BENCH_MISSING', raising=False)
    path = document(tmp_path, targets={'api': {'image': 'api:1', 'port': 8000, 'env': {'TOKEN': {'env': 'BENCH_MISSING'}}}})
    with pytest.raises(ConfigError):
        load_config(path)


def test_secret_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv('BENCH_TOKEN', 'private-secret')
    config = load_config(document(tmp_path, targets={'api': {'image': 'api:1', 'port': 8000, 'env': {'TOKEN': {'env': 'BENCH_TOKEN'}}}}))
    assert 'private-secret' not in config.model_dump_json()
    assert 'BENCH_TOKEN' in config.model_dump_json()


def test_import_collision(tmp_path):
    imported = tmp_path / 'defs.yaml'
    imported.write_text('version: 1\ntargets:\n  api: {image: other:1, port: 8000}\n')
    with pytest.raises(ConfigError):
        load_config(document(tmp_path, definitions=['defs.yaml']))


def test_criterion_units(tmp_path):
    path = document(tmp_path, jobs=[{'id': 'bad', 'targets': ['api'], 'criteria': [{'metric': 'duration', 'statistic': 'p99', 'operator': '<=', 'value': '1GiB'}], 'test': {'type': 'fixed', 'users': 1}}])
    with pytest.raises(ConfigError):
        load_config(path)


def test_sweep_and_search_normalization(tmp_path):
    jobs = [{'id': 'curve', 'targets': ['api'], 'test': {'type': 'stress', 'strategy': 'sweep', 'users': {'start': 10, 'end': 25, 'step': 10}}},
            {'id': 'boundary', 'targets': ['api'], 'test': {'type': 'search', 'users': {'min': 1, 'start': 10, 'max': 'auto'}, 'limits': {'max_trials': 10, 'max_elapsed': '1h'}}}]
    config = load_config(document(tmp_path, jobs=jobs))
    assert config.jobs[0].test.type == 'fixed'
    assert config.jobs[0].test.users == [10, 20, 25]
    assert config.jobs[1].test.limits.max_elapsed == 3600


def test_list_overrides_replace(tmp_path):
    jobs = [{'id': 'a', 'targets': ['api'], 'stages': [{'id': 'custom', 'from': 5, 'to': 5, 'duration': '1s'}], 'criteria': [], 'test': {'type': 'staged'}}]
    job = resolve_jobs(load_config(document(tmp_path, jobs=jobs)))[0]
    assert [s.id for s in job.stages] == ['custom']
    assert job.criteria == []


def test_fixed_cannot_use_search_strategy(tmp_path):
    path = document(tmp_path, jobs=[{'id': 'bad', 'targets': ['api'], 'test': {'type': 'fixed', 'strategy': 'search', 'users': 1}}])
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize('updates', [{'jobs': ['malformed']}, {'targets': []}, {'version': True}])
def test_malformed_shapes_are_configuration_errors(tmp_path, updates):
    with pytest.raises(ConfigError):
        load_config(document(tmp_path, **updates))
