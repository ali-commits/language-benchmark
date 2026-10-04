from datetime import datetime,timezone
from typing import Annotated
import anyio
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.security import HTTPAuthorizationCredentials,HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SyncSession
from .db import sync_session as session
from .schemas import Credentials,Login,User,Token
from .security import hash_password,verify_password,issue_token,decode_token,DUMMY_HASH

router=APIRouter(prefix='/api/v1')
bearer=HTTPBearer(auto_error=False)
Session=Annotated[SyncSession,Depends(session)]

def identity(credentials: Annotated[HTTPAuthorizationCredentials | None,Depends(bearer)]) -> int:
    if credentials is None: raise HTTPException(401,'invalid credentials',headers={'WWW-Authenticate':'Bearer'})
    try: return decode_token(credentials.credentials,datetime.now(timezone.utc))
    except ValueError: raise HTTPException(401,'invalid credentials',headers={'WWW-Authenticate':'Bearer'})

Identity=Annotated[int,Depends(identity)]

@router.post('/auth/register',response_model=User,status_code=201)
def register(body: Credentials,s: Session):
    encoded=hash_password(body.password)
    try:
        row=(s.execute(text('INSERT INTO users(username,password_hash) VALUES (:name,:hash) RETURNING id,username'),{'name':body.username,'hash':encoded})).mappings().one()
    except IntegrityError: raise HTTPException(409,'username already exists')
    return dict(row)

@router.post('/auth/login',response_model=Token)
def login(body: Login,s: Session,request: Request):
    row=(s.execute(text('SELECT id,password_hash FROM users WHERE username=:name'),{'name':body.username})).mappings().first()
    valid=verify_password(body.password,row['password_hash'] if row else DUMMY_HASH)
    if not valid or row is None: raise HTTPException(401,'invalid credentials',headers={'WWW-Authenticate':'Bearer'})
    return Token(access_token=issue_token(row['id'],datetime.now(timezone.utc)),expires_in=request.app.state.settings.jwt_ttl_seconds)

@router.get('/users/me',response_model=User)
def me(user_id: Identity,s: Session):
    row=(s.execute(text('SELECT id,username FROM users WHERE id=:id'),{'id':user_id})).mappings().first()
    if row is None: raise HTTPException(401,'invalid credentials')
    return dict(row)
