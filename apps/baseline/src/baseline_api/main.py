from contextlib import asynccontextmanager
import anyio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from .settings import Settings
from .db import make_engine
from .auth import router


def create_app() -> FastAPI:
    cfg=Settings()
    @asynccontextmanager
    async def lifespan(app):
        anyio.to_thread.current_default_thread_limiter().total_tokens=cfg.thread_limit
        app.state.settings=cfg
        app.state.engine=make_engine(cfg)
        app.state.sessions=async_sessionmaker(app.state.engine,expire_on_commit=False)
        try:
            async with app.state.engine.connect() as c: await c.execute(text('SELECT 1'))
            yield
        finally: await app.state.engine.dispose()
    app=FastAPI(title='Python baseline social API',version='1.0.0',lifespan=lifespan)
    app.include_router(router)
    @app.get('/health/live')
    async def live(): return {'status':'ok'}
    @app.get('/health/ready')
    async def ready():
        async with app.state.engine.connect() as c: await c.execute(text('SELECT 1'))
        return {'status':'ok'}
    return app
