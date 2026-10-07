"""Bind a continuous stage timeline once per load session, without fixture resets."""
from fractions import Fraction as Rational

from .types import Criterion, Fraction, ResolvedJob, ResolvedStage


def fixed_populations(job: ResolvedJob) -> tuple[int | None, ...]:
    if job.test.type != 'fixed':
        raise ValueError('fixed populations require fixed mode')
    return tuple(job.test.users) if job.test.users is not None else (None,)


def resolve_stages(job: ResolvedJob, users: int | None) -> tuple[ResolvedStage, ...]:
    if users is not None and (type(users) is not int or users < 1):
        raise ValueError('candidate users must be a positive integer')
    if job.test.type == 'search':
        bounds = job.test.users
        if users is None or users < bounds.min:
            raise ValueError('candidate below search minimum')
        ceilings = [v for v in (bounds.max, job.test.limits.hard_users) if isinstance(v, int)]
        if ceilings and users > min(ceilings):
            raise ValueError('candidate exceeds search ceiling')
    def endpoint(value):
        if type(value) is int:
            return value
        if users is None:
            raise ValueError('target references require a population')
        return users if value == 'target' else _bind(value, users)
    result = []
    elapsed = 0.
    ids = set()
    for stage in job.stages:
        if not stage.id or stage.id in ids:
            raise ValueError('stages require unique nonempty IDs')
        ids.add(stage.id)
        start, end = endpoint(stage.from_users), endpoint(stage.to_users)
        if result and result[-1].to_users != start:
            raise ValueError('adjacent stage endpoints disagree after resolution')
        if job.test.type == 'search' and max(start, end) > users:
            raise ValueError('search stage exceeds candidate users')
        result.append(ResolvedStage(id=stage.id, from_users=start, to_users=end,
            duration=stage.duration, evaluate=stage.evaluate,
            start_seconds=elapsed, end_seconds=elapsed + stage.duration))
        elapsed += stage.duration
    if not result:
        raise ValueError('stages must be nonempty')
    return tuple(result)


def criterion_windows(stages: tuple[ResolvedStage, ...], criterion: Criterion) -> tuple[tuple[float, float], ...]:
    """Completion-time windows; drain is deliberately outside the schedule."""
    selected = [s for s in stages if s.id in criterion.stages] if criterion.stages is not None else [s for s in stages if s.evaluate]
    if criterion.stages is not None and set(criterion.stages) - {s.id for s in stages}:
        raise ValueError('unresolved criterion stage reference')
    if not selected:
        raise ValueError('criterion has no selected stage windows')
    if criterion.scope == 'workload' and not any(max(s.from_users, s.to_users) > 0 for s in selected):
        raise ValueError('no-traffic stages cannot qualify a response criterion')
    return tuple((s.start_seconds, s.end_seconds) for s in selected)


def _ratio(endpoint):
    return Rational(1) if endpoint == 'target' else Rational(str(endpoint.fraction_of_target))


def _bind(endpoint, users):
    if type(endpoint) is int:
        return endpoint
    fraction = _ratio(endpoint)
    return users * fraction.numerator // fraction.denominator


def _floor_sum(n, denominator, numerator, offset):
    """Sum floor((numerator*i + offset)/denominator), i in [0,n).

    Euclidean reduction takes logarithmic time even for enormous user ceilings.
    """
    total = 0
    while True:
        whole, numerator = divmod(numerator, denominator)
        total += n * (n - 1) // 2 * whole
        whole, offset = divmod(offset, denominator)
        total += n * whole
        extent = numerator * n + offset
        if extent < denominator:
            return total
        n, offset = divmod(extent, denominator)
        denominator, numerator = numerator, denominator


def _agree(left, right, minimum, maximum):
    if type(left) is int and type(right) is int:
        return left == right
    if type(left) is int or type(right) is int:
        absolute, reference = (left, right) if type(left) is int else (right, left)
        if maximum is None:
            return absolute == 0 and _ratio(reference) == 0
        # A nondecreasing reference is constant across the interval iff its
        # values at both extremes equal the absolute endpoint.
        return _bind(reference, minimum) == absolute == _bind(reference, maximum)
    a, b = _ratio(left), _ratio(right)
    if a == b:
        return True
    if maximum is None:
        return False
    n = maximum - minimum + 1
    def total(fraction):
        return _floor_sum(n, fraction.denominator, fraction.numerator, minimum * fraction.numerator)
    # Floors preserve fractional ordering, so differences never change sign;
    # equal interval sums prove equality at every candidate, not only extremes.
    return total(a) == total(b)


def validate_stages(job: ResolvedJob) -> None:
    """Reject infeasible templates before any runner or search is started."""
    if job.test.type == 'search':
        def at_target(endpoint):
            return endpoint == 'target' or isinstance(endpoint, Fraction) and endpoint.fraction_of_target == 1
        if not any(s.evaluate and at_target(s.from_users) and at_target(s.to_users) for s in job.stages):
            raise ValueError('search requires an evaluated hold at target')
        populations = (job.test.users.min, job.test.users.start)
        ceilings = [v for v in (job.test.users.max, job.test.limits.hard_users) if isinstance(v, int)]
        maximum = min(ceilings) if ceilings else None
        for previous, following in zip(job.stages, job.stages[1:]):
            if not _agree(previous.to_users, following.from_users, job.test.users.min, maximum):
                raise ValueError('adjacent search endpoints disagree within candidate bounds')
    else:
        populations = fixed_populations(job)
    for population in populations:
        stages = resolve_stages(job, population)
        for criterion in [*job.criteria, *(c for group in job.objectives.values() for c in group)]:
            criterion_windows(stages, criterion)
