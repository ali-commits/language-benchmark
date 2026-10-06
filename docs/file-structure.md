# Repository structure

```text
backends/<language>/          Language application code, variants, dependencies, Dockerfile
runtimes/<language>/          Compiler/interpreter builds, immutable inputs, runtime matrices
contracts/                   Shared HTTP API behavior
database/                   Shared PostgreSQL schema and migrations
benchmarks/
  k6/                        Shared workload scripts and metrics
  campaign.example.json      Controlled experiment configuration
  smoke.json                 Short, explicitly local functional trial
tools/bench/                Orchestrator, fixtures, reset, telemetry, reports (Python tooling)
tests/
  contract/                  Shared API correctness tests
  integration/               Database/fixture integration tests
  unit/                      Tooling and Python-specific unit tests
  smoke/                     Compose and whole-workflow verification
docker/                     Shared tooling container and infrastructure assets
docs/                       Methodology, AWS runbook, support evidence, design/plan
artifacts/                  Ignored private fixtures, credentials, build logs and trial results
```

Directory names under `backends` and `runtimes` identify the measured language. The implementation language of `tools/bench` does not define the measured backend. New languages reuse data, API semantics, hardware budgets and k6 workloads; runtime-specific worker/build/preflight logic is added only when that language is implemented.

Additional backend directories are java/spring-boot, php/laravel, go, rust, node, bun and csharp/aspnet-core. Shared Express source lives in backends/javascript/express; node/bun each own their runtime Dockerfile. New language runtime locks contain exactly one staging cell each. compose.languages.yaml and bench.languages provide local staging and shared verification without changing the active Python runtime/campaign.

Current Python runtime cell IDs begin `python-`; app image names begin `lb-python-baseline` or `lb-python-modern`. This avoids collisions when another language adds its own baseline.

C# is registered as `csharp-aspnet-core` in `runtimes/csharp/lock.json`, using one ASP.NET Core process and a 20-connection Npgsql pool. The generic native preflight enforces the same six image labels, read-only RDS CA, 250,000-file descriptor limit, 2 vCPUs, 3 GiB and no swap. Registration does not enroll a new runtime in a running campaign: the image must pass local HTTP/TLS verification and then be appended through an explicitly approved queue-extension audit.
