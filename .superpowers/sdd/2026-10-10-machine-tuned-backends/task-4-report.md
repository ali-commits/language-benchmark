# Task 4 — Explicit tuned profiles and live attestation

Implementation commit: `ac30256` (`feat(bench): add explicit four-CPU profiles and live runtime attestation`). Main checkout; owned paths only, committed with `git commit --only` under the shared `git-write.lock`. The four staged Rust/.NET permission-fix files were excluded and preserved. No AWS, SSH, RDS, deployment, current campaign, or historical image mutation occurred.

## Implementation and public interface

`runtimes/profiles/tuned4.json` adds exactly nine new cell IDs and image tags ending `-tuned4`. Original registry bytes, dependency versions, labels and default cells remain unchanged. The profile registry records actual SHA-256 fingerprints of the nine original lock files and rejects any changed input. It contains no invented image digest: staging still must inspect actual built images.

`tools.bench.tuning.load_tuned_profiles(root)` returns typed `TunedProfile` objects exposing `base_cell`, `cell`, `resources`, `environment`, and `build_args`. `profile_for_cell(cell, root)` returns the selected validated profile or None for legacy cells. `load_runtime_cell(id)` resolves explicitly selected tuned cells; `load_runtime_cells()` retains the historical list, with `include_profiles=True` adding tuned cells. Profiles are packaged in the wheel. Public controls and environment-reference semantics are documented in `runtimes/profiles/README.md`.

RuntimeCell permits modern async4 only with the explicit frozen tuned Python identity; baseline/threaded and historical worker restrictions remain. NativeCell permits Express4×5 only with an explicit tuned Node/Bun identity. Octane's new identity remains separate from FPM. Unknown/forged profile identities and uncontrolled resources reject selection. Legacy native container budgets remain2CPU/3GiB; explicit tuned native container guards require4CPU/6GiB. Legacy native preflight rejects tuned cells so labels alone cannot bypass generic social attestation.

Social preparation validates target4CPU/6GiB/swap0/nofile250000 and all resolved profile.environment controls before fixture reset/deployment. Engine Target.env remains SecretRef-only: campaign generation must create private environment references containing the safe profile values. Memory controls are Go4GiB soft limit, Node old-space1024MiB per worker, JVM65%/ActiveProcessorCount4 and .NET0x41/processor4. Bun retains existing GC controls/common cap.

Tuned social attestation checks inspected container identity, verified image provenance, current `bench.trial` nonce, resource limits, process nofile and independently observed cgroup-v2 cpu.max/memory.max/memory.swap.max. A credential-free container-local /proc probe identifies actual serving processes and their start times. Receipts must match live serving PID count/set, filename, fresh run nonce, modification time, exact allowlisted schema and expected topology. Native Go/Rust/Java/.NET require observed CPU4, process hash4 and pool20. Python/Node/Bun require4 live workers each hash1/pool5; PHP requires20 live serving workers each pool1, globally coordinated hash4. Python/JS/PHP CPU observations remain null and their actual four-CPU quota is external evidence. Safe dead recycled PHP receipts can be ignored only when all twenty current serving workers have valid receipts; live non-serving PIDs never count toward readiness.

Python continues using existing PID.json snapshots containing tuning; Node/Bun/native/PHP use tuning-PID.json. Other Express readiness files are ignored before JSON parsing. PHP's bounded warm-up sends100 readiness requests outside baseline/evaluated stages, then requires20 live receipts within the finite readiness budget. Historical images without receipts remain accepted under historical cells.

## Test-first evidence

Observed red before production APIs/changes:

