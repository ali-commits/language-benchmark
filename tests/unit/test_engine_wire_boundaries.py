"""Malformed external data must not bypass the engine's evidence guards."""

import pytest

from tools.bench.engine.trial import parse_events


@pytest.mark.parametrize(
    "raw", ["BENCH_EVENT []", 'BENCH_EVENT {"type":"operation_start","operation_id":[]}']
)
def test_malformed_event_records_are_invalid_evidence(raw):
    result = parse_events(raw)
    assert "malformed workload event" in result.reasons


def test_non_object_native_point_is_invalid_evidence():
    result = parse_events(
        '{"type":"Point","metric":"vus","data":[] }\nBENCH_EVENT {"type":"metadata","load_start_epoch_ms":1000}'
    )
    assert "malformed native k6 metric" in result.reasons
