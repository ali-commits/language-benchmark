import os
from pathlib import Path
import psycopg
import pytest

DSN=os.environ.get('TEST_DATABASE_URL','postgresql://postgres:benchmark@127.0.0.1:55432/language_benchmark')
@pytest.fixture
def database():
    with psycopg.connect(DSN,autocommit=True) as c:
        c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
        c.execute(Path('database/schema.sql').read_text())
    return DSN

@pytest.fixture(params=['baseline_api.main','modern_api.main','modern_api.threaded'])
def api_client(request,database,monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv('DATABASE_URL',database)
    monkeypatch.setenv('JWT_SECRET','test-secret-that-has-at-least-32-bytes')
    monkeypatch.setenv('WORKERS','1')
    module=importlib.import_module(request.param)
    with TestClient(module.create_app()) as c: yield c
