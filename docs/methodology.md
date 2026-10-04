# Benchmark methodology

## Controlled comparisons

1. **Interpreter:** identical baseline code and dependencies, Python 3.10.22 through 3.15.0rc3, two async worker processes. Source image flags/OS/compiler policy match; each worker records its actual build flags and dependency inventory.
2. **Application:** baseline versus modern on the same Python 3.15 release. Modern uses native Psycopg pools and generic response types; the result includes stack/architecture changes and is not a pure interpreter speedup.
3. **JIT:** identical modern application and JIT-capable executable, toggled off/on, same process topology.
4. **Free threading:** identical free-threaded executable with GIL on/off. Compare two async processes separately from one process with a 16-thread synchronous request pool. Async I/O alone does not exploit Python CPU parallelism across cores.

Combined JIT/free-threading is excluded until explicitly supported and verified. Every worker must emit a unique, current run ID after application imports; enabled JIT does not establish that particular API handlers compiled. Prerelease results remain prerelease results.

## Application and data controls

Both applications implement the same HTTP contract, following-feed query shape, cursor ordering, authentication policy, schema and transactions. The modern path uses `%()` parameter bindings and native PostgreSQL transactions; baseline uses SQLAlchemy named bindings. Pagination defaults to 20, caps at 100, and includes followed authors plus own posts. No caching, synthetic CPU loops or fan-out pipeline is added to favor a runtime.

Password hashing is Argon2id, 64 MiB, time cost 3, parallelism 1, unique salts; up to two simultaneous password hashes per backend process. Timed login always verifies the real hash. Tokens for other operations are obtained before timing; issuer, audience, signature and expiration are still verified on every request. Unknown usernames do equivalent dummy-hash work.

Every full fixture has exact counts and a fixed logical seed/time origin. Hash salts are intentionally random; a captured canonical dump fixes their bytes thereafter. Restore checks database identity, dump checksum, all table counts and ordered logical fingerprints. The marker/allowlist protects ordinary databases; the tooling is for a dedicated benchmark DB only.

## Trial controls

Restore before and after every rate/cell/repetition. Use a fixed 120-second read-only warm-up and 300-second measurement, with five randomized blocked repetitions. Token preparation precedes warm-up and does not mutate the data. Separate scenarios measure feed, comments, writes, likes, login, and user journeys; mixed operations use 60/15/10/5/5/5 percentages.

Each arrival means one operation, with HTTP requests tracked separately. Like PUTs use unique known-unliked actor/post pairs; a VU may DELETE only a pair it successfully liked earlier. This prevents an idempotent no-op storm masquerading as write throughput. Expected and observed relationship state can be audited against the fixture and raw operation metrics. First-page and later-page reads, hot and cold targets, and text lengths are covered.

Dropped arrivals, timeouts and failed checks remain failures. A primary mixed rate qualifies only at >=99.9% success, p95 <=200 ms, p99 <=500 ms and zero dropped iterations; login's separate p95 target is 500 ms. Overload results are retained. Raw points preserve per-endpoint distributions; percentile averages are not called pooled percentiles. Reports show individual trials and throughput medians/ranges without inventing confidence from five repetitions.

## Bottlenecks and limitations

The generator must sustain the offered rate with CPU/network/memory headroom. A c7i.4xlarge has eight times the backend's vCPUs, but calibration and per-core telemetry decide whether it is enough. Capacity tests use an open arrival-rate model; user journeys with think time are a separate closed model. Watch scheduling, sockets, network limits and dropped iterations rather than inferring capacity from VU count.

RDS is intentionally separate and initially larger than the backend. CPU, connections, I/O, cache hits, waits and maintenance events must accompany results. A saturated database limits inference about Python scaling. Restoration resets logical state, not OS/storage caches; primary results are warmed measurements with randomized ordering, not guaranteed cold-cache comparisons.

Local smoke tests run beside build/test processes and may flag generator CPU/swap use. They prove the workflow, not controlled AWS performance. Any missing telemetry or failed cleanup makes qualification unknown. Remote setup and actual AWS calibration must be verified in the target account/region.
