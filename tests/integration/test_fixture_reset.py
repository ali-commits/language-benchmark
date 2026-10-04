from pathlib import Path
import psycopg
import pytest
from bench.fixture import capture_fixture,verify_fixture
from bench.reset import mark_database,restore_fixture

def test_reset_roundtrip_restores_changed_data_and_sequences(database,tmp_path,monkeypatch):
    monkeypatch.setenv('DB_CLIENT_CONTAINER','lb-test-db')
    mark_database(database,'language_benchmark')
    with psycopg.connect(database) as c:
        c.execute("INSERT INTO users(username,password_hash) VALUES ('alice','hash')")
        c.execute("INSERT INTO posts(author_id,content) VALUES (1,'original')")
    manifest=capture_fixture(database,tmp_path)
    with psycopg.connect(database) as c: c.execute("UPDATE posts SET content='changed'")
    assert verify_fixture(database,manifest)
    restore_fixture(database,tmp_path/'manifest.json','language_benchmark')
    assert not verify_fixture(database,manifest)
    with psycopg.connect(database) as c:
        assert c.execute("INSERT INTO posts(author_id,content) VALUES (1,'after reset') RETURNING id").fetchone()[0]==2

def test_reset_refuses_unmarked_or_wrong_database(database,tmp_path):
    with pytest.raises(ValueError): mark_database(database,'production')
    with pytest.raises(ValueError): restore_fixture(database,tmp_path/'missing.json','production')
