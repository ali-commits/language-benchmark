# Machine-appropriate four-vCPU backend campaign

Human authorization: keep the running `four-vcpu-20261009` campaign unchanged, improve all runtime configurations for the same c7a.xlarge, and automatically rerun once the existing campaign completes. Work in the main checkout. This is bounded deployment/configuration work on the existing backends and engine; no API or workload redesign.

## Goal and evidence

Compare each framework using a reasonable senior-engineer configuration that can use the available machine. The previous frozen profiles were designed for two CPUs: Go explicitly uses GOMAXPROCS2, Rust Tokio2, Java ActiveProcessorCount2, .NET processor count2, Node/Bun2 processes; native hashing caps2. Python2 processes each have2 hash slots, giving aggregate4. Saved telemetry confirms Go ramp container CPU48.1% versus Python82.7% at their passing boundaries on a4CPU quota. At comparable ~4,500 users Go hold p99 was7ms versus Python45ms. Existing results remain measured frozen-deployment evidence, not universal language rankings.

## Global constraints

- Do not deploy source/images, change environment variables, rebuild on the backend/generator, interrupt, resume or modify the currently running campaign/checkpoints. Stage new images locally. Handoff only after all9 current jobs complete and actual cleanup/fixture/leases are verified.
- New campaign uses new immutable tuned image tags and runtime-cell identities, fresh queue/state/config/output, frozen language/framework/dependency versions. Preserve every historical image, lock file, trial and report.
- Backend remains default c7a.xlarge4CPU8GiB; container4CPU6GiB/no swap/nofile250000. Total database connection budget20 per backend.
- Canonical fixture50,000users/500,000posts and same following-feed social behavior, real logins/random3–10s waits, Argon2id64MiB/t3/p1/hash32/salt16, SQL/transaction semantics and JWT/auth contracts unchanged.
- Same search policy:300s ramp/0settle/120s hold, p95/p99<=5000ms, separate operation/auth>=90%, upper_probe, gap<=50, majority-on-disagreement, finite existing recovery/search budgets. Previous final passing counts are hints to remeasure, never imported votes.
- Tune CPU scheduling to4; aggregate hash admission4. Python4workers×1hash and5DB connections; Node/Bun4workers×1hash and5connections. Go/Rust/Java/.NET one process, scheduler4, hashes4,pool20. PHPFPM/Octane20request workers×1connection with4 globally coordinated hash slots.
- Preserve historical defaults and legacy validation; new tuned cells explicitly attest their effective profile. Never print DSNs, JWT secrets or complete environment dumps.
- Managed-memory headroom: Go soft limit4GiB; JVM/.NET retain65% heap policy scaled to6GiB. Rust/PHP/Python/JS remain under the common container cap; existing Node/Bun libuv2 per worker and bounded hash queues remain. No caching results, weaker hashes, feature removal, dependencies upgrades, speculative performance tricks or benchmark-result-targeted optimizations.
- Record startup observations of actual scheduler/worker/hash/pool settings using safe configuration-only runtime receipts. Tuned social preflight must verify them, not merely image labels. Historical profiles without receipts remain supported.

## Task 1 — Native CPU/hash controls

Owner: backends/go, backends/rust, backends/java/spring-boot, backends/csharp/aspnet-core only.
Add validated HASH_CONCURRENCY startup setting (default2; tuned4) and Rust CPU_PARALLELISM (default2;tuned4), preserving cancellation and permit lifetimes. Go scheduling and heap/.NET/JVM settings remain native environment overrides in the tuned campaign. Add credential-free effective runtime receipt containing actual CPU parallelism, hash slots and pool20. Tuned builds must keep version/lock provenance. Test defaults, tuned4, malformed/zero/out-of-range values, admission bounds and cancellation; run existing relevant native tests and compilation using pinned containers if host tools differ. Commit owned changes only and report exact checks.

## Task 2 — Python and shared Express worker scaling

Owner: backends/python (modern/entrypoint only, baseline unchanged), backends/javascript/express, backends/node, backends/bun and backend-specific tests only.
Permit modern async4workers with validated per-worker HASH_CONCURRENCY default2/tuned1; avoid mandatory credentials during security module import. Preserve threads16 and pool20//workers. Node/Bun expose WORKERS default2/tuned4, derive pool20/workers and report effective4×5×1. Keep existing worker failure/readiness semantics and queue64. Tuned build arguments/defaults/labels must match actual topology. Add safe worker-specific runtime receipts. Test2/4 configurations, rejection, actual child processes and pool totals; pinned Node/Bun Linux verification required. Do not change shared tools/bench models; Task4 owns them.

