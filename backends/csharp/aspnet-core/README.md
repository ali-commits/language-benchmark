# C# / ASP.NET Core

One stable implementation: **.NET / ASP.NET Core 10.0.12**, built with **SDK 10.0.401**, ordinary Release managed code and the default JIT. No NativeAOT, application caches or alternate schema. Npgsql **10.0.3**, Konscious Argon2 **1.3.1** and Microsoft.IdentityModel.JsonWebTokens **8.23.0** are exact pinned stable NuGet dependencies. `packages.lock.json` and the native test lock pin transitive content hashes; locked restore is enabled. `global.json` fixes the SDK and Microsoft Testing Platform runner.

ASP.NET Core minimal endpoints call a service/repository layer with parameterized Npgsql queries, scoped transactions and a single aggregate 20-connection pool. A login lookup releases the pooled connection before password verification. Two expensive Argon2 jobs can run concurrently. Queued jobs observe HTTP request cancellation; running crypto retains its slot until completion, preventing detached work from breaking the concurrency bound.

The canonical Argon2id PHC policy is memory 65536 KiB / time 3 / parallelism 1 / salt 16 / hash 32. Unknown-user login performs equivalent hash work and returns the same 401. JWTs use HS256, string positive int64 subject, integer iat/exp, issuer/audience, expiration/future-iat/optional-nbf guards and audience-array compatibility. The shared protocol preserves strict JSON types/error aggregation, normalized usernames, Unicode code-point content limits, bigint IDs and UTC microsecond keyset cursors. All API endpoints in the shared contract are implemented.

## Build and run

From the repository root:

```sh
docker build -f backends/csharp/aspnet-core/Dockerfile -t lb-csharp-aspnet:10.0.12 .
docker run --rm --cpus=2 --memory=3g --memory-swap=3g \
  --ulimit nofile=250000:250000 -p 8000:8000 \
  -e DATABASE_URL='postgresql://user:password@db:5432/language_benchmark' \
  -e JWT_SECRET='replace-with-at-least-32-byte-secret' lb-csharp-aspnet:10.0.12
```

Both SDK/runtime base images are digest-pinned. The runtime runs as the MCR nonroot application user. Server GC is enabled, processor count 2 and GC heap hard-limit 65% of the container budget leave headroom for runtime/Argon2 allocations. These settings do not change Docker's required two CPUs, 3 GiB/no-swap budget.

Inputs: `DATABASE_URL`, `JWT_SECRET` (at least 32 UTF-8 bytes), `JWT_TTL_SECONDS` (default 3600, range 600–86400), `BIND_IP` (default 0.0.0.0), `PORT` (default 8000), `MODE`, optional `RUN_ID`/`RUN_DIR` orchestration metadata. No migrations, seeds or schema changes run at startup. Real connection checkouts and cumulative checkout milliseconds are exposed at `/health/metrics`; readiness queries the database.

`MODE=aws` requires unambiguous `sslmode=verify-full` and nonempty `sslrootcert` URL parameters. Mount the trusted CA read-only and use its container path. Credentials use percent decoding that preserves literal plus characters. Every session is tagged `application_name=lb-backend` with PostgreSQL statement_timeout 10000 ms. GSS/Kerberos negotiation is disabled for the password-authenticated TLS deployment, avoiding Npgsql 10's optional Kerberos fallback warning without disabling TLS certificate/hostname verification.

## Verification

The Docker build runs native protocol tests; its real-DB regression is explicitly skipped unless `CSHARP_TEST_DATABASE_URL` points to a dedicated `language_benchmark*_test` database. Locally:

```sh
cd backends/csharp/aspnet-core
dotnet restore tests/BenchmarkApi.Tests.csproj --locked-mode
dotnet test --project tests/BenchmarkApi.Tests.csproj
# Optional: CSHARP_TEST_DATABASE_URL='postgresql://.../language_benchmark_csharp_test' before testing.
```

Native tests cover error aggregation, int64/microsecond cursors, PHC policy, strict signed JWT guards, audience arrays, cancelled hashing, credential/TLS parsing and real transaction rollback/session controls. The established shared harness tests all 62 external HTTP cases, including the captured Python validation oracle, Python seed PHCs, JWT interoperability, idempotence, body/chunked limits, committed writes and bounded/tagged lock waits. TLS verification includes both positive CA validation and rejection of a mismatched hostname.

```sh
PYTHONPATH=tools uv run -m bench.languages verify --language csharp \
  --db-port 55645 --api-port 58065 --output artifacts/csharp-contract-tls
```

Local disposable checks use 55644/58064; TLS harness uses 55645/58065. Never point shared tests at the canonical AWS fixture. Local correctness verification establishes API/workflow compatibility, not an AWS capacity measurement.

Version sources: [.NET 10 releases](https://github.com/dotnet/core/blob/main/release-notes/10.0/README.md), [official release JSON](https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/10.0/releases.json), [MCR containers](https://github.com/dotnet/dotnet-docker), [Npgsql](https://www.nuget.org/packages/Npgsql/10.0.3), [Argon2](https://www.nuget.org/packages/Konscious.Security.Cryptography.Argon2/1.3.1), [JWT](https://www.nuget.org/packages/Microsoft.IdentityModel.JsonWebTokens/8.23.0), [Npgsql security](https://www.npgsql.org/doc/security). .NET 11 release candidates are excluded.

Verified locally:15 native tests (including real database/HTTP integration) and62 external HTTP contract cases passed. The same62 cases passed with verified TLS; a wrong hostname was rejected. Explicit application/json or application/*+json is required; absent/text/plain returns the canonical422 model-validation body without mutations.

`HASH_CONCURRENCY` accepts integers 1–4 and defaults to 2. It controls the process-wide Argon2 admission capacity without changing Argon2 parameters or the 20-connection database pool. The Docker build argument of the same name preserves the default and can bake an explicit tuned capacity of 4. Invalid or empty supplied values fail startup.

When `RUN_DIR` is set, startup atomically publishes a credential-free `tuning-<serving PID>.json` receipt using `RUN_ID`, observed runtime CPU parallelism, effective hash slots, and pool capacity. The receipt is readable by the host-side verifier.

Tuned deployments set `DOTNET_PROCESSOR_COUNT=4` under the 6GiB container limit, retaining `DOTNET_GCHeapHardLimitPercent=0x41` (65%). The historical image default remains two processors; receipts observe `Environment.ProcessorCount`.
