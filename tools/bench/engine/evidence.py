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


def execution_identity(job, users, *, provenance=None):
    payload=job.model_dump(mode='json',by_alias=True)
    for field in ('criteria','objectives','test','id'):
        payload.pop(field,None)
    payload['stages']=[s.model_dump(mode='json',exclude={'evaluate'}) for s in resolve_stages(job,users)]
    payload['users']=users
    if provenance is not None:
        if not provenance.get('image_id') or not provenance.get('workload_identity'):
            raise ValueError('prepared provenance requires image and workload identities')
        if job.target.build and not provenance.get('build_input_identity'):
            raise ValueError('prepared provenance requires build input identity')
        payload['prepared_inputs']=provenance
        return _digest(payload)
    payload['files']={str(p):_file_identity(p) for p in [job.workload.script,*job.workload.dependencies]}
    if job.target.build:
        context=job.target.build.context
        if not context.is_dir():
            raise ValueError(f'build context is not a directory: {context}')
        payload['build_files']={str(p.relative_to(context)):_file_identity(p) for p in sorted(context.rglob('*')) if p.is_file() and '.git' not in p.parts}
        if job.target.build.dockerfile:
            payload['dockerfile_hash']=_file_identity(job.target.build.dockerfile)
    return _digest(payload)


def decision_identity(job, objective):
    if objective != 'default' and objective not in job.objectives:
        raise ValueError(f'unknown objective: {objective}')
    return _digest({'objective':objective,'criteria':[c.model_dump(mode='json') for c in [*job.criteria,*job.objectives.get(objective,[])]], 'evaluation_stages':[s.id for s in job.stages if s.evaluate], 'confirmation':job.test.confirmation})


def immutable_json(path: Path, value):
    """Publish a complete artifact without replacing an existing artifact."""
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


class Journal:
    """One immutable intent/completion file per transition; protected by trial lock."""
    def __init__(self, directory: Path):
        self.directory = directory / 'journal'
        self.directory.mkdir(parents=True, exist_ok=True)

    def entries(self):
        return [json.loads(p.read_text()) for p in sorted(self.directory.glob('*.json'))]

    def record(self, action, state, details=None):
        import time
        entries = self.entries()
        value = {'sequence': len(entries), 'action': action, 'state': state,
                 'epoch': time.time(), 'details': details or {}}
        immutable_json(self.directory / f'{len(entries):06d}.json', value)
        return value
