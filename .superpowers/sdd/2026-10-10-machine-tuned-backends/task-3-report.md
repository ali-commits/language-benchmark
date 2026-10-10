# Task 3 — PHP global hash admission

Implemented validated `HASH_CONCURRENCY` (absent/default 2, valid 1–4; tuned 4) in both Protocol classes. Both launch scripts validate before spawning servers. Docker build argument defaults to 2; only new local test tags baked 4. Global flock names/admission and finally-based release remain shared across processes. Argon2id m65536/t3/p1/salt16/hash32 remains unchanged.

Serving receipts are atomic `RUN_DIR/tuning-<actual PID>.json`, require RUN_ID when enabled, and contain only schema_version/run_id/pid/language/workers_total/connections_per_worker/pool_budget/cpu_parallelism/hash_concurrency/hash_scope. PHP cpu_parallelism is null by coordinator agreement; quota/host CPU are external evidence. Workers20 × connections1 = pool20, global hash4. FPM publishes on application initialization; Octane WorkerStarting publishes when Laravel boots in each worker. RoadRunner lazily initializes Laravel as requests arrive: exercising 100 readiness requests produced receipts matching all20 actual serving PIDs.

## Red evidence

- `docker run --rm -v "$PWD/backends/php/laravel:/work:ro" php:8.5.11-cli-bookworm php /work/tests/tuning.php` initially exited255: `Call to undefined method App\Support\Protocol::hashConcurrency()`.
- Adding missing-nonce assertion before its implementation gave exit255: `missing run nonce accepted`.
- Copied the original committed Laravel Protocol into `/tmp/lb-task3-red/app/Support/Protocol.php`, copied new hash tests into the corresponding tests directory, then ran `docker run --rm -v /tmp/lb-task3-red:/work:ro php:8.5.11-cli-bookworm php /work/tests/hash_slots.php`: exit255, `hash did not enter the available global slot`. Original two-slot implementation cannot enter available tuned slot2. This is regression red evidence; expanded concurrency coverage was added after the initial configuration implementation.
- Corrected one faulty error-safety assertion: substring matching treated the invalid value ` 4` as disclosed because the generic valid-range error ends with ` 4`. Test now compares the exact safe fixed message.

## Green checks

Host has no PHP. All PHP checks use pinned local PHP8.5.11; both Dockerfiles retain pinned digests and Octane RoadRunner2025.1.15.

Exact builds (exit0):

```sh
docker build --build-arg HASH_CONCURRENCY=4 -t lb-task3-fpm:local -f backends/php/laravel/Dockerfile . > /tmp/lb-task3-fpm-build.log 2>&1
docker build --build-arg HASH_CONCURRENCY=4 -t lb-task3-octane:local -f backends/php/laravel-octane/Dockerfile . > /tmp/lb-task3-octane-build.log 2>&1
```

Final exact image checks (each exit0):

```sh
docker run --rm --entrypoint php lb-task3-fpm:local /app/tests/run.php
docker run --rm --entrypoint php lb-task3-fpm:local /app/tests/tuning.php
docker run --rm --entrypoint php lb-task3-fpm:local /app/tests/hash_slots.php
docker run --rm --entrypoint php lb-task3-octane:local /app/tests/run.php
docker run --rm --entrypoint php lb-task3-octane:local /app/tests/tuning.php
docker run --rm --entrypoint php lb-task3-octane:local /app/tests/hash_slots.php
```

Outputs: `PHP protocol and configuration tests passed`; `PHP default, tuned capacity, invalid settings and safe receipt passed`; `global two/four-slot admission, fifth waiter, cancellation, failure release and canonical Argon2id policy passed`.

Concurrency tests use actual separate PHP processes and real flock files. Real Argon2 hashing proves all slots0..3 admit when individually freed. A test-only deterministic password_hash barrier proves four concurrent holders, blocked fifth process, and kernel permit release on holder termination. Additional waiter termination and injected hash failure prove cancellation/finally release. No production test hooks.

Exact invalid startup checks (expected exit255 before either server launch):

```sh
docker run --rm -e HASH_CONCURRENCY=secret-value lb-task3-fpm:local
docker run --rm -e HASH_CONCURRENCY=0 lb-task3-octane:local
```

Both print only the fixed `HASH_CONCURRENCY must be an integer from 1 to 4` message; rejected value is absent.

Disposable local network `lb-task3-test`, PostgreSQL18-alpine container `lb-task3-db`, database `octane_test`, app containers `lb-task3-fpm` and `lb-task3-octane`. Local-only database environment values are intentionally omitted from this report. No AWS/SSH/RDS operations occurred. Loaded `database/schema.sql` only into this disposable database.

Existing `persistence.php` executed using the local Octane image and disposable database: exit0; connection reused across logical requests; rollback, cleanup and reconnect checks passed.

Existing `http_persistence.py` executed using local `lb-tools:latest` on the disposable Docker network: exit0; status pass, media_types9, alternating_requests400, physical_connections_before20/after20, connection_cap20, request_isolation true, read_reconnect true, idle_transactions0.

Copied the new serving receipt test into each running app container and ran:

```sh
docker exec lb-task3-fpm php /tmp/serving_receipts.php
docker exec lb-task3-octane php /tmp/serving_receipts.php
```

Both exit0: `20 live serving workers, effective hash capacity and safe nonce receipts verified`. Test compares receipt PID set directly against actual `/proc` FPM/RoadRunner request worker set.

Final rebuilt local image IDs:

- FPM: sha256:bc51d6e925bfbf58d564ab85489f03ff70d0bc0b115e033f0a6e2341eae3ac1f
- Octane: sha256:a9a28c3f8334dd40394b38ad7d78be5ac5fd308739410da0516ebdb34b6f2ba8

## Self-review and preservation

`git diff --check -- backends/php/laravel backends/php/laravel-octane` exits0. `git diff --exit-code` on both Composer manifests/locks, php.ini, fpm.conf, .rr.yaml and Database.php files exits0. Request workers20, recycling10000, OPcache128M/JIT0 and connection lifetime semantics remain byte-identical. Original tags were never overwritten. No engine/native registries edited. Hash capacity errors are fixed messages; receipts serialize an explicit allowlist, no credentials. Builds and source-only tests pass; live initialization/receipt checks pass. Local app/database containers and network removed after validation.

Commit: recorded in parent completion message; this report is included in the same atomic owned-path implementation commit, serialized with git-write.lock.
