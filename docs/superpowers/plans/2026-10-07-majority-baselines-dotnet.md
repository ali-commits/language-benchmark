# Majority boundary decisions, clean baselines and C# extension

User authorized direct autonomous implementation and execution on2026-10-07.

## Required behavior
- Retain Python's completed2375/2437 boundary; its two failing2437 runs agree. Do not rerun Python.
- Resume Java's2000-user split (one fail/one pass) with exactly one third valid run; majority decides. If majority passes, continue search above2000 until a new<=100 bracket is found.
- Final endpoint first two agreeing runs settle decision; only disagreement needs third. Invalid infrastructure trials never vote and use bounded retries. Preserve every raw outcome and a separate vote ledger, with no pooled or cherry-picked metrics.
- Preserve seven-minute300/0/120 load schedule,90% operation/auth targets,5s deadline, random3-10s waits,2vCPU/3GiB/no-swap/pool20/nofile250000 and canonical fixture resets.
- Apply explicit audited decision-only handoff: all prior measurement identity keys, resource/image/registry checks remain strict. Existing incomplete/unstable searches reopen as needed; completed stable boundaries remain.
- Before each new trial, verify backend test jobs/containers absent, three consecutive low CPU samples (<=15%) and >=1024MiB available after stop/reset. After new API readiness, require three CPU samples<=20% and >=512MiB available, allowing only its exact container/process tree plus host telemetry. Five-second intervals,120s timeout; bounded invalid recovery; no cache purge/reboot/unrelated process termination. Record baseline evidence.
- Signal requests during cleanup are deferred until backend/load stop and verified fixture restoration finish; return interrupted/reset_verified rather than abandoning cleanup.
- Add C# latest stable .NET/ASP.NET Core10.0.12, ordinary Release minimal API/Npgsql20/Argon2hashslots2, exact existing API/schema/security/validation behavior. Verify locked SDK/dependencies/base digests,62 shared HTTP contracts, TLS positive+hostname negative and native resource/image labels before AWS enrollment.
- Append csharp-aspnet-core last only after proof; queue-extension receipt permits strict original-prefix preservation and new-cell hint/image additions only. Reordering/removing/replacing old cells or changing their identities is forbidden. The installed supervisor must run the complete approved queue without desktop dependence.

## Ownership / checks
1. go_rust_stacks: majority ledger/search continuation, strict handoff/queue-extension receipt, baseline gate, cleanup signal protection; focused regression and full unit suite.
2. java_php_stacks: backends/csharp/aspnet-core and runtimes/csharp; build/package locks, native/security/HTTP/TLS correctness, verified image proof.
3. express_stacks: C# generic native-model/registry/CLI integration and independent numerical/operational checks.
4. final_review: independent C# and final policy/cleanup/baseline/extension review; responsible owners fix blockers.
5. Main: preserve current artifacts, verify paused clean state and fixture, create audited private handoffs, preload verified images, deploy tested source/services, resume Java, append verified C#, verify live baseline/load and supervise unattended. Never launch unrelated campaigns or mutate/delete historical evidence.

## Existing pause evidence
Direct controller stop received SIGINT during its normal cleanup and escaped because finally handled Exception rather than BaseException. API/k6containers are gone; an explicit canonical restore subsequently passed fingerprints. Interrupted PHP400 evidence is preserved and excluded from voting unless independently qualified; no manufactured successful cleanup claims.
