"""Safe YAML loading, quantity normalization and explicit default resolution."""
import json
import math
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import JsonValue, TypeAdapter, ValidationError

from ..records import checked_record
from .records import RawConfig, RawJob
from .types import BenchmarkConfig, ResolvedJob


class ConfigError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    # YAML 1.2 booleans: off/on remain strings (e.g. telemetry: off).
    yaml_implicit_resolvers = {
        key: [(tag, pattern) for tag, pattern in rules
              if tag != 'tag:yaml.org,2002:bool']
        for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


UniqueLoader.add_implicit_resolver('tag:yaml.org,2002:bool',
    re.compile(r'^(?:true|false|True|False|TRUE|FALSE)$'), list('tTfF'))


def _mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, object]:
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ConfigError(f'duplicate or non-string YAML key: {key!r}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)

_FACTORS: dict[str, dict[str, float]] = {'seconds': {'ms': .001, 's': 1, 'm': 60, 'h': 3600},
            'ms': {'ms': 1, 's': 1000, 'm': 60000},
            'bytes': {'B': 1, 'KB': 1000, 'MB': 1000000, 'GB': 1000000000,
                      'KiB': 1024, 'MiB': 1048576, 'GiB': 1073741824},
            'fraction': {'%': .01}, 'count': {}}


def parse_quantity(value: object, unit: str) -> float:
    if isinstance(value, bool):
        raise ConfigError('boolean is not a quantity')
    if isinstance(value, str):
        match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([A-Za-z%]+)', value)
        if not match or match[2] not in _FACTORS.get(unit, {}):
            raise ConfigError(f'incompatible quantity {value!r} for {unit}')
        value = float(match[1]) * _FACTORS[unit][match[2]]
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigError('quantity must be finite numeric value')
    return value


_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def _opaque_field(path: tuple[str, ...]) -> bool:
    return (
        len(path) == 3
        and ((path[0] == "workloads" and path[2] == "parameters")
             or (path[0] == "targets" and path[2] == "labels"))
    ) or (len(path) == 4 and path[0] == "targets" and path[2:] == ("build", "args"))


def _normalize(value: object, base: Path, key: str = '', *, field_path: tuple[str, ...] = ()) -> object:
    if _opaque_field(field_path):
        # These maps belong to the workload or Docker, so their keys are literal.
        literal = _JSON_VALUE.validate_python(value, strict=True)
        json.dumps(literal, allow_nan=False)  # Reject nonfinite numbers at every depth.
        return literal
    if isinstance(value, dict):
        if key == 'hold_tolerance' and 'fraction' in value:
            value = {**value, 'fraction': parse_quantity(value['fraction'], 'fraction')}
        if set(value) == {'env'}:
            if not isinstance(value['env'], str) or value['env'] not in os.environ:
                raise ConfigError(f'missing environment reference: {value["env"]!r}')
        return {k: _normalize(v, base, k, field_path=(*field_path, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v, base, key, field_path=field_path) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError('nonfinite number')
    if key in {'script', 'context', 'dockerfile', 'identity_file', 'workdir', 'dependencies', 'manifest'} and isinstance(value, str):
        path = (base / value).resolve()
        if key != 'workdir' and not path.exists():
            raise ConfigError(f'path does not exist: {path}')
        return path
    if key in {'duration', 'timeout', 'interval', 'drain', 'graceful_ramp_down', 'max_elapsed', 'grace'}:
        return parse_quantity(value, 'seconds')
    if key == 'hold_tolerance' and isinstance(value, (int, float)):
        return {'fraction': parse_quantity(value, 'fraction')}
    if key in {'memory', 'swap'}:
        return parse_quantity(value, 'bytes')
    return value


def _read(path: Path) -> dict[str, object]:
    try:
        data = yaml.load(path.read_text(), Loader=UniqueLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f'cannot read configuration {path}: {exc}') from exc
    if not isinstance(data, dict):
        raise ConfigError('configuration must be a mapping')
    return checked_record(_normalize(data, path.parent), dict[str, object])


def _load_config(path: Path) -> BenchmarkConfig:
    path = Path(path).resolve()
    data = checked_record(_read(path), RawConfig)
    if type(data.get('version')) is not int or data['version'] != 1:
        raise ConfigError('version must be integer 1')
    imports = data.get('definitions', [])
    if not isinstance(imports, list) or any(not isinstance(p, str) for p in imports):
        raise ConfigError('definitions must be a list of paths')
    imported_paths = [(path.parent / item).resolve() for item in imports]
    for imported in imported_paths:
        definitions = checked_record(_read(imported), RawConfig)
        if set(definitions) - {'version', 'targets', 'workloads', 'environments'} or type(definitions.get('version')) is not int or definitions['version'] != 1:
            raise ConfigError('definition files accept version, targets, workloads and environments only')
        _merge_group(data.setdefault("targets", {}), definitions.get("targets", {}), "targets")
        _merge_group(data.setdefault("workloads", {}), definitions.get("workloads", {}), "workloads")
        _merge_group(data.setdefault("environments", {}), definitions.get("environments", {}), "environments")
    # Criteria need the selected workload's metric contract before model validation.
    defaults = data.get('defaults', {})
    jobs = data.get('jobs', [])
    original_criteria = deepcopy(defaults.get('criteria', []))
    default_workload = defaults.get('workload') or next(
        (job.get('workload') for job in jobs if isinstance(job, dict) and job.get('workload')), None)
    default_criteria_job: RawJob = {'workload': default_workload, 'criteria': deepcopy(original_criteria)}
    for job in [*jobs, default_criteria_job]:
        if 'criteria' not in job:
            job['criteria'] = deepcopy(original_criteria)
        workload = data.get('workloads', {}).get(job.get('workload', defaults.get('workload')) or '', {})
        metrics = {m['name']: m for m in workload.get('metrics', {}).values()}
        groups = [job.get('criteria', defaults.get('criteria', [])), *job.get('objectives', {}).values()]
        for criteria in groups:
            for criterion in criteria:
                metric = metrics.get(criterion.get('metric', ''))
                scope = criterion.get('scope', 'workload')
                if metric is None and scope == 'workload':
                    raise ConfigError(f'unknown workload metric: {criterion.get("metric")}')
                if metric is None:
                    if criterion.get('metric') not in {'memory_used', 'cpu_used'}:
                        raise ConfigError('unknown resource metric')
                    metric = {'type': 'gauge', 'unit': 'bytes' if criterion['metric'] == 'memory_used' else 'fraction'}
                stat = criterion.get('statistic', '')
                valid = {'rate'} if metric['type'] == 'rate' else {'mean', 'max', 'min', 'count', 'sum'}
                if metric['type'] == 'trend' and re.fullmatch(r'p(?:100|[0-9]{1,2})(?:\.\d+)?', stat):
                    valid.add(stat)
                if stat not in valid:
                    raise ConfigError('statistic incompatible with metric type')
                unit = metric.get('unit') or ('fraction' if metric['type'] == 'rate' else 'count')
                if criterion.get('unit', unit) != unit:
                    raise ConfigError('criterion unit incompatible with metric')
                quantity = parse_quantity(criterion.get('value'), 'seconds' if unit == 's' else unit)
                criterion['value'] = quantity
                criterion['unit'] = unit
                if metric['type'] == 'rate' and not 0 < quantity <= 1:
                    raise ConfigError('rate criterion must be in (0, 1]')
    if 'criteria' in defaults:
        defaults['criteria'] = default_criteria_job['criteria']
    try:
        config = BenchmarkConfig.model_validate({**data, 'definitions': imported_paths, 'source': path})
        resolve_jobs(config)
        return config
    except (ValidationError, KeyError, TypeError) as exc:
        raise ConfigError(str(exc)) from exc


def resolve_jobs(config: BenchmarkConfig) -> list[ResolvedJob]:
    result = []
    ids = set()
    for job in config.jobs:
        if job.id in ids:
            raise ConfigError(f'duplicate job id: {job.id}')
        ids.add(job.id)
        environment_name = job.environment or config.defaults.environment
        workload_name = job.workload or config.defaults.workload
        if environment_name is None or workload_name is None or environment_name not in config.environments or workload_name not in config.workloads:
            raise ConfigError('unresolved environment or workload reference')
        if not job.targets or len(set(job.targets)) != len(job.targets):
            raise ConfigError('targets must be nonempty and unique')
        stages = job.stages if job.stages is not None else config.defaults.stages
        criteria = job.criteria if job.criteria is not None else config.defaults.criteria
        if not stages or len({s.id for s in stages}) != len(stages):
            raise ConfigError('stages must be nonempty with unique IDs')
        for criterion in [*criteria, *(c for group in job.objectives.values() for c in group)]:
            if criterion.stages is not None and (not criterion.stages or set(criterion.stages) - {s.id for s in stages}):
                raise ConfigError('unresolved criterion stage reference')
        if job.objectives and job.test.type != 'search':
            raise ConfigError('objectives require search mode')
        for target_name in job.targets:
            if target_name not in config.targets:
                raise ConfigError(f'unresolved target: {target_name}')
            result.append(ResolvedJob(id=job.id, target_name=target_name, target=config.targets[target_name],
                workload_name=workload_name, workload=config.workloads[workload_name],
                environment_name=environment_name, environment=config.environments[environment_name],
                test=job.test, stages=stages, criteria=criteria, objectives=job.objectives,
                graceful_ramp_down=job.graceful_ramp_down if job.graceful_ramp_down is not None else config.defaults.graceful_ramp_down,
                drain=job.drain if job.drain is not None else config.defaults.drain,
                hold_tolerance=job.hold_tolerance if job.hold_tolerance is not None else config.defaults.hold_tolerance))
    from .stages import validate_stages
    for resolved in result:
        try:
            validate_stages(resolved)
        except ValueError as exc:
            raise ConfigError(f"job {resolved.id}: {exc}") from exc
    return result


def load_config(path: Path) -> BenchmarkConfig:
    try:
        return _load_config(path)
    except ConfigError:
        raise
    except (ValidationError, AttributeError, TypeError, KeyError, ValueError) as exc:
        raise ConfigError(f'invalid configuration structure: {exc}') from exc


Definition = TypeVar("Definition")

def _merge_group(target: dict[str, Definition], source: dict[str, Definition], group: str) -> None:
    for name, definition in source.items():
        if name in target:
            raise ConfigError(f"conflicting imported definition: {group}.{name}")
        target[name] = definition
