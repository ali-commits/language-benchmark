from datetime import datetime, timezone

import pytest
from baseline_api.queries import decode_cursor, encode_cursor


def test_cursor_roundtrip_preserves_utc_and_id():
    d = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert decode_cursor(encode_cursor(d, 42)) == (d, 42)


@pytest.mark.parametrize("value", ["", "?", "eyJ0IjoiMjAyNi0wMS0wMSIsImlkIjotMX0=", "a" * 2048])
def test_cursor_rejects_invalid_payload(value):
    with pytest.raises(ValueError):
        decode_cursor(value)