## Task 3 — PHP global hash admission

Owner: backends/php/laravel and backends/php/laravel-octane only.
Parameterize shared flock hash slots with validated HASH_CONCURRENCY default2/tuned4; preserve slot release on failure and cross-process mutual exclusion. Keep20worker and1connection topology, request recycling, OPcache/JIT and DB-lifetime semantics. Startup config receipt must expose effective4 hash slots and20×1 topology without credentials. Add meaningful default/tuned4 and occupied-slot/cancellation tests. Keep Composer/dependency bytes and historical tags unchanged. Commit owned changes only.

## Task 4 — Explicit tuned profiles and attestation

Owner: tools/bench/models.py, records/runtime/native_runtime/engine/social as needed, new runtimes profile registry and associated tooling tests/docs. Execute after Tasks1–3 interfaces are available.
Add9 explicit tuned identities/images in an isolated profile registry; original registry/lock bytes and original2CPU3GiB guard remain unchanged. Support modern async4 and Express4×5 only for explicit tuned profiles. Generic social preflight verifies effective receipts against fixed profile budget and per-process/aggregate topology, requires live receipt provenance for tuned images, rejects missing/mismatched/stale proof and avoids relying on declared labels alone. Actual nofile/TLS/dependency/image/nonce/fixture checks continue. Explain public configuration controls and historical versus tuned results. Run focused tests plus full repository lint/type/unit checks. Commit integration separately.

## Task 5 — Cross-backend validation and staging

Owner: separate validation/review subagent after implementation.
Review aggregate hash/pool/CPU arithmetic, cancellation, env parsing, historical compatibility and unchanged cryptographic/API semantics. Build all9 tuned images locally using pinned dependencies, test actual effective settings in live processes and shared contracts against a disposable local test DB, verify4workers/readiness for both Node and Bun. Do not reuse or touch RDS during local validation. Export immutable image IDs/labels/provenance and archives for later deployment. Fix material findings through responsible implementers and review again.

## Task 6 — Completion-gated supervised handoff

Owner: main orchestrator private AWS configs/services, not measured artifacts.
Install only an inert/persistent observer on monitor while old test runs. It watches current full queue without interruption and respects explicit stops. Once old jobs all complete: locally copy/verify completed raw data, stop/disable old handoff+controller, verify actual owned containers/leases absent and fixture restored, deploy locked new engine/images off measured backend, verify image identity and host baselines, run9 distinct10user smokes and requirevalidpass+cleanup before starting new full searchqueue. New persistent queue/handoff/recovery services operate without desktop. Record active metadata atomically and update idle-stop automation so any tuning/build/deploy/preflight/test/recovery prevents shutdown. No further suites beyond this authorized rerun.

## Review focus

Cross-process versus per-process hash count; Python import without database config; Go cancel tests filling exactly2 legacy slots; Rust permit lifetime during spawn_blocking; PHP flock cross-process enforcement; four Bun workers sharing a Linux socket; worker-count×pool total20; Java/.NET non-heap memory; actual receipts versus hardcoded claims; old lock fingerprints; completion/stop races; local backup verified before machine stopping; no current-campaign source/image mutations.

## Execution record

The human explicitly requested implementation and subsequent rerun. Use independent subagents for backend ownership groups and fresh review before deployment; do not ask for repeated permission for this authorized scope. Only new destructive actions or materially changed workload require human input.

## Shared receipt interface

For Tasks1–3, use existing engine-injected RUN_ID and RUN_DIR. Only publish when RUN_DIR is set. Native/JS/PHP write atomically RUN_DIR/tuning-<actual PID>.json; Python adds a `tuning` object to its existing per-PID worker snapshot to avoid interfering with Python file discovery. Payload: `schema_version:1`, `run_id` from RUN_ID, `pid` from actual serving process, `language`, `workers_total`, `connections_per_worker`, `pool_budget:20`, `cpu_parallelism` (observed Go runtime/JVM/.NET/Tokio value where applicable), `hash_concurrency` (effective permit capacity), `hash_scope` (`process`, `worker`, or `global`). No arbitrary environment content. Task4 validates fresh RUN_ID/PID/container provenance, number of workers where applicable, arithmetic and selected profile; labels alone are insufficient. PHP may publish inside application worker initialization and can report globally coordinated slot capacity; actual20-worker count is verified outside PHP through container process evidence.
