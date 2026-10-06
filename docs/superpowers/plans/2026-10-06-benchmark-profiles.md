# Benchmark Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Execute in main worktree; user authorized direct execution.

**Goal:** Add seven-minute/legacy profiles, 90% capacity and configurable analysis, fixed-load experiments and Python 3.14 modern reference.
**Architecture:** Extend the guarded existing runner; reuse its restore/retry/image verification instead of adding another lifecycle. Separate metrics/analysis from execution and runtime registry work.
**Tech Stack:** Python/uv, k6, Docker Compose, Grafana/Prometheus, PostgreSQL.
**Spec:** docs/superpowers/specs/2026-10-06-benchmark-profiles-design.md

## Global Constraints
Main checkout only; preserve running AWS campaign and old evidence. 2 vCPUs/3 GiB/no swap; same DB and journeys. Fast timing 180/0/240, legacy 300/120/300. New success target 0.90; deadline 5s. No extra passing repeats; final failing repeat once; bracket <=100. Infrastructure-invalid evidence never defines capacity.

## Review Focus
Zero settle must produce correct phases and k6 exit behavior. Resume must reject changed methodology. Arbitrary thresholds must reject NaN/out-of-range values. Sparse observations must not imply exact capacity. Fixed-load must restore after failures and not invoke search.

### Task 1: Runner profiles and fixed-load
Files: tools/bench/user_capacity.py, capacity.py, native_campaign.py, runner.py (only timing/result metadata), benchmarks/k6/user-journey.js, benchmarks/*.example.json, tests/unit/test_user_capacity*.py and new profile/fixed-load tests.
Interfaces: config experiment_type capacity/fixed-load, profile fast/legacy, fixed_users list[int], authentication_fraction; annotate manifest method/timing and classification. Metrics reader remains owned by Task 2: coordinate signature additions.
- [x] Write/run failing tests for profile defaults/override/zero-settle, finite thresholds, fixed-load execution and checkpoint identity.
- [x] Implement profile normalization and fixed-load branch reusing run_probe lifecycle; preserve legacy explicit configs.
- [x] Make k6 success target configurable and skip zero-duration settling stage; isolate authentication gate.
- [x] Detect backend open-file evidence and mark invalid; add explicit comparable nofile=250000 to native compose with preflight verification, staged only.
- [x] Run scoped tests; report changes and evidence. Do not deploy or commit other workers' files.

### Task 2: Metrics, analysis and dashboard
Files: tools/bench/journeys.py, new analysis.py/resources.py as needed, tools/bench/report.py, cli.py, observability dashboard/exporter files, new tests/unit/test_analysis.py and metric/resource tests.
Interfaces: read_journey_metrics(path) backward compatible, new additive metrics; analysis CLI consumes directories/results/manifests, thresholds [0.90,1.00]. Task 1 provides timing metadata; analysis accepts old evidence.
- [x] Write/run failing synthetic tests for configured-duration throughput, endpoint/phase latency including errors, arbitrary threshold and sparse/contradictory bounds.
- [x] Implement exported JSON/human summary with CPU normalized to allocation, MiB working set, p99.9, login success separately, immutable validity.
- [x] Update history/live dashboard with selectable threshold and throughput/mean latency/resources, without changing AWS live files.
- [x] Run scoped tests and analysis against saved evidence; report artifacts and limitations.

### Task 3: Python stable modern reference and docs
Files: runtimes/python/lock.json, modern/pyproject.toml, runtime compatibility/build code only if needed, docs/methodology/runtime-support/user-capacity-methodology.md, new future campaign examples (coordinate Task1 examples), runtime tests.
Interface: python-modern-3.14.8-standard-jit0-gil1-async locked cell; future comparison reference.
- [x] Write/run failing registry tests for reference version/features/budgets.
- [x] Add cell and ensure package/runtime syntax compatible; preserve old cells.
- [x] Build actual locked Python3.14 image and verify API using established contract harness when available.
- [x] Document distinct fixed-load versus capacity and phase metrics, threshold analysis limitations, seven-minute total overhead and historical comparability.

### Task 4: Integration and independent review
- [x] Run combined unit suite and CLI help/example validation; inspect worker reports.
- [x] Independent reviewer checks spec coverage, checkpoint isolation, classification and resource normalization.
- [x] Fix identified blocking issues through responsible subagents and rerun scoped checks.
- [x] Report completed implementation, verification and whether AWS deployment/run occurred truthfully.
