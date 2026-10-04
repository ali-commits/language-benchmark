from datetime import datetime, timedelta, timezone

import pytest


def test_expired_or_tampered_token_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:pass@localhost/test")
    from baseline_api.security import decode_token, issue_token

    now = datetime.now(timezone.utc)
    t = issue_token(42, now)
    assert decode_token(t, now) == 42
    with pytest.raises(ValueError):
        decode_token(t, now + timedelta(days=2))
    with pytest.raises(ValueError):
        decode_token(t + "bad", now)


def test_security_settings_reject_missing_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    from baseline_api.settings import Settings

    with pytest.raises(ValueError):
        Settings()


def test_password_salts_are_independent():
    from baseline_api.security import hash_password, verify_password

    a = hash_password("valid-password")
    b = hash_password("valid-password")
    assert a != b
    assert verify_password("valid-password", a)
    assert not verify_password("incorrect", a)


def test_token_operations_accept_lifespan_settings_without_reading_environment(monkeypatch):
    from baseline_api.security import decode_token, issue_token
    from baseline_api.settings import Settings

    cfg = Settings(
        database_url="postgresql://unused@localhost/language_benchmark",
        jwt_secret="cached-signing-secret-at-least-32-bytes",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    now = datetime.now(timezone.utc)
    token = issue_token(42, now, cfg)
    assert decode_token(token, now, cfg) == 42
