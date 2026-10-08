import json

import pytest
import yaml

from tools.bench.engine.config import ConfigError, load_config, parse_quantity, resolve_jobs


def document(tmp_path, **updates):
    (tmp_path / "api.js").write_text("// journey")
    data = dict(
        version=1,
        name="example",
        targets={"api": {"image": "api:1", "port": 8000}},
        workloads={
            "http": {
                "adapter": "k6",
                "script": "./api.js",
                "metrics": {"latency": {"name": "duration", "type": "trend", "unit": "ms"}},
            }
        },
        environments={
            "local": {
                "backend": {"transport": "local"},
                "generator": {"transport": "local"},
                "network": {"mode": "compose"},
            }
        },
        defaults={
            "workload": "http",
            "environment": "local",
            "stages": [
                {"id": "hold", "from": "target", "to": "target", "duration": "5m", "evaluate": True}
            ],
        },
        jobs=[{"id": "measure", "targets": ["api"], "test": {"type": "fixed", "users": [10, 20]}}],
    )
    data.update(updates)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_units():
    assert parse_quantity("5m", "seconds") == 300
    assert parse_quantity("1GB", "bytes") == 1_000_000_000
    assert parse_quantity("1GiB", "bytes") == 1_073_741_824
    assert parse_quantity("50%", "fraction") == 0.5


def test_relative_and_fixed(tmp_path, monkeypatch):
    path = document(tmp_path)
    monkeypatch.chdir("/")
    config = load_config(path)
    assert config.workloads["http"].script == tmp_path / "api.js"
    job = resolve_jobs(config)[0]
    assert job.test.users == [10, 20]
    assert job.stages[0].duration == 300


