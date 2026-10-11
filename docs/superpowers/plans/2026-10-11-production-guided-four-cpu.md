# Production-guided four-CPU implementation

User authorized one officially researched senior-engineering configuration per stack, no parameter sweep. Specification: `docs/superpowers/specs/2026-10-11-production-guided-four-cpu.md`. Work in main checkout. Keep active AWS suite unchanged.

1. Python/JS owner: preserve legacy defaults; introduce explicit pool maxima, Python server loop/parser controls and isolated uv accelerator lock; Node libuv control; Bun supported cluster-worker native reusePort behavior. Truthful worker/pool receipts, build arguments and labels. Verify lifecycle, cancellation, actual normal entrypoints and API behavior.
2. Native owner: configurable pool min/max retaining defaults20; Java bounded platform-thread hashing with correct permit lifetime/cancellation; Npgsql preparation/enlistment settings; accurate receipts. Preserve hashes, SQL, TLS and all historical version locks.
3. PHP owner: profile-selectable32 workers/OPcache/limits; FPM persistent PDO with transaction/session release safety; RR memory recycle; accurate receipts/labels. Verify reused physical connections and request isolation, no write replay.
4. Engine/profile owner after backend contracts stabilize: add isolated production4 registry, independent per-stack pool validation/attestation, frozen legacy/tuned4 acceptance, image commands and packaging. Update tests and docs explaining sources, choices, exact runtime controls and limits.
5. Build immutable images and run shared correctness/TLS plus actual normal-entrypoint/process/resource/pool/hash proofs for all nine. Review changes independently. Run required type/format/lint/commit hooks. Preserve existing baseline locks and measured artifacts.
6. Prepare fresh AWS campaign source/config/images without deploying to active backend. Verify source/image provenance and disk capacity. Install an audited completion-gated observer after current-suite cleanup; actual supervised new queue runs exactly one production4 configuration per stack with fresh evidence. Report actual preparation versus test execution truthfully.

Do not use historical benchmark output to choose multiple configurations; previous passes are start hints only. No background sweeps. Stop requests remain durable. Backups and idle-shutdown scopes continue to apply to both current and new work.
