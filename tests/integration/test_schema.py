import psycopg
import pytest


def test_schema_constraints_and_indexes(database):
    with psycopg.connect(database, autocommit=True) as c:
        c.execute(
            "INSERT INTO users(username,password_hash) VALUES ('alice','hash'),('bob','hash')"
        )
        c.execute("INSERT INTO follows VALUES (1,2)")
        for statement in [
            "INSERT INTO follows VALUES (1,2)",
            "INSERT INTO follows VALUES (1,1)",
            "INSERT INTO follows VALUES (1,99)",
        ]:
            with pytest.raises(psycopg.IntegrityError):
                c.execute(statement)
        indexes = c.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'").fetchall()
        assert ("posts_author_created_idx",) in indexes
        assert ("comments_post_created_idx",) in indexes
