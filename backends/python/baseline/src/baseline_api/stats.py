"""Small per-worker counters; collection and file writes stay outside request paths."""

import json
import os
import threading
import time
from pathlib import Path

import anyio


class PoolTelemetry:
    def __init__(self):
        self.lock = threading.Lock()
        self.count = 0
        self.total_ms = 0.0

    def record(self, elapsed: float):
        with self.lock:
            self.count += 1
            self.total_ms += elapsed * 1000

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "pid": os.getpid(),
                "time": time.time(),
                "connection_acquires": self.count,
                "connection_acquire_ms": self.total_ms,
            }


async def emit_metrics(app, cfg):
    while True:
        if cfg.run_dir:
            result = app.state.telemetry.snapshot()
            if hasattr(app.state, "pool"):
                result["native_pool"] = app.state.pool.get_stats()
            else:
                result["pool_status"] = app.state.engine.pool.status()
            p = Path(cfg.run_dir) / (str(os.getpid()) + ".metrics")
            tmp = p.with_suffix(".tmp-metrics")
            tmp.write_text(json.dumps(result))
            tmp.replace(p)
        await anyio.sleep(2)
