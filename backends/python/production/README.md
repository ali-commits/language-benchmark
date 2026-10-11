# Isolated production server dependencies

This lock retains every existing modern application dependency and adds only
`uvloop==0.22.1` and `httptools==0.7.1`, whose upstream releases support CPython
3.14. Historical `modern/uv.lock` and requirements remain unchanged.

Build with `APP=modern`, `DEPENDENCY_PROFILE=production`, `WORKERS=4`,
`HASH_CONCURRENCY=1`, `CONNECTIONS_PER_WORKER=10`, `POOL_BUDGET=40`.
Select `UVICORN_LOOP=uvloop`, `UVICORN_HTTP=httptools`,
`DB_POOL_MIN_SIZE=2`, `DB_POOL_MAX_SIZE=10` in the production profile.
Absent these environment settings, server defaults remain asyncio/h11 and the
historical pool allocation remains 20 total connections. Runtime receipts
include the actual event-loop implementation and pool allocation.

Sources: [uvloop releases](https://github.com/MagicStack/uvloop/releases),
[httptools releases](https://github.com/MagicStack/httptools/releases),
[Uvicorn settings](https://www.uvicorn.org/settings/).