- Profile loader/control tests:7 failures, missing bench.tuning module.
- Receipt/resource tests:29 failures/7pass, missing validator APIs (`/tmp/lb-task4-receipts-red.log`).
- Actual cgroup proof parser:1failure/36pass, absent parse_process_probe (`/tmp/lb-task4-cgroup-red.log`).
- Integration edge tests:3failures/37pass; Express readiness file incorrectly treated as a tuning receipt, forged native identity accepted, profile configuration validator absent (`/tmp/lb-task4-integrate-red.log`).
- Social integration:3failures, absent warm_tuned_php/attest_tuned_runtime (`/tmp/lb-task4-social-red.log`).
- Native guard/registry tests:2failures/40pass; tuned budget refused and opt-in registry API absent (`/tmp/lb-task4-native-guards-red.log`).
- Freshness/input tests:3failures/46pass; nonfinite timestamps bypassed freshness and ignored Express files were parsed too early (`/tmp/lb-task4-freshness-red.log`).
- Extra registry field:1failure/45pass; uncontrolled version field silently ignored (`/tmp/lb-task4-registry-fields-red.log`).
- Original lock drift:1failure/46pass; changed framework version fingerprint not yet rejected (`/tmp/lb-task4-lock-red.log`).

Green covers default compatibility, duplicate identities, frozen versions/fingerprints, uncontrolled budgets/env, explicit worker topologies, missing/duplicate/mismatched/stale receipts, PID/count/proc-start provenance, exact hash scopes/CPU/pool arithmetic, nonfinite timestamps, independent cgroup limits, safe receipt allowlists, PHP dead-file handling, real100-request readiness warm-up and finite missing-receipt deadline. The warm-up test uses an actual local HTTP server; Docker transport is mocked only where an actual serving container is an external dependency of unit validation.

## Exact final verification

- `.venv/bin/python -m pytest tests/unit/test_tuned_profiles.py tests/unit/test_tuned_social.py -q`:51passed.
- Focused compatibility command adding test_native_runtime.py, test_php_octane_registry.py, test_runtime.py and test_legacy_records.py:92passed in1.05s.
- `.venv/bin/python -m pytest tests/unit -q`:684passed/13skipped in15.86s. Skips are existing optional DB/SDK/integration coverage, not new attestation tests. Final log `/tmp/lb-task4-unit-final.log`.
- `.venv/bin/ruff check .`:passed; `.venv/bin/ruff format --check .`:153files formatted; `.venv/bin/mypy`:74source files passed.
- `bun run typecheck`:passed. `bun run check`:passed,48 existing informational diagnostics, no errors/fixes; `/tmp/lb-task4-biome.log`.
- `uv build --wheel --out-dir /tmp/lb-task4-wheel`:passed. Zip byte comparison verifies packaged tuned registry and all nine original locks exactly match source bytes.
- Ran the actual PROCESS_PROBE inside the existing pinned local Java image with `docker run --pull=never --rm --cpus4 --memory6g --memory-swap6g --ulimit nofile=250000:250000 --entrypoint sh lb-native-java-tuned:task1 -c <probe>`. Parser verified actual4CPU/6GiB/no-swap cgroup evidence and live namespace process data. No app/database was started by this probe. An initial attempt used a nonexistent Go task1 local tag and Docker refused the image; it was corrected to the existing Java tag with --pull=never.
- `git diff --check`:clean. `git diff --exit-code -- runtimes/*/lock.json uv.lock benchmarks/k6`:clean.
- Implementation commit's pre-commit hooks:all Ruff lint/format, strict Python types, strict JS types, Biome passed. Unrelated native staged work restored and excluded.

## Self-review and handoff

Reviewed identity selection, original defaults/lock bytes, profile packaging, environment-reference binding, fixed resource arithmetic, per-process versus global hash capacity, actual serving command/count matching, safe fresh receipt allowlists, process/container start times, cgroup independence, finite readiness budgets and warm-up placement before baseline/load. Cryptographic/API/SQL/fixture/workload inputs remain unchanged. No known material Task4 finding remains.

Task5 still must build/stage all nine final images and exercise this actual social attestation against live serving processes and the disposable local test database, recording immutable IDs/archive provenance. This task's real Docker probe establishes the kernel/probe interface, not a substitute for that nine-backend validation. No remote or existing campaign action was taken.
