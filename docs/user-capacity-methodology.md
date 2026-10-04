# Concurrent-user capacity experiment

This replaces the operation-arrival-rate pilot as the primary experiment requested by the user. The old results remain separate and cannot establish a concurrent-user limit.

## User behavior

One k6 virtual user means one unique database account and one stateful session. Each user performs a real password login, gets a following feed, waits a random 3–10 seconds, likes or unlikes a selected feed post, waits 3–10 seconds, optionally reads comments, writes a reply, waits again, and repeats. A small fraction also creates posts. Authentication tokens stay with that user; no pre-issued tokens or shared-account round robin are used. Failed authentication is retried after a think interval and is recorded.

Default startup ramps from zero to the target over 300 seconds, followed by 120 seconds of settling and a five-minute constant-user window. Ramp/authentication results are reported separately from steady activity. This is a workload-specific concurrent-user result: changing think time, login arrival pattern or response deadline changes the measured capacity. An instantaneous simultaneous-login burst is a separate scenario.

## Search

Start at 10,000 target users, then 20,000, 30,000 and so on until a healthy-generator trial fails. Binary search between the last passing and first failing targets. If the first 10,000-user target fails, bisect 0–10,000. Default stopping resolution is a 100-user interval; the result is a bracket rather than a fabricated exact user limit. Recheck both endpoints five times. A disagreement marks the boundary unstable rather than selecting a winner.

The current canonical fixture has 50,000 accounts. A pass at 50,000 is a lower bound with a fixture ceiling, not a failure or a measured maximum. Testing higher unique user counts requires a larger fixture kept identical across compared backends.

## Response-based failure criterion

Provisional defaults: at least 99% of steady operations must return valid responses within five seconds, and at least 99% of target accounts must authenticate successfully. Authentication failures, network failures, timeouts, unexpected HTTP statuses and invalid bodies are visible. The required virtual-user population must actually be reached. The previous aggregate 200-ms p95 target does not define failure for this scenario; p50/p95/p99 remain diagnostic metrics.

A partial run, missing telemetry, overloaded generator, insufficient accounts or OOM is invalid and cannot move the server's passing/failing bounds. Calibrate the generator with the same user script and target VU count against a fast isolated mock target before starting the real 10,000-user probe. Increase or shard generators when required. Server stalls reduce a closed-model user's request rate, so the report includes timeouts, authenticated/active users and completed journeys, not just requests/s.

Restore and fingerprint-check the canonical database before and after every candidate/repetition. Keep backend hardware, worker topology, dependencies, password policy, database connection budgets and think-time rules fixed across runtime comparisons.

## Collection

k6 metrics carry ramp/settle/steady/drain phase labels. Only steady requests determine the responsiveness verdict. CloudWatch statistics queries align to full minute buckets while retaining the exact measurement interval and event checks. This corrects the short-window collection issue observed in the old pilot without inventing missing data.

CLI:

```sh
PYTHONPATH=tools uv run -m bench.cli user-capacity \
  --config artifacts/aws/user-capacity.json --output artifacts/results/user-capacity
```

Start from `benchmarks/user-capacity.example.json` and supply the already configured host/fixture/secrets paths. The dashboard has dedicated concurrent-user, authenticated-user and timeout panels. Raw metrics and the final passing/failing bracket remain the comparison evidence.
