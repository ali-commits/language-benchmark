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
