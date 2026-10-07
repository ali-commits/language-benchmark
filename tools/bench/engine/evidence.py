"""Separate identities for immutable execution and objective classification."""
import hashlib
import json
from pathlib import Path
from .stages import resolve_stages


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _file_identity(path):
    if not path.is_file():
        raise ValueError(f'identity input is not a file: {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execution_identity(job, users):
    payload=job.model_dump(mode='json',by_alias=True)
    for field in ('criteria','objectives','test','id'):
        payload.pop(field,None)
    payload['stages']=[s.model_dump(mode='json',exclude={'evaluate'}) for s in resolve_stages(job,users)]
    payload['users']=users
    payload['files']={str(p):_file_identity(p) for p in [job.workload.script,*job.workload.dependencies]}
    if job.target.build:
        context=job.target.build.context
        payload['build_files']={str(p.relative_to(context)):_file_identity(p) for p in sorted(context.rglob('*')) if p.is_file() and '.git' not in p.parts}
        if job.target.build.dockerfile:
            payload['dockerfile_hash']=_file_identity(job.target.build.dockerfile)
    return _digest(payload)


def decision_identity(job, objective):
    if objective != 'default' and objective not in job.objectives:
        raise ValueError(f'unknown objective: {objective}')
    return _digest({'objective':objective,'criteria':[c.model_dump(mode='json') for c in [*job.criteria,*job.objectives.get(objective,[])]], 'evaluation_stages':[s.id for s in job.stages if s.evaluate], 'confirmation':job.test.confirmation})
