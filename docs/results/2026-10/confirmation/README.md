# Confirmation campaign: October 8, 2026

This completed campaign used the reusable engine with nine backend configurations.
The API VM had two vCPUs; the application container was limited to two CPUs and
3 GiB RAM, with no swap. PostgreSQL and k6 ran on separate machines.

- Fixed load: 100 and 1,000 users; 180-second ramp and 240-second hold.
- Capacity search: 300-second ramp and 120-second hold; final bracket width at most 50 users.
- Acceptance: operation success and cumulative distinct-account authentication coverage
  at least 90%, plus the selected hold latency percentile at most 5,000 ms.
- Real login and random 3-10-second waits; following feed, likes, comments and posts.
- Canonical 50,000-user / 500,000-post fixture restored before and after trials.

| Configuration | p99 passing | p99 failing | p95 passing | p95 failing |
|---|---:|---:|---:|---:|
| Rust / Axum | 3296 | 3311 | 3296 | 3311 |
| Go / net-http | 2995 | 3020 | 2995 | 3020 |
| Bun / Express | 2630 | 2654 | 2630 | 2654 |
| Node / Express | 2453 | 2472 | 2453 | 2472 |
| Python 3.14 / FastAPI | 2226 | 2263 | 2300 | 2337 |
| Java / Spring Boot | 2094 | 2126 | 2094 | 2126 |
| PHP / Laravel Octane | 1632 | 1668 | 1632 | 1668 |
| C# / ASP.NET Core | 1166 | 1216 | 1166 | 1216 |
| PHP / Laravel FPM | 262 | 300 | 300 | 337 |

There were 18 fixed-load trials and 113 physical stress attempts. One stress attempt
was interrupted by monitor disk exhaustion and excluded as invalid; cleanup was
recovered and the probe retried. The other 130 trials were valid. Final cleanup and
fixture restoration were verified. The same raw trial could be evaluated for both
latency objectives without another execution.

These are observed bounds for the tested implementations and workload. They are not
universal language limits or confidence intervals. Login-ramp pressure is material.
The current Python image contains the beta dependency Pydantic 2.14.0b2. Monitoring
overhead has not been isolated, fixed-load points have one observation each, and
binary, hybrid and upper-probe searches were used across different configurations.
See the report for all qualifications and historical records.

- [Complete 80-page report](language-benchmark-complete-report-2026-10-08.pdf), including
  an attached ZIP with detailed CSV and sanitized data exports.
- [Compact machine-readable summary](summary.json), including all 18 fixed-load
  observations, final bounds, units, timing and the source export checksum.
- [Earlier campaigns](../README.md), with different timing and acceptance policies.

The report foregrounds sustained-phase **whole-VM** CPU and memory. Memory is
`MemTotal - MemAvailable`, not application RSS. Container and RDS metrics are
reported separately. Original private execution configurations, credentials, SSH
keys and account fixtures are excluded from this repository.
