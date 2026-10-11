"""Native Psycopg pools avoid greenlet bridging in free-threaded application paths."""

import time

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from .settings import Settings

KWARGS = {
    "row_factory": dict_row,
    "application_name": "lb-backend",
    "options": "-c statement_timeout=10000",
}


def make_pool(cfg: Settings, threaded: bool = False):
    cls = ConnectionPool if threaded else AsyncConnectionPool
    return cls(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size or 20 // cfg.workers,
        timeout=5,
        kwargs=KWARGS,
        open=False,
    )


async def session(request: Request):
    started = time.monotonic()
    async with request.app.state.pool.connection() as c:
        request.app.state.telemetry.record(time.monotonic() - started)
        async with c.transaction():
            yield c


def sync_session(request: Request):
    started = time.monotonic()
    with request.app.state.pool.connection() as c:
        request.app.state.telemetry.record(time.monotonic() - started)
        with c.transaction():
            yield c
