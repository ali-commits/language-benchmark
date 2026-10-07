"""Validated public configuration objects. Quantities are normalized at loading."""
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True, allow_inf_nan=False, frozen=True, strict=True)


class SecretRef(Model):
    env: str


class Host(Model):
    transport: Literal['local', 'ssh']
    host: str | None = None
    user: str | None = None
    port: StrictInt = 22
    identity_file: Path | None = None
    workdir: Path | None = None

    @model_validator(mode='after')
    def ssh_host(self):
        if self.transport == 'ssh' and not self.host:
            raise ValueError('SSH transport requires host')
        return self


class Network(Model):
    mode: Literal['compose', 'bridge', 'host', 'direct'] = 'compose'
    backend_url: str | None = None


class Telemetry(Model):
    host: Literal['required', 'optional', 'off'] = 'optional'
    container: Literal['required', 'optional', 'off'] = 'optional'
    database: Literal['required', 'optional', 'off'] = 'off'
    interval: float = 1


class Environment(Model):
    backend: Host
    generator: Host
    build: Host | None = None
    network: Network = Field(default_factory=Network)
    telemetry: Telemetry = Field(default_factory=Telemetry)
    database: dict[str, Any] | None = None


class Readiness(Model):
    path: str = '/health'
    timeout: float = 60
    interval: float = 1


class Resources(Model):
    cpus: float = Field(default=2, gt=0)
    memory: float = Field(default=3_221_225_472, gt=0)
    swap: float = Field(default=0, ge=0)
    nofile: StrictInt = Field(default=250000, gt=0)


class Build(Model):
    context: Path
    dockerfile: Path | None = None
    args: dict[str, str] = Field(default_factory=dict)


class Target(Model):
    image: str | None = None
    build: Build | None = None
    port: StrictInt = 8000
    readiness: Readiness = Field(default_factory=Readiness)
    resources: Resources = Field(default_factory=Resources)
    env: dict[str, SecretRef] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    command: list[str] | None = None

    @model_validator(mode='after')
    def source(self):
        if (self.image is None) == (self.build is None):
            raise ValueError('target requires exactly one of image or build')
        if not 1 <= self.port <= 65535:
            raise ValueError('invalid target port')
        return self


class Metric(Model):
    name: str
    type: Literal['rate', 'trend', 'counter', 'gauge']
    unit: Literal['ms', 's', 'bytes', 'fraction', 'count'] | None = None
    includes_failures: StrictBool = True


class Hook(Model):
    argv: list[str]
    host: Literal['backend', 'generator', 'controller'] = 'backend'
    timeout: float = 60
    env: dict[str, SecretRef] = Field(default_factory=dict)


class Fixture(Model):
    adapter: str = 'hooks'
    prepare: Hook | None = None
    reset: Hook | None = None
    verify: Hook | None = None


class Workload(Model):
    adapter: Literal['k6', 'social'] = 'k6'
    script: Path
    metrics: dict[str, Metric]
    parameters: dict[str, Any] = Field(default_factory=dict)
    fixture: Fixture | None = None
    dependencies: list[Path] = Field(default_factory=list)


class Fraction(Model):
    fraction_of_target: float = Field(ge=0, le=1)


Endpoint = StrictInt | Literal['target'] | Fraction


class Stage(Model):
    id: str
    from_users: Endpoint = Field(alias='from')
    to_users: Endpoint = Field(alias='to')
    duration: float = Field(gt=0)
    evaluate: StrictBool = False

    @model_validator(mode='after')
    def endpoints(self):
        if any(isinstance(v, int) and v < 0 for v in (self.from_users, self.to_users)):
            raise ValueError('negative stage endpoint')
        return self


class Criterion(Model):
    metric: str
    scope: Literal['workload', 'backend_host', 'container', 'database', 'generator'] = 'workload'
    statistic: str
    operator: Literal['>=', '<=', '>', '<', '==']
    value: float
    unit: str | None = None
    stages: list[str] | None = None
    minimum_samples: StrictInt = 1
    minimum_coverage: float = Field(default=1, ge=0, le=1)


class SearchUsers(Model):
    min: StrictInt = Field(default=1, ge=1)
    start: StrictInt = Field(ge=1)
    max: StrictInt | Literal['auto']


