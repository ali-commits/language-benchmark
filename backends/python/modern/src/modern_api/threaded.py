from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI

from .auth_sync import router
from .body_limit import BodyLimit
from .db import make_pool
from .runtime import publish
from .settings import Settings
from .social_sync import router as social_router


def create_app() -> FastAPI:
    cfg = Settings()

    @asynccontextmanager
    async def lifespan(app):
        anyio.to_thread.current_default_thread_limiter().total_tokens = cfg.thread_limit
        app.state.settings = cfg
        app.state.pool = make_pool(cfg, threaded=True)
        try:
            app.state.pool.open()
            app.state.pool.wait()
            app.state.runtime = publish(cfg)
            yield
        finally:
            app.state.pool.close()

    app = FastAPI(title="Python modern threaded social API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.include_router(router)
    app.include_router(social_router)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        with app.state.pool.connection() as c:
            c.execute("SELECT 1")
        return {"status": "ok"}

    return app
