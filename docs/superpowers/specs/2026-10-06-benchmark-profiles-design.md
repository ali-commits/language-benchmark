# Benchmark profiles and load analysis

User approved this design and authorized direct subagent implementation on 2026-10-06. Work in the main checkout. Do not modify an active AWS campaign or reuse its checkpoints under different semantics.

## Intent
Measure realistic capacity and resource efficiency of identical APIs on 2 vCPUs/3 GiB/no swap, with real login, random 3–10 second waits and canonical fixture restoration. Introduce a faster optional methodology and useful comparisons at fixed populations. Existing results remain historical evidence.

## Profiles and classification
The new default fast profile is ramp_seconds=180, settle_seconds=0, hold_seconds=240. Legacy profile is 300/120/300. Explicit timings remain supported. Seven minutes excludes deployment, fixture work, telemetry finalization and graceful drain.
New example campaigns use success_fraction=0.90. A successful operation returns the expected valid response within timeout_seconds=5. Continue search while operation success meets the configured target. Authentication success is reported separately and its gate is configurable as authentication_fraction, defaulting to success_fraction for backward compatibility (new example explicitly 0.90). Invalid generator, missing telemetry, open-file exhaustion, bad cleanup and configuration evidence must not count as backend capacity. Detect backend open-file errors in collected logs and require retry/correction; no silent reuse of known constrained Rust evidence.
Retain geometric expansion, measured hints, bracket width<=100 and final failing rerun once, no passing repeats. All timing, classification, mode and fixed population inputs participate in resume identity.

## Fixed population
Add experiment_type=capacity (default/backward compatible) or fixed-load with fixed_users=[100] default in its example. Fixed-load probes each cell/population once, with normal bounded infrastructure retries and the identical guarded fixture lifecycle. A valid below-target result is measured failure evidence; proceed to the next population/cell. No capacity search or endpoint confirmation in fixed-load mode.

## Analysis
Preserve raw phase and operation samples. Report configured steady duration as denominator for attempted requests/sec and successful operations/sec, not incidental first/last sample duration; expose partial/invalid evidence. Include mean response latency (including failures), p50/p95/p99/p99.9, success fraction, authentication fraction, timeouts, endpoint metrics and phase-separated resource summaries. Normalize Docker CPU: 100% represents both allocated CPUs. Memory uses Docker sampled working-set reporting with average/peak MiB and limitation stated. Include container/backend and generator/RDS evidence without confusing their budgets.
Provide analysis CLI with an arbitrary finite threshold in [0.90,1.00] and defaults 0.90,0.95,0.99,0.999. Reclassify recorded valid observations at each threshold; label highest observed passing/lowest observed failing bounds, contradictions and unsampled ranges. Do not claim a 90%-guided search finds exact higher-threshold capacity. Accept old campaigns lacking new fields and label methodology correctly. Export JSON and human-readable summary, and expose configurable-threshold/throughput/latency/resources on existing Grafana history/live facilities where appropriate.

## Python reference
Add one Python 3.14.8 standard-build modern FastAPI async cell, JIT off/GIL on, with the existing modern framework/dependency lock and worker/pool budgets. Preserve existing 3.15 experimental cells and evidence. Widen modern package Python floor only where compatible and verify with actual 3.14.8 image/API smoke/contract tests; do not claim package install alone proves compatibility. Future campaign examples use this stable runtime reference. Keep preexisting dependency pins explicit; do not call prerelease dependency pins latest stable.

## Deployment boundaries
Stage and validate locally. Keep existing running native queue intact. Any new AWS runs use a fresh output directory/config and preflight verified images/limits. Do not automatically stop/delete EC2/RDS. Completion reminder stays active.
