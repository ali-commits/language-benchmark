"""The modern stable reference retains the original API's strict clock-hour range."""

import base64
import json
from datetime import datetime, timezone

import pytest
from modern_api.queries import decode_cursor


def cursor(stamp, item_id=9223372036854775806):
    return base64.urlsafe_b64encode(json.dumps([stamp, item_id]).encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "stamp", ["2026-01-01T24:00:00Z", "2026-01-01 24:00:00+00:00", "20260101T240000Z"]
)
def test_end_of_day_hour_is_rejected_even_on_python314(stamp):
    with pytest.raises(ValueError, match="invalid cursor"):
        decode_cursor(cursor(stamp))


@pytest.mark.parametrize(
    "stamp, expected",
    [
        (
            "2026-10-06T23:59:59.123456+00:00",
            datetime(2026, 10, 6, 23, 59, 59, 123456, tzinfo=timezone.utc),
        ),
        (
            "2026-10-06T10:20:30.000001+05:30",
            datetime(2026, 10, 6, 4, 50, 30, 1, tzinfo=timezone.utc),
        ),
        ("0001-01-01T00:00:00Z", datetime(1, 1, 1, tzinfo=timezone.utc)),
        ("9999-12-31T23:59:59Z", datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)),
    ],
)
def test_valid_microseconds_offsets_and_year_bounds_are_unchanged(stamp, expected):
    assert decode_cursor(cursor(stamp)) == (expected, 9223372036854775806)
