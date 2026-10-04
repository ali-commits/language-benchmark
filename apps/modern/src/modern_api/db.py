from fastapi import Request
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from .settings import Settings

def make_engine(cfg: Settings):
    url=cfg.database_url.replace('postgresql://','postgresql+psycopg://',1)
    return create_async_engine(url,pool_size=20//cfg.workers,max_overflow=0,pool_timeout=5,pool_pre_ping=True,connect_args={'application_name':'lb-backend','options':'-c statement_timeout=10000'})

async def session(request: Request):
    async with request.app.state.sessions() as s:
        async with s.begin(): yield s

from sqlalchemy import create_engine

def make_sync_engine(cfg: Settings):
    return create_engine(cfg.database_url.replace('postgresql://','postgresql+psycopg://',1),pool_size=20//cfg.workers,max_overflow=0,pool_timeout=5,pool_pre_ping=True,connect_args={'application_name':'lb-backend','options':'-c statement_timeout=10000'})

def sync_session(request: Request):
    with request.app.state.sessions() as s:
        with s.begin(): yield s