class SweepUsers(Model):
    start: StrictInt = Field(ge=1)
    end: StrictInt = Field(ge=1)
    step: StrictInt = Field(ge=1)


class Limits(Model):
    max_trials: StrictInt = Field(gt=0)
    max_elapsed: float = Field(gt=0)
    hard_users: StrictInt | None = Field(default=None, gt=0)


class TestPolicy(Model):
    type: Literal['fixed', 'search', 'stress', 'staged']
    strategy: Literal['search', 'sweep'] | None = None
    users: StrictInt | list[StrictInt] | SearchUsers | SweepUsers | None = None
    repetitions: StrictInt = Field(default=1, gt=0)
    stop_on_failure: StrictBool = False
    limits: Limits | None = None
    expansion_factor: float = Field(default=2, gt=1)
    resolution: StrictInt = Field(default=1, gt=0)
    confirmation: Literal['majority_on_disagreement', 'single_observation'] = 'majority_on_disagreement'
    invalid_retries: StrictInt = Field(default=1, ge=0)

    @model_validator(mode='after')
    def combinations(self):
        if self.strategy is not None and self.type != 'stress':
            raise ValueError('strategy is accepted only by stress alias')
        if self.type == 'stress':
            if self.strategy is None:
                raise ValueError('stress requires search or sweep strategy')
            object.__setattr__(self, 'type', 'search' if self.strategy == 'search' else 'fixed')
            object.__setattr__(self, 'strategy', None)
        if self.type == 'search':
            if not isinstance(self.users, SearchUsers) or self.limits is None:
                raise ValueError('search requires users bounds and finite limits')
            ceiling = self.users.max if isinstance(self.users.max, int) else self.limits.hard_users
            if self.users.start < self.users.min or (ceiling is not None and (self.users.start > ceiling or self.users.min > ceiling)):
                raise ValueError('search start/min outside ceilings')
        elif self.type == 'staged':
            if self.users is not None:
                raise ValueError('staged does not accept users')
            object.__setattr__(self, 'type', 'fixed')
        elif isinstance(self.users, SweepUsers):
            if self.users.end < self.users.start:
                raise ValueError('sweep end precedes start')
            values = list(range(self.users.start, self.users.end + 1, self.users.step))
            if values[-1] != self.users.end:
                values.append(self.users.end)
            object.__setattr__(self, 'users', values)
        elif self.users is not None:
            values = [self.users] if isinstance(self.users, int) else self.users
            if not isinstance(values, list) or not values or any(v < 1 for v in values) or len(values) != len(set(values)):
                raise ValueError('fixed users must be unique positive integers')
            object.__setattr__(self, 'users', values)
        return self


class Defaults(Model):
    environment: str | None = None
    workload: str | None = None
    stages: list[Stage] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)
    drain: float = 30
    hold_tolerance: float = Field(default=.05, ge=0, le=1)


class Job(Model):
    id: str
    targets: list[str]
    test: TestPolicy
    environment: str | None = None
    workload: str | None = None
    stages: list[Stage] | None = None
    criteria: list[Criterion] | None = None
    objectives: dict[str, list[Criterion]] = Field(default_factory=dict)
    drain: float | None = None
    hold_tolerance: float | None = Field(default=None, ge=0, le=1)


class BenchmarkConfig(Model):
    version: Literal[1]
    name: str
    definitions: list[Path] = Field(default_factory=list)
    targets: dict[str, Target]
    workloads: dict[str, Workload]
    environments: dict[str, Environment]
    defaults: Defaults = Field(default_factory=Defaults)
    jobs: list[Job]
    source: Path | None = Field(default=None, exclude=True)

    def public_snapshot(self):
        return self.model_dump(mode='json', by_alias=True)


class ResolvedJob(Model):
    id: str
    target_name: str
    target: Target
    workload_name: str
    workload: Workload
    environment_name: str
    environment: Environment
    test: TestPolicy
    stages: list[Stage]
    criteria: list[Criterion]
    objectives: dict[str, list[Criterion]] = Field(default_factory=dict)
    drain: float = 30
    hold_tolerance: float = .05
