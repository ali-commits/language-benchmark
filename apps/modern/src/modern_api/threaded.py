from contextlib import asynccontextmanager
import anyio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from .settings import Settings
from .db import make_sync_engine as make_engine
from .auth_sync import router
from .social_sync import router as social_router


def create_app() -> FastAPI:
    cfg=Settings()
    @asynccontextmanager
    async def lifespan(app):
        anyio.to_thread.current_default_thread_limiter().total_tokens=cfg.thread_limit
        app.state.settings=cfg
        app.state.engine=make_engine(cfg)
        app.state.sessions=sessionmaker(app.state.engine,expire_on_commit=False)
        try:
            with app.state.engine.connect() as c: c.execute(text('SELECT 1'))
            yield
        finally: app.state.engine.dispose()
    app=FastAPI(title='Python modern social API',version='1.0.0',lifespan=lifespan)
    app.include_router(router)
    app.include_router(social_router)
    @app.get('/health/live')
    async def live(): return {'status':'ok'}
    @app.get('/health/ready')
    def ready():
        with app.state.engine.connect() as c: c.execute(text('SELECT 1'))
        return {'status':'ok'}
    return app
