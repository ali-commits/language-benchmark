import pytest
from bench.runner import database_campaign_lock


def test_two_orchestrators_cannot_share_database(database):
    with database_campaign_lock(database):
        with pytest.raises(RuntimeError):
            with database_campaign_lock(database):
                pass
