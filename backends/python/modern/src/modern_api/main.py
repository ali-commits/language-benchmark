from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI

from .auth import router
from .body_limit import BodyLimit
from .db import make_pool
from .runtime import publish
from .settings import Settings
from .social import router as social_router
from .stats import PoolTelemetry, emit_metrics


def create_app() -> FastAPI:
    cfg = Settings()

    @asynccontextmanager
    async def lifespan(app):
        anyio.to_thread.current_default_thread_limiter().total_tokens = cfg.thread_limit
        app.state.telemetry = PoolTelemetry()
        app.state.settings = cfg
        app.state.pool = make_pool(cfg)
        try:
            await app.state.pool.open()
            await app.state.pool.wait()
            app.state.runtime = publish(cfg)
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(emit_metrics, app, cfg)
                try:
                    yield
                finally:
                    tasks.cancel_scope.cancel()
        finally:
            await app.state.pool.close()

    app = FastAPI(title="Python modern social API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.include_router(router)
    app.include_router(social_router)

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/metrics")
    async def metrics():
        return app.state.telemetry.snapshot()

    @app.get("/health/ready")
    async def ready():
        async with app.state.pool.connection() as c:
            await c.execute("SELECT 1")
        return {"status": "ok"}

    return app