@pytest.mark.parametrize("text", ["version: 1\nversion: 1", "version: 1\nunknown: true"])
def test_bad_yaml(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("users", [True, [1, True], [2, 2], [], 0, float("nan")])
def test_bad_users(tmp_path, users):
    path = document(
        tmp_path,
        jobs=[{"id": "bad", "targets": ["api"], "test": {"type": "fixed", "users": users}}],
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_environment_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_MISSING", raising=False)
    path = document(
        tmp_path,
        targets={
            "api": {"image": "api:1", "port": 8000, "env": {"TOKEN": {"env": "BENCH_MISSING"}}}
        },
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_secret_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TOKEN", "private-secret")
    config = load_config(
        document(
            tmp_path,
            targets={
                "api": {"image": "api:1", "port": 8000, "env": {"TOKEN": {"env": "BENCH_TOKEN"}}}
            },
        )
    )
    assert "private-secret" not in config.model_dump_json()
    assert "BENCH_TOKEN" in config.model_dump_json()
    snapshot = json.dumps(config.public_snapshot())
    assert "private-secret" not in snapshot
    assert "BENCH_TOKEN" in snapshot


def test_import_collision(tmp_path):
    imported = tmp_path / "defs.yaml"
    imported.write_text("version: 1\ntargets:\n  api: {image: other:1, port: 8000}\n")
    with pytest.raises(ConfigError):
        load_config(document(tmp_path, definitions=["defs.yaml"]))


def test_criterion_units(tmp_path):
    path = document(
        tmp_path,
        jobs=[
            {
                "id": "bad",
                "targets": ["api"],
                "criteria": [
                    {"metric": "duration", "statistic": "p99", "operator": "<=", "value": "1GiB"}
                ],
                "test": {"type": "fixed", "users": 1},
            }
        ],
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_sweep_and_search_normalization(tmp_path):
    jobs = [
        {
            "id": "curve",
            "targets": ["api"],
            "test": {
                "type": "stress",
                "strategy": "sweep",
                "users": {"start": 10, "end": 25, "step": 10},
            },
        },
        {
            "id": "boundary",
            "targets": ["api"],
            "test": {
                "type": "search",
                "users": {"min": 1, "start": 10, "max": "auto"},
                "limits": {"max_trials": 10, "max_elapsed": "1h"},
            },
        },
    ]
    config = load_config(document(tmp_path, jobs=jobs))
    assert config.jobs[0].test.type == "fixed"
    assert config.jobs[0].test.users == [10, 20, 25]
    assert config.jobs[1].test.limits.max_elapsed == 3600


def test_list_overrides_replace(tmp_path):
    jobs = [
        {
            "id": "a",
            "targets": ["api"],
            "stages": [{"id": "custom", "from": 5, "to": 5, "duration": "1s"}],
            "criteria": [],
            "test": {"type": "staged"},
        }
    ]
    job = resolve_jobs(load_config(document(tmp_path, jobs=jobs)))[0]
    assert [s.id for s in job.stages] == ["custom"]
    assert job.criteria == []


def test_fixed_cannot_use_search_strategy(tmp_path):
    path = document(
        tmp_path,
        jobs=[
            {
                "id": "bad",
                "targets": ["api"],
                "test": {"type": "fixed", "strategy": "search", "users": 1},
            }
        ],
    )
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("updates", [{"jobs": ["malformed"]}, {"targets": []}, {"version": True}])
def test_malformed_shapes_are_configuration_errors(tmp_path, updates):
    with pytest.raises(ConfigError):
        load_config(document(tmp_path, **updates))


def test_shared_default_criteria_normalize_per_workload(tmp_path):
    workloads = {
        name: {
            "adapter": "k6",
            "script": "./api.js",
            "metrics": {"latency": {"name": "duration", "type": "trend", "unit": unit}},
        }
        for name, unit in [("millis", "ms"), ("seconds", "s")]
    }
    defaults = {
        "workload": "millis",
        "environment": "local",
        "stages": [
            {"id": "hold", "from": "target", "to": "target", "duration": "1s", "evaluate": True}
        ],
        "criteria": [{"metric": "duration", "statistic": "p99", "operator": "<=", "value": "1s"}],
    }
    jobs = [
        {"id": name, "targets": ["api"], "workload": name, "test": {"type": "fixed", "users": 1}}
        for name in workloads
    ]
    resolved = resolve_jobs(
        load_config(document(tmp_path, workloads=workloads, defaults=defaults, jobs=jobs))
    )
    assert [(j.criteria[0].value, j.criteria[0].unit) for j in resolved] == [(1000, "ms"), (1, "s")]


def test_overridden_defaults_still_normalize(tmp_path):
    path = document(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["defaults"]["criteria"] = [
        {"metric": "duration", "statistic": "p99", "operator": "<=", "value": "1s"}
    ]
    data["jobs"][0]["criteria"] = []
    path.write_text(yaml.safe_dump(data))
    config = load_config(path)
    assert config.defaults.criteria[0].value == 1000
    assert resolve_jobs(config)[0].criteria == []


def test_numeric_max_also_enforces_hard_users(tmp_path):
    jobs = [
        {
            "id": "search",
            "targets": ["api"],
            "test": {
                "type": "search",
                "users": {"min": 1, "start": 100, "max": 1000},
                "limits": {"max_trials": 10, "max_elapsed": "1h", "hard_users": 10},
            },
        }
    ]
    with pytest.raises(ConfigError):
        load_config(document(tmp_path, jobs=jobs))


@pytest.mark.parametrize("version", ["true", "1.0"])
def test_imported_version_is_exact_integer(tmp_path, version):
    (tmp_path / "defs.yaml").write_text(f"version: {version}\n")
    with pytest.raises(ConfigError):
        load_config(document(tmp_path, definitions=["defs.yaml"]))


def test_graceful_ramp_down_default_override_and_quantity(tmp_path):
    from tools.bench.engine.types import Defaults

    assert Defaults().graceful_ramp_down == 5
    path = document(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["defaults"]["graceful_ramp_down"] = "200ms"
    data["jobs"][0]["graceful_ramp_down"] = "0s"
    path.write_text(yaml.safe_dump(data))
    config = load_config(path)
    assert config.defaults.graceful_ramp_down == 0.2
    assert resolve_jobs(config)[0].graceful_ramp_down == 0
    with pytest.raises(Exception):
        Defaults(graceful_ramp_down=-1.0)


@pytest.mark.parametrize(
    "parameters",
    [
        {"context": "feed", "duration": "business-value", "nested": {"memory": "low"}},
        {"env": "staging"},
        {"items": [{"manifest": "opaque-record", "timeout": "never"}], "null": None},
    ],
)
def test_workload_parameters_preserve_engine_named_keys(tmp_path, parameters):
    path = document(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["workloads"]["http"]["parameters"] = parameters
    path.write_text(yaml.safe_dump(data))
    config = load_config(path)
    assert config.workloads["http"].parameters == parameters


def test_labels_and_build_args_preserve_literal_strings(tmp_path):
    path = document(tmp_path)
    data = yaml.safe_load(path.read_text())
    literals = {"memory": "low", "context": "feed", "timeout": "never", "env": "staging"}
    data["targets"]["api"] = {
        "build": {"context": ".", "args": literals},
        "labels": literals,
    }
    path.write_text(yaml.safe_dump(data))
    target = load_config(path).targets["api"]
    assert target.labels == literals
    assert target.build.context == tmp_path
    assert target.build.args == literals


@pytest.mark.parametrize("invalid", [float("inf"), float("nan")])
def test_opaque_parameters_still_reject_nonfinite_json(tmp_path, invalid):
    path = document(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["workloads"]["http"]["parameters"] = {"nested": [invalid]}
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("unit_fields", [{}, {"unit": None}])
def test_metric_unit_may_be_omitted_or_explicitly_null(tmp_path, unit_fields):
    path = document(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["workloads"]["http"]["metrics"] = {
        "requests": {"name": "requests", "type": "counter", **unit_fields}
    }
    path.write_text(yaml.safe_dump(data))
    metric = load_config(path).workloads["http"].metrics["requests"]
    assert metric.unit is None
    assert metric.model_dump(mode="json")["unit"] is None


@pytest.mark.parametrize("method", [None, "binary", "hybrid"])
def test_search_method_new_yaml(tmp_path, method):
    test = {
        "type": "search",
        "users": {"min": 1, "start": 10, "max": 100},
        "limits": {"max_trials": 10, "max_elapsed": "1h"},
    }
    if method is not None:
        test["search_method"] = method
    cfg = load_config(document(tmp_path, jobs=[{"id": "search", "targets": ["api"], "test": test}]))
    assert cfg.jobs[0].test.search_method == (method or "hybrid")


def test_unknown_search_method_rejected(tmp_path):
    test = {
        "type": "search",
        "search_method": "magic",
        "users": {"start": 1, "max": 10},
        "limits": {"max_trials": 10, "max_elapsed": "1h"},
    }
    with pytest.raises(ConfigError):
        load_config(document(tmp_path, jobs=[{"id": "search", "targets": ["api"], "test": test}]))


def test_search_method_binds_decision_preserving_legacy_binary_hash(tmp_path):
    import hashlib

    from tools.bench.engine.evidence import decision_identity, execution_identity

    test = {
        "type": "search",
        "search_method": "binary",
        "users": {"start": 1, "max": 100},
        "limits": {"max_trials": 10, "max_elapsed": "1h"},
    }
    binary = resolve_jobs(
        load_config(document(tmp_path, jobs=[{"id": "search", "targets": ["api"], "test": test}]))
    )[0]
    hybrid = binary.model_copy(
        update={"test": binary.test.model_copy(update={"search_method": "hybrid"})}
    )
    legacy_payload = {
        "objective": "default",
        "criteria": [],
        "evaluation_stages": ["hold"],
        "confirmation": binary.test.confirmation,
    }
    legacy_hash = hashlib.sha256(
        json.dumps(legacy_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert decision_identity(binary, "default") == legacy_hash
    assert decision_identity(hybrid, "default") != legacy_hash
    assert execution_identity(binary, 1) == execution_identity(hybrid, 1)
