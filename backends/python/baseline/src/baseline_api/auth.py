from datetime import datetime, timezone
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session
from .schemas import Credentials, Login, Token, User
from .security import DUMMY_HASH, decode_token, hash_password, issue_token, verify_password

router = APIRouter(prefix="/api/v1")
bearer = HTTPBearer(auto_error=False)
Session = Annotated[AsyncSession, Depends(session, scope="function")]


async def identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> int:
    if credentials is None:
        raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    try:
        return decode_token(credentials.credentials, datetime.now(timezone.utc))
    except ValueError:
        raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})


Identity = Annotated[int, Depends(identity)]


@router.post("/auth/register", response_model=User, status_code=201)
async def register(body: Credentials, s: Session):
    encoded = await anyio.to_thread.run_sync(hash_password, body.password)
    try:
        row = (
            (
                await s.execute(
                    text(
                        "INSERT INTO users(username,password_hash) VALUES (:name,:hash) RETURNING id,username"
                    ),
                    {"name": body.username, "hash": encoded},
                )
            )
            .mappings()
            .one()
        )
    except IntegrityError:
        raise HTTPException(409, "username already exists")
    return dict(row)


@router.post("/auth/login", response_model=Token)
async def login(body: Login, s: Session, request: Request):
    row = (
        (
            await s.execute(
                text("SELECT id,password_hash FROM users WHERE username=:name"),
                {"name": body.username},
            )
        )
        .mappings()
        .first()
    )
    valid = await anyio.to_thread.run_sync(
        verify_password, body.password, row["password_hash"] if row else DUMMY_HASH
    )
    if not valid or row is None:
        raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    return Token(
        access_token=issue_token(row["id"], datetime.now(timezone.utc)),
        expires_in=request.app.state.settings.jwt_ttl_seconds,
    )


@router.get("/users/me", response_model=User)
async def me(user_id: Identity, s: Session):
    row = (
        (await s.execute(text("SELECT id,username FROM users WHERE id=:id"), {"id": user_id}))
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(401, "invalid credentials")
    return dict(row)
