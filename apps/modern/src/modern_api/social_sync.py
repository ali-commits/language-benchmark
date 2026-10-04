from typing import Annotated
from fastapi import APIRouter,HTTPException,Query,Response
from sqlalchemy import text
from .auth_sync import Identity,Session
from .schemas import PostInput,CommentInput,Post,Comment,PostPage,CommentPage
from .queries import POST_SELECT,FEED,COMMENTS,decode_cursor,encode_cursor

router=APIRouter(prefix='/api/v1')
Limit=Annotated[int,Query(ge=1,le=100)]

def params(cursor,limit):
    try: stamp,id=decode_cursor(cursor) if cursor is not None else (None,0)
    except ValueError: raise HTTPException(422,'invalid cursor')
    return {'cursor_time':stamp,'cursor_id':id,'limit':limit+1}

def item(row):
    d=dict(row)
    d['author']={'id':d.pop('author_id'),'username':d.pop('username')}
    return d

def page(rows,limit):
    more=len(rows)>limit
    selected=rows[:limit]
    cursor=encode_cursor(selected[-1]['created_at'],selected[-1]['id']) if more else None
    return {'items':[item(r) for r in selected],'next_cursor':cursor}

def exists(s,table,id):
    # table is always an internal literal, never request input.
    if not (s.execute(text(f'SELECT 1 FROM {table} WHERE id=:id'),{'id':id})).first():
        raise HTTPException(404,'not found')

@router.post('/posts',response_model=Post,status_code=201)
def create_post(body: PostInput,user_id: Identity,s: Session):
    id=(s.execute(text('INSERT INTO posts(author_id,content) VALUES (:id,:content) RETURNING id'),{'id':user_id,'content':body.content})).scalar_one()
    row=(s.execute(text(POST_SELECT+' WHERE p.id=:id'),{'id':id,'viewer':user_id})).mappings().one()
    return item(row)

@router.get('/posts/{post_id}',response_model=Post)
def detail(post_id: int,user_id: Identity,s: Session):
    row=(s.execute(text(POST_SELECT+' WHERE p.id=:id'),{'id':post_id,'viewer':user_id})).mappings().first()
    if row is None: raise HTTPException(404,'not found')
    return item(row)

@router.get('/feed',response_model=PostPage)
def feed(user_id: Identity,s: Session,limit: Limit=20,cursor: str | None=None):
    p=params(cursor,limit);p['viewer']=user_id
    rows=(s.execute(text(FEED),p)).mappings().all()
    return page(rows,limit)

@router.put('/users/{target_id}/follow',status_code=204)
def follow(target_id: int,user_id: Identity,s: Session):
    if target_id==user_id: raise HTTPException(422,'cannot follow yourself')
    exists(s,'users',target_id)
    s.execute(text('INSERT INTO follows(follower_id,followed_id) VALUES (:id,:target) ON CONFLICT DO NOTHING'),{'id':user_id,'target':target_id})
    return Response(status_code=204)

@router.delete('/users/{target_id}/follow',status_code=204)
def unfollow(target_id: int,user_id: Identity,s: Session):
    if target_id==user_id: raise HTTPException(422,'cannot follow yourself')
    exists(s,'users',target_id)
    s.execute(text('DELETE FROM follows WHERE follower_id=:id AND followed_id=:target'),{'id':user_id,'target':target_id})
    return Response(status_code=204)

@router.put('/posts/{post_id}/like',status_code=204)
def like(post_id: int,user_id: Identity,s: Session):
    exists(s,'posts',post_id)
    s.execute(text('INSERT INTO likes(post_id,user_id) VALUES (:post,:id) ON CONFLICT DO NOTHING'),{'post':post_id,'id':user_id})
    return Response(status_code=204)

@router.delete('/posts/{post_id}/like',status_code=204)
def unlike(post_id: int,user_id: Identity,s: Session):
    exists(s,'posts',post_id)
    s.execute(text('DELETE FROM likes WHERE post_id=:post AND user_id=:id'),{'post':post_id,'id':user_id})
    return Response(status_code=204)

@router.post('/posts/{post_id}/comments',response_model=Comment,status_code=201)
def create_comment(post_id: int,body: CommentInput,user_id: Identity,s: Session):
    exists(s,'posts',post_id)
    row=(s.execute(text('''WITH inserted AS (INSERT INTO comments(post_id,author_id,content) VALUES (:post,:id,:content) RETURNING *)
     SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM inserted c JOIN users u ON u.id=c.author_id'''),{'post':post_id,'id':user_id,'content':body.content})).mappings().one()
    return item(row)

@router.get('/posts/{post_id}/comments',response_model=CommentPage)
def comments(post_id: int,user_id: Identity,s: Session,limit: Limit=20,cursor: str | None=None):
    exists(s,'posts',post_id)
    p=params(cursor,limit);p['post_id']=post_id
    rows=(s.execute(text(COMMENTS),p)).mappings().all()
    return page(rows,limit)
