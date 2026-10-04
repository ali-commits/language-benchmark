from concurrent.futures import ThreadPoolExecutor
import psycopg
from tests_support import actor

def test_feed_contains_followed_and_own_posts_only(api_client):
    a,ah=actor(api_client,'alice'); b,bh=actor(api_client,'bob'); _,ch=actor(api_client,'carol')
    own=api_client.post('/api/v1/posts',json={'content':'own'},headers=ah).json()
    other=api_client.post('/api/v1/posts',json={'content':'followed'},headers=bh).json()
    api_client.post('/api/v1/posts',json={'content':'hidden'},headers=ch)
    assert api_client.put(f'/api/v1/users/{b}/follow',headers=ah).status_code==204
    r=api_client.get('/api/v1/feed',headers=ah)
    assert r.status_code==200,r.text
    assert [p['id'] for p in r.json()['items']]==[other['id'],own['id']]
    assert all(p['like_count']==0 for p in r.json()['items'])

def test_equal_timestamps_and_new_insert_do_not_duplicate_pages(api_client,database):
    _,h=actor(api_client,'alice')
    for i in range(3): assert api_client.post('/api/v1/posts',json={'content':str(i)},headers=h).status_code==201
    with psycopg.connect(database) as c: c.execute("UPDATE posts SET created_at='2026-01-01T00:00:00Z'")
    p=api_client.get('/api/v1/feed?limit=2',headers=h).json()
    assert [x['id'] for x in p['items']]==[3,2]
    api_client.post('/api/v1/posts',json={'content':'new'},headers=h)
    q=api_client.get('/api/v1/feed',params={'cursor':p['next_cursor'],'limit':2},headers=h).json()
    assert [x['id'] for x in q['items']]==[1]
    assert q['next_cursor'] is None

def test_limits_and_bad_cursor_are_422(api_client):
    _,h=actor(api_client,'alice')
    for query in ['limit=101','limit=0','cursor=???']:
        assert api_client.get('/api/v1/feed?'+query,headers=h).status_code==422

def test_post_and_comment_unicode_limits(api_client):
    _,h=actor(api_client,'alice')
    p=api_client.post('/api/v1/posts',json={'content':'🙂'*280},headers=h)
    assert p.status_code==201,p.text
    assert api_client.post('/api/v1/posts',json={'content':'🙂'*281},headers=h).status_code==422
    id=p.json()['id']
    assert api_client.post(f'/api/v1/posts/{id}/comments',json={'content':'🙂'*1000},headers=h).status_code==201
    assert api_client.post(f'/api/v1/posts/{id}/comments',json={'content':' '},headers=h).status_code==422
    comments=api_client.get(f'/api/v1/posts/{id}/comments',headers=h).json()['items']
    assert len(comments)==1

def test_missing_targets_are_404(api_client):
    _,h=actor(api_client,'alice')
    for path in ['/posts/999','/posts/999/comments']:
        assert api_client.get('/api/v1'+path,headers=h).status_code==404
    assert api_client.put('/api/v1/posts/999/like',headers=h).status_code==404
    assert api_client.put('/api/v1/users/999/follow',headers=h).status_code==404

def test_self_follow_is_rejected(api_client):
    id,h=actor(api_client,'alice')
    assert api_client.put(f'/api/v1/users/{id}/follow',headers=h).status_code==422

def test_like_and_follow_are_idempotent(api_client):
    a,h=actor(api_client,'alice'); b,bh=actor(api_client,'bob')
    p=api_client.post('/api/v1/posts',json={'content':'hello'},headers=bh).json()['id']
    with ThreadPoolExecutor(4) as pool:
        replies=list(pool.map(lambda _:api_client.put(f'/api/v1/posts/{p}/like',headers=h),range(8)))
    assert all(r.status_code==204 for r in replies)
    r=api_client.get(f'/api/v1/posts/{p}',headers=h).json()
    assert r['like_count']==1 and r['liked'] is True
    for _ in range(2):
        assert api_client.delete(f'/api/v1/posts/{p}/like',headers=h).status_code==204
        assert api_client.put(f'/api/v1/users/{b}/follow',headers=h).status_code==204
    r=api_client.get(f'/api/v1/posts/{p}',headers=h).json()
    assert r['like_count']==0 and r['liked'] is False

def test_unauthenticated_social_is_401(api_client):
    assert api_client.get('/api/v1/feed').status_code==401
    assert api_client.post('/api/v1/posts',json={'content':'hello'}).status_code==401
