"""Typed legacy boundaries preserve historical payload and identity semantics."""

import json

import pytest
from bench.majority import measurement_identity
from bench.records import Campaign, CapacityResult, TrialManifest, _adapter, checked_record


def test_checked_campaign_preserves_object_numeric_types_and_unknown_metadata():
    original = {
        "profile": "fast",
        "success_fraction": 1,
        "authentication_fraction": 1.0,
        "custom_metadata": {"sample": [1, 1.0], "version": "historical"},
    }
    before = json.dumps(original, sort_keys=True)
    identity = measurement_identity(original)
    validated = checked_record(original, Campaign)
    assert validated is original
    assert type(validated["success_fraction"]) is int
    assert type(validated["authentication_fraction"]) is float
    assert json.dumps(validated, sort_keys=True) == before
    assert measurement_identity(validated) == identity


def test_nested_checkpoints_keep_unknown_trial_evidence():
    payload = {
        "go-net-http": {
            "status": "bracket_found",
            "passing_users": 100,
            "failing_users": 200,
            "observations": [{"outcome": "pass", "users": 100, "custom_proof": {"id": 42}}],
            "historical_search_rule": "retain-me",
        }
    }
    validated = checked_record(payload, dict[str, CapacityResult])
    assert validated is payload
    assert validated["go-net-http"]["observations"][0] is payload["go-net-http"]["observations"][0]
    assert json.dumps(validated) == json.dumps(payload)


def test_partial_historical_attestation_remains_a_valid_wire_record():
    manifest = {
        "native_runtime": {
            "container": {"nofile": {"soft": 250000, "hard": 250000, "process_samples": 2}}
        }
    }
    assert checked_record(manifest, TrialManifest) is manifest


def test_cached_schema_still_validates_every_payload():
    _adapter.cache_clear()
    checked_record({"start_users": 100}, Campaign)
    first = _adapter.cache_info()
    with pytest.raises(ValueError):
        checked_record({"start_users": "100"}, Campaign)
    assert _adapter.cache_info().hits == first.hits + 1
