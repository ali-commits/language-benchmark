# Benchmark methodology

## Primary user experiments

Capacity and fixed-load comparisons share realistic closed-loop journeys: one unique account per k6 VU, real Argon2id login, following feed, likes/comments/posts and random 3–10 second waits. They measure complete language/framework stacks on 2 vCPUs, 3 GiB and no swap, with 20 aggregate database connections. They are not isolated language-speed benchmarks.

New examples use the `fast` profile (180 second ramp, no settling,240 second steady window) and 90% valid responses within 5 seconds. Authentication has a separately recorded configurable target. Seven minutes is scheduled load duration; fixture resets, deployment/readiness, drain and telemetry add wall time. The explicit `legacy` profile remains 300/120/300 with historical response targets preserved. Existing AWS campaigns/checkpoints are not silently switched. Full details and generic commands are in [user-capacity-methodology.md](user-capacity-methodology.md).

Capacity retests hints from prior runtimes, expands geometrically and narrows measured passing/failing counts to width 100. New examples reuse the passing search observation and repeat only the final failing endpoint once. Fixed-load probes declared populations (starting with 100) once per runtime without searching or confirming capacity. Both modes reuse the same guarded lifecycle and bounded infrastructure recovery; invalid attempts cannot establish a capacity bound.

## Controlled Python comparisons

1. **Interpreter:** identical baseline source/dependencies, Python 3.10.22 through 3.15.0rc3, two async processes, matching source build/OS/compiler policy.
2. **Application:** baseline versus modern on the same interpreter. Modern native Psycopg pools/generic response types include architecture/dependency changes and do not isolate interpreter speed.
3. **Stable runtime reference:** modern Python 3.14.8 standard build, JIT off/GIL on, two async processes and the existing modern dependency lock. This adds a reference for future language comparisons; it preserves all 3.15 experimental controls.
4. **JIT:** same modern code and JIT-capable executable, toggled off/on, with identical topology. Enabling JIT does not prove each handler was compiled.
5. **Free threading:** same free-threaded executable, GIL on/off; compare two async processes separately from one process with 16 synchronous worker threads. Async I/O alone does not exploit CPU parallelism.

Combined JIT/free-threading remains excluded until explicitly supported and verified. Per-worker snapshots attest current nonce, release, optimized build flags, imports/dependencies and actual GIL/JIT state. Python 3.15.0rc3 remains prerelease evidence. The 3.14 modern reference retains the explicitly pinned Pydantic 2.14.0b2/Core 2.49.0 beta-era dependency stack; do not label it an all-stable or latest-dependency result. See [runtime-support.md](runtime-support.md).

## Application and fixture controls

All implementations share the HTTP contract, following-plus-own feed, descending `(created_at,id)` pagination, exact schema, username normalization, security policy and transactional behavior. IDs/timestamp microseconds interoperate. No caches, denormalized counters, synthetic CPU loops or weaker passwords favor a runtime. Idiomatic framework implementations can use different drivers/concurrency models; record those choices.

Argon2id uses 64 MiB, time 3, parallelism 1 and unique salts. Timed journey logins verify the real hashes; unknown users do equivalent dummy-hash work. JWT issuer/audience/signature/expiry validation remains active. Legacy arrival-rate scenarios prepared tokens before timing for non-login operations; user journeys authenticate within their ramp.

The captured canonical dump fixes otherwise random hash salts, logical seed/time origin and exact users/posts/relationships/comments. Restore before and after every probe, then verify database identity, dump checksum, counts and ordered fingerprints. A dedicated database marker/allowlist and backend connection tags guard ordinary databases. Restoration resets logical state, not operating-system/storage caches.

## Metrics and comparison limits

Keep all raw phase/endpoint observations, including failed responses and timeouts. Steady throughput divides by configured steady duration. Report response/authentication fractions, mean latency including failures, percentiles through p99.9 and endpoint diagnostics. Backend CPU is normalized to its 2-CPU allocation; generator/RDS CPU uses separate budgets. Average/peak working-set memory is sampled Docker MiB, not exact heap accounting.

Threshold analysis reclassifies valid recorded samples at arbitrary finite thresholds 90–100%. A search guided by 90% success cannot establish an exact 99%-success capacity without additional probes. Show observed pass/fail bounds, contradictions and unsampled intervals; fixed-load data does not become a capacity curve by interpolation. Old artifacts retain original timing/targets, inferred fields are labeled and unavailable new metrics remain unavailable. Do not pool different campaign identities or methodology changes.

A generator must sustain the configured users with CPU/core, memory, network, socket and scheduling headroom; increase/shard it when calibration requires. Both application overload and generator exhaustion can reduce closed-loop request rate, so throughput alone is insufficient. Preserve active/authenticated counts, completed journeys, failures and infrastructure evidence. Backend open-file exhaustion, partial evidence or failed restoration makes qualification invalid rather than a language capacity result.

RDS is separate and initially larger than the backend. Record CPU, connections, waits, I/O, memory, cache state and disruptive events. Database saturation limits inference about language scaling. Local checks beside build processes establish correctness/workflow, not controlled AWS rankings.

## Historical arrival-rate pilot

The original pilot used open arrival rates (each arrival was one operation),120 second read-only warmup and 300 second measurement, with randomized repetitions. Mixed operations used 60/15/10/5/5/5 percentages and unique unliked actor/post pairs to avoid idempotent no-op write storms.

Its qualification rule was at least 99.9% success, p95<=200 ms, p99<=500 ms and zero dropped arrivals (login p95<=500 ms separately). These historical criteria/results remain distinct from the current closed-loop user experiment. They cannot be translated into concurrent-user capacity by renaming operations/sec.
