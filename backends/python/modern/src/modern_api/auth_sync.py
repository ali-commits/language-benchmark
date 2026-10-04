from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import Connection as SyncSession
from psycopg.errors import UniqueViolation as IntegrityError

from .db import sync_session as session
from .schemas import Credentials, Login, Token, User
from .security import DUMMY_HASH, decode_token, hash_password, issue_token, verify_password

router = APIRouter(prefix="/api/v1")
bearer = HTTPBearer(auto_error=False)
Session = Annotated[SyncSession, Depends(session, scope="function")]


def identity(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> int:
    if credentials is None:
        raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    try:
        return decode_token(credentials.credentials, datetime.now(timezone.utc))
    except ValueError:
        raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})


Identity = Annotated[int, Depends(identity)]


@router.post("/auth/register", response_model=User, status_code=201)
def register(body: Credentials, s: Session):
    encoded = hash_password(body.password)
    try:
        row = s.execute(
            "INSERT INTO users(username,password_hash) VALUES (%(name)s,%(hash)s) RETURNING id,username",
            {"name": body.username, "hash": encoded},
        ).fetchone()
    except IntegrityError:
        raise HTTPException(409, "username already exists")
    return dict(row)


@router.post("/auth/login", response_model=Token)
def login(body: Login, s: Session, request: Request):
    row = s.execute(
        "SELECT id,password_hash FROM users WHERE username=%(name)s", {"name": body.username}
    ).fetchone()
    valid = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
    if not valid or row is None:
        raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    return Token(
        access_token=issue_token(row["id"], datetime.now(timezone.utc)),
        expires_in=request.app.state.settings.jwt_ttl_seconds,
    )


@router.get("/users/me", response_model=User)
def me(user_id: Identity, s: Session):
    row = s.execute("SELECT id,username FROM users WHERE id=%(id)s", {"id": user_id}).fetchone()
    if row is None:
        raise HTTPException(401, "invalid credentials")
    return dict(row)
