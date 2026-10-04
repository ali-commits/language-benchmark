import time

from fastapi import Request
from sqlalchemy.ext.asyncio import create_async_engine

from .settings import Settings


def make_engine(cfg: Settings):
    url = cfg.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_async_engine(
        url,
        pool_size=20 // cfg.workers,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=True,
        connect_args={"application_name": "lb-backend", "options": "-c statement_timeout=10000"},
    )


async def session(request: Request):
    async with request.app.state.sessions() as s:
        async with s.begin():
            started = time.monotonic()
            await s.connection()
            request.app.state.telemetry.record(time.monotonic() - started)
            yield s
