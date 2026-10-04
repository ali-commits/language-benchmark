"""Worker identity emitted after imports and pool initialization, never in hot paths."""
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import sysconfig

def runtime_snapshot() -> dict:
    jit=getattr(sys,'_jit',None)
    return dict(pid=os.getpid(),run_id=os.environ.get('RUN_ID',''),python=platform.python_version(),version=sys.version,
        gil=getattr(sys,'_is_gil_enabled',lambda:True)(),free_threaded=bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
        jit_available=bool(jit and jit.is_available()),jit=bool(jit and jit.is_enabled()),
        compiler=platform.python_compiler(),config_args=sysconfig.get_config_var('CONFIG_ARGS'),
        dependencies={d.metadata['Name']:d.version for d in importlib.metadata.distributions()})

def publish(cfg) -> dict:
    snapshot=runtime_snapshot()
    if snapshot['gil']!=cfg.expected_gil or snapshot['jit']!=cfg.expected_jit:
        raise RuntimeError('post-import GIL/JIT mode does not match configuration')
    if cfg.run_dir:
        path=Path(cfg.run_dir);path.mkdir(parents=True,exist_ok=True)
        f=path/f'{snapshot["pid"]}.json'
        temporary=f.with_suffix('.tmp');temporary.write_text(json.dumps(snapshot));temporary.replace(f)
    return snapshot
