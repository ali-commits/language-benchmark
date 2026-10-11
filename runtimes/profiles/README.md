The `tuned4` profile selects nine new runtime cell IDs and image tags ending in
`-tuned4`. Original `runtimes/*/lock.json` files remain the version/dependency
sources; historical cells retain their defaults and two-CPU, three-GiB guards.
The isolated registry records the real SHA-256 fingerprints of all nine original
lock files and rejects changed version/topology inputs. It adds controls, never
guessed image digests. Build and
inspect each image before supplying its verified immutable digest to a campaign.

Public Python API (module `tools.bench.tuning`; `bench.tuning` also works when
`tools` is on `PYTHONPATH`):

```python
from tools.bench.tuning import load_tuned_profiles

for profile in load_tuned_profiles():
    print(profile.cell.id, profile.cell.image)
    # profile.resources -> Target.resources (4 CPUs, 6 GiB, swap 0, nofile 250000)
    # profile.build_args -> pinned Dockerfile build arguments
    # profile.environment -> explicit safe configuration values
```

`load_runtime_cell("go-net-http-tuned4")` resolves one tuned cell. Calling
`load_runtime_cells()` continues to return historical cells; opt in with
`include_profiles=True` to append the nine tuned cells. `profile_for_cell(cell)`
returns its validated profile or `None` for historical cells. The package wheel
includes this registry alongside the original attestation locks.

Engine `Target.env` values are environment references: put each value from
`profile.environment` into the private campaign environment and reference its
name in `Target.env`. Social preparation validates every tuning reference and
resource setting before fixture reset/deployment. Extra credential references
remain permitted. The tuning controls themselves contain no credentials.

| Backend | Runtime controls | Serving topology / hash admission |
| --- | --- | --- |
| Go | `GOMAXPROCS=4`, `GOMEMLIMIT=4GiB`, `HASH_CONCURRENCY=4` | One process, pool20, process hash4 |
| Rust | `CPU_PARALLELISM=4`, `HASH_CONCURRENCY=4` | One Tokio process, pool20, process hash4 |
| Java | `JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=65 -XX:ActiveProcessorCount=4`, `HASH_CONCURRENCY=4` | One JVM, pool20, process hash4 |
| C# | `DOTNET_PROCESSOR_COUNT=4`, `DOTNET_GCHeapHardLimitPercent=0x41`, `HASH_CONCURRENCY=4` | One process, pool20, process hash4 |
| Python modern async | `APP_VARIANT=modern`, `ADAPTER=async`, `WORKERS=4`, `HASH_CONCURRENCY=1` | Four workers, pool5/hash1 each, threads16 |
| Node Express | `WORKERS=4`, `NODE_OPTIONS=--max-old-space-size=1024` | Four workers, pool5/hash1 each, libuv2, pending hash queue64 |
| Bun Express | `WORKERS=4` | Four workers, pool5/hash1 each, libuv2, pending hash queue64 |
| PHP FPM / Octane | `HASH_CONCURRENCY=4` | Twenty workers, pool1 each, four globally coordinated flock slots |

Bun retains its runtime GC defaults and common six-GiB container cap; no
unsupported per-worker heap knob is introduced. Java/.NET's65% heap policy
leaves native/non-heap headroom. Go's soft heap limit and Node's per-worker
old-space limit leave room for native memory and hashes under the same cap.

Tuned social preflight checks the actual inspected container and its trial
nonce, immutable image identity, nofile, and cgroup-v2 CPU quota/memory/swap.
Safe startup receipts attest observed CPU scheduling for native runtimes,
effective pool/hash settings and each actual serving PID. Native/JS/PHP receipts
are `/tmp/bench-runtime/tuning-<PID>.json`; Python embeds `tuning` in its existing
`<PID>.json` readiness snapshot. `RUN_ID` is the engine's fresh trial nonce;
`RUN_DIR=/tmp/bench-runtime` is injected by the engine. Receipts are allowlisted
configuration objects and never include environment dumps or credentials.

The verifier independently enumerates `/proc`, matches serving commands/counts,
and checks receipt filename/PID, nonce and modification time against container
and process start times. Missing, duplicate, stale, malformed or mismatched
receipts fail preflight. Dead recycled PHP worker files may be ignored only
when twenty current serving workers have valid current receipts. Python/JS/PHP
report `cpu_parallelism=null`; their four-CPU quota is independently checked.
Labels alone cannot satisfy tuned attestation.

PHP preflight issues exactly100 bounded `/health/ready` requests to initialize
lazy workers, then waits within the existing finite readiness budget for all
20 live receipts. Warm-up precedes the ready baseline and evaluated load
stages. Legacy social cells accept historical images without tuning receipts;
the legacy native preflight cannot be used to bypass tuned social attestation.

Argon2id, JWT/API/SQL semantics, fixtures, dependencies, workload scripts and
search policy remain unchanged. Historical and tuned results identify different
runtime configurations; compare them as measured deployments, not universal
language rankings. Staging these profiles does not authorize modifying an
already running campaign.

## Production-guided four-CPU profile

`load_production_profiles()` loads nine isolated `-production4` identities.
`load_runtime_cell("go-net-http-production4")` selects one directly;
`load_runtime_cells(include_production_profiles=True)` appends them explicitly.
`include_profiles=True` retains its historical meaning: append only `tuned4`.
Neither flag changes the default runtime inventory or original locks.

The common container budget remains four CPUs/six GiB/no swap/nofile250000.
Database maxima now reflect independent stack configurations: Python/Node/Bun
four workers ×10 connections (40), four native single-process stacks16, and
PHP FPM/Octane32 workers ×one connection (32). Python pools retain min2 per
worker; native min4 except Java's fixed16. These selected limits are engineering
choices informed by official production guidance, not experimentally proven
universal optima. The full rationale and primary sources are in
[the production specification](../../docs/superpowers/specs/2026-10-11-production-guided-four-cpu.md).

Python adds only uvloop and httptools to its isolated pinned production lock;
all original dependency pins remain identical. The registry fingerprints the
original runtime locks and production requirements/uv/pyproject inputs. Wheels
bundle those files so installed CLI attestation works outside the checkout.
Production Python explicitly runs uvloop/httptools; Node/Bun use four supported
cluster workers with libuv4. Go uses GOMAXPROCS4, GOMEMLIMIT5GiB, GOGC100; Java
uses a dedicated bounded platform hash executor; .NET enables bounded Npgsql
automatic preparation without transaction enlistment. PHP enables safe physical
connection reuse,128MiB memory limits,256MiB OPcache/16MiB interned strings and
Octane's256MiB worker recycle threshold. The registry exposes the exact build
arguments and safe environment controls, including `BENCH_PROFILE=production4`.

Production receipts use schema2, with strict per-stack keys for effective pool
minimums, server protocol/event loop, worker mode, executor, preparation or PHP
reuse/OPcache settings. Schema1 receipts remain unchanged for historical and
tuned profiles. The verifier still independently checks actual live PIDs,
commands, nonce/start times, cgroup quotas and immutable image labels. A claimed
20-connection receipt or forged/missing feature cannot satisfy a production
profile. Original and tuned results stay separate from this new configuration.
