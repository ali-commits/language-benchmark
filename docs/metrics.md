# Metrics and reports

`bench report QUEUE --state-dir STATE --output DIRECTORY` reads retained evidence and writes `report.html` and `report.json`. Tables separate trial validity, cleanup, objective decisions, bounds and observed-only reuse. Selected windows and each stage are reduced independently; inline SVG charts need no external CDN. Missing evidence is shown as unavailable, never zero. Invalid trials retain their observed metrics without becoming passing evidence.

Latency events use completion timestamps in half-open stage windows. Mean is arithmetic over events; p50/p95/p99/p99.9 linearly interpolate sorted samples. High quantiles from sparse samples are descriptive. Failure-inclusive latency is declared explicitly. Rates aggregate numerators/denominators; operation success and distinct-account authentication use separate populations. Attempted/successful operation counts divided by selected duration give throughput, not requested users.

Temporal interval samples are clipped to evaluation windows; means weight interval duration. Gauges hold only to the next observation or declared maximum gap. Required sample count and time coverage enforce evidence validity. CPU uses host `/proc/stat` `1 - delta(idle+iowait)/delta(all ticks)`, normalized over all host CPUs; container CPU uses delta CPU nanoseconds divided by elapsed nanoseconds and configured CPU quota. Host memory is MemTotal minus MemAvailable; container memory is cgroup-accounted usage, not Docker working set or RSS.

Collectors retain scope, provider, interval, formula and raw points. Database RDS CloudWatch has independent 60-second bucket provenance and does not substitute host metrics. Generator saturation, missing occupancy, malformed/nonfinite samples, exit/OOM and required telemetry loss invalidate evidence independently of performance criteria.

Historical October results use their original reductions: CPU means are unweighted sampled values and container memory is Docker working set. Fixed timing 180/0/240 seconds differs from stress 300/0/120. Do not recompute historical decisions with the new engine formulas. See [measured results](results/2026-10/README.md).
