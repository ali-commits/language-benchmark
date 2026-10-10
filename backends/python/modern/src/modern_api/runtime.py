"""Worker identity emitted after imports and pool initialization, never in hot paths."""

import importlib.metadata
import json
import os
import platform
import sys
import sysconfig
from pathlib import Path

from psycopg import pq


def runtime_snapshot() -> dict:
    jit = getattr(sys, "_jit", None)
    return dict(
        pid=os.getpid(),
        run_id=os.environ.get("RUN_ID", ""),
        python=platform.python_version(),
        version=sys.version,
        gil=getattr(sys, "_is_gil_enabled", lambda: True)(),
        free_threaded=bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        jit_available=bool(jit and jit.is_available()),
        jit=bool(jit and jit.is_enabled()),
        compiler=platform.python_compiler(),
        config_args=sysconfig.get_config_var("CONFIG_ARGS"),
        postgres_driver=pq.__impl__,
        libpq_version=pq.version(),
        dependencies={d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
    )


def publish(cfg, pool) -> dict:
    from .security import HASH_CONCURRENCY

    snapshot = runtime_snapshot()
    snapshot["tuning"] = {
        "schema_version": 1,
        "run_id": cfg.run_id,
        "pid": snapshot["pid"],
        "language": "python",
        "workers_total": cfg.workers,
        "connections_per_worker": pool.max_size,
        "pool_budget": 20,
        "cpu_parallelism": None,
        "hash_concurrency": HASH_CONCURRENCY,
        "hash_scope": "worker",
    }
    if snapshot["gil"] != cfg.expected_gil or snapshot["jit"] != cfg.expected_jit:
        raise RuntimeError("post-import GIL/JIT mode does not match configuration")
    if cfg.run_dir:
        path = Path(cfg.run_dir)
        path.mkdir(parents=True, exist_ok=True)
        f = path / f"{snapshot['pid']}.json"
        temporary = f.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot))
        temporary.replace(f)
    return snapshot
