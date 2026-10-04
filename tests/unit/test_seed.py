import pytest
from bench.seed import fixture_rows
from bench.models import FixtureSpec

def test_seed_exact_counts_and_no_self_or_duplicate_follows():
    s=FixtureSpec(users=10,posts=30,follows_per_user=3,likes=20,comments=5)
    rows=fixture_rows(s)
    follows=list(rows['follows']); posts=list(rows['posts'])
    assert len(follows)==30 and len(set(follows))==30
    assert all(a!=b for a,b in follows)
    assert len(posts)==30
    assert [p[0] for p in posts]==list(range(1,31))

def test_seed_rejects_impossible_relationship_counts():
    with pytest.raises(ValueError): FixtureSpec(users=2,posts=2,follows_per_user=2)

def test_seed_is_logically_deterministic():
    s=FixtureSpec(users=10,posts=30,follows_per_user=3,likes=20,comments=5)
    assert list(fixture_rows(s)['follows'])==list(fixture_rows(s)['follows'])
