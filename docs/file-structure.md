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

Current Python runtime cell IDs begin `python-`; app image names begin `lb-python-baseline` or `lb-python-modern`. This avoids collisions when another language adds its own baseline.
