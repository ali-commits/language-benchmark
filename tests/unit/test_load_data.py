import pytest
from bench.load_data import partition_actors, validate_token_lifetime


def test_prepare_data_rejects_short_token_lifetime():
    with pytest.raises(ValueError):
        validate_token_lifetime(100, 300)
    validate_token_lifetime(600, 300)


def test_shards_use_distinct_actor_partitions():
    actors = list(range(10))
    a = partition_actors(actors, 0, 2)
    b = partition_actors(actors, 1, 2)
    assert set(a).isdisjoint(b) and sorted(a + b) == actors
