from datetime import datetime
from threading import BoundedSemaphore

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from .settings import HashSettings, Settings

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16)
HASH_CONCURRENCY = HashSettings().hash_concurrency
HASH_LIMIT = BoundedSemaphore(HASH_CONCURRENCY)
# Unknown-user verification does the same expensive work as a wrong password.
DUMMY_HASH = HASHER.hash("dummy-only-not-a-valid-user-password")


def hash_password(password: str) -> str:
    with HASH_LIMIT:
        return HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    with HASH_LIMIT:
        try:
            return HASHER.verify(encoded, password)
        except VerificationError:
            return False


def issue_token(user_id: int, now: datetime, cfg: Settings | None = None) -> str:
    cfg = cfg or Settings()
    t = int(now.timestamp())
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": t,
            "exp": t + cfg.jwt_ttl_seconds,
            "iss": "language-benchmark",
            "aud": "social-api",
        },
        cfg.jwt_secret,
        algorithm="HS256",
    )


def decode_token(token: str, now: datetime, cfg: Settings | None = None) -> int:
    cfg = cfg or Settings()
    try:
        data = jwt.decode(
            token,
            cfg.jwt_secret,
            algorithms=["HS256"],
            issuer="language-benchmark",
            audience="social-api",
            options={"require": ["sub", "iat", "exp"], "verify_exp": False, "verify_iat": False},
        )
        if (
            not isinstance(data["exp"], int)
            or data["exp"] <= now.timestamp()
            or data["iat"] > now.timestamp() + 30
        ):
            raise ValueError("invalid token")
        user_id = int(data["sub"])
        if not 0 < user_id < 2**63:
            raise ValueError("invalid token")
        return user_id
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as e:
        raise ValueError("invalid token") from e
