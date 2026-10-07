"""Evaluate raw measurements over selected completion-time windows."""
import math
import operator
from .stages import criterion_windows
from .types import CriterionResult, Evaluation, TrialEvidence

_OPERATORS = {'<=':operator.le, '>=':operator.ge, '<':operator.lt, '>':operator.gt, '==':operator.eq}


class _Invalid(ValueError):
    def __init__(self, reason, count, coverage):
        super().__init__(reason)
        self.count = count
        self.coverage = coverage


def _finite(value):
    return value is not None and math.isfinite(value)


def _result(evidence, criterion):
    windows=criterion_windows(evidence.stages,criterion)
    samples=sorted((s for s in evidence.samples if s.metric==criterion.metric and s.scope==criterion.scope),key=lambda s:s.timestamp)
    temporal=any(s.kind in {'gauge','interval'} for s in samples)
    pieces=[]
    selected=[]
    if temporal:
        if any(s.kind not in {'gauge','interval'} for s in samples):
            raise ValueError('mixed temporal and event samples')
        for i,s in enumerate(samples):
            if not _finite(s.timestamp):
                raise ValueError('nonfinite timestamp')
            if s.kind=='interval':
                end=s.end_seconds
                if not _finite(end) or end<=s.timestamp:
                    raise ValueError('invalid interval')
            else:
                if not _finite(s.max_gap_seconds) or s.max_gap_seconds<=0:
                    raise ValueError('gauge requires positive maximum gap')
                end=min(samples[i+1].timestamp if i+1<len(samples) else float('inf'),s.timestamp+s.max_gap_seconds)
            overlaps=[(max(a,s.timestamp),min(b,end)) for a,b in windows if max(a,s.timestamp)<min(b,end)]
            if overlaps:
                selected.append(s)
                pieces.extend((a,b,s.value) for a,b in overlaps)
        ordered=sorted(pieces)
        if any(left[1]>right[0] for left,right in zip(ordered,ordered[1:])):
            raise ValueError('overlapping temporal intervals')
        coverage=sum(b-a for a,b,v in pieces)/sum(b-a for a,b in windows)
    else:
        selected=[s for s in samples if any(a<=s.timestamp<b for a,b in windows)]
        if any(not _finite(s.timestamp) for s in samples):
            raise ValueError('nonfinite timestamp')
        coverage=None
    count=len(selected)
    def invalid(reason):
        raise _Invalid(reason,count,coverage)
    if count<criterion.minimum_samples:
        invalid('insufficient samples')
    if any(s.unit is not None and criterion.unit is not None and s.unit!=criterion.unit for s in selected):
        invalid('contradictory metric units')
    if len({s.includes_failures for s in selected})>1:
        invalid('contradictory latency populations')
    if criterion.metric=='cpu_used':
        expected={'backend_host':'all_host_cpus','container':'container_quota'}.get(criterion.scope)
        if expected and any(s.normalization!=expected for s in selected):
            invalid('contradictory CPU normalization')
    if coverage is not None and coverage+1e-12<criterion.minimum_coverage:
        invalid('insufficient temporal coverage')
    if criterion.statistic=='rate':
        if any(s.kind!='rate' or not _finite(s.numerator) or not _finite(s.denominator) or s.denominator<0 or s.numerator<0 or s.numerator>s.denominator for s in selected):
            invalid('invalid rate numerator/denominator')
        denominator=sum(s.denominator for s in selected)
        if denominator<=0:
            invalid('empty rate denominator')
        value=sum(s.numerator for s in selected)/denominator
    else:
        if any(not _finite(s.value) for s in selected):
            invalid('missing or nonfinite required value')
        values=sorted(s.value for s in selected)
        stat=criterion.statistic
        if stat=='mean':
            value=sum((b-a)*v for a,b,v in pieces)/sum(b-a for a,b,v in pieces) if temporal else sum(values)/count
        elif stat=='max': value=max(values)
        elif stat=='min': value=min(values)
        elif stat=='count': value=float(count)
        elif stat=='sum': value=sum(values)
        elif stat.startswith('p') and not temporal:
            q=float(stat[1:])/100
            if not 0<=q<=1: invalid('invalid quantile')
            pos=(count-1)*q
            lo=int(pos)
            value=values[lo]+(values[min(lo+1,count-1)]-values[lo])*(pos-lo)
        else: invalid('unsupported statistic')
    return CriterionResult(criterion=criterion,valid=True,passed=_OPERATORS[criterion.operator](value,criterion.value),value=value,sample_count=count,coverage=coverage)


def evaluate(evidence: TrialEvidence, criteria: tuple) -> Evaluation:
    results=[]
    warnings=[]
    for criterion in criteria:
        try:
            result=_result(evidence,criterion)
        except (ValueError, ZeroDivisionError) as exc:
            result=CriterionResult(criterion=criterion,valid=False,reason=str(exc),sample_count=getattr(exc,'count',0),coverage=getattr(exc,'coverage',None))
        results.append(result)
        if criterion.statistic.startswith('p') and result.valid and float(criterion.statistic[1:])>=99 and result.sample_count<1000:
            warnings.append(f'{criterion.metric}: high quantile from {result.sample_count} samples is descriptive')
    reasons=tuple(evidence.invalid_reasons)+tuple(r.reason for r in results if r.reason)
    verdict='invalid' if not evidence.valid or any(not r.valid for r in results) else 'pass' if all(r.passed for r in results) else 'fail'
    return Evaluation(verdict=verdict,results=tuple(results),reasons=reasons,warnings=tuple(warnings))
