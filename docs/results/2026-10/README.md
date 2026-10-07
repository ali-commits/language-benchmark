# Measured October 2026 results

These are sanitized reductions of retained measurements, not measurements of the reusable engine. [Fixed-load CSV](fixed-load.csv) contains all 27 trials across nine runtime configurations at 100/500/1000 users. [Stress CSV](stress.csv) contains nine final bounds from 100 trials. [Provenance](provenance.json) records source hashes. The inventory has 234 trials; its older validation subsection predates the six .NET/Octane additions.

Fixed timing is ramp/settle/hold 180/0/240 seconds; stress timing is 300/0/120. Different timing and resource budgets prevent treating these campaigns as interchangeable. Authentication is a distinct-account fraction separate from operation success. Historical CPU means are unweighted observed-sample means; the new engine uses duration-weighted interval means. Historical container CPU is normalized to two allocated CPUs; memory is sampled Docker working set, not RSS or the new cgroup usage formula. Sparse resource quantiles are descriptive. Historical file-descriptor exhaustion invalidates only the explicitly flagged trials, not every runtime or campaign.

Passing/failing user counts are observed brackets, not a universal capacity claim. Go at 3250 users retains fail/pass/pass majority votes. Raw configuration, credentials, private host addresses, remote paths and logs are not distributed. Both original final PDFs passed a strict private IPv4, credential and personal-path extracted-text scan. Public copies are byte-identical to the immutable originals; hashes are recorded in provenance.

| Runtime | passing users | failing users | status |
|---|---:|---:|---|
| python-modern-3.14.8-standard-jit0-gil1-async | 2375 | 2406 | bracket_found |
| java-spring-boot | 2062 | 2093 | bracket_found |
| php-laravel | 300 | 350 | bracket_found |
| go-net-http | 3250 | 3281 | bracket_found |
| rust-axum | 3656 | 3687 | bracket_found |
| node-express | 2843 | 2875 | bracket_found |
| bun-express | 2937 | 2968 | bracket_found |
| csharp-aspnet-core | 1281 | 1312 | bracket_found |
| php-laravel-octane | 1718 | 1750 | bracket_found |

| Runtime | users | mean ms | p99 ms | attempted ops/s | successful ops/s | outcome |
|---|---:|---:|---:|---:|---:|---|
| bun-express | 100 | 4.432501273560876 | 11.0 | 16.358333333333334 | 16.358333333333334 | pass |
| bun-express | 500 | 3.7088413856435896 | 9.0 | 81.67083333333333 | 81.67083333333333 | pass |
| bun-express | 1000 | 3.561770613510971 | 9.0 | 163.87916666666666 | 163.875 | pass |
| csharp-aspnet-core | 100 | 3.983481575603558 | 9.0 | 16.395833333333332 | 16.395833333333332 | pass |
| csharp-aspnet-core | 500 | 3.6224182773216382 | 9.0 | 81.70416666666667 | 81.70416666666667 | pass |
| csharp-aspnet-core | 1000 | 4505.57347424284 | 5002.0 | 91.075 | 9.066666666666666 | fail |
| go-net-http | 100 | 3.082292993630573 | 8.0 | 16.354166666666668 | 16.354166666666668 | pass |
| go-net-http | 500 | 2.5266033496732025 | 5.0 | 81.6 | 81.6 | pass |
| go-net-http | 1000 | 2.419784804008852 | 5.0 | 163.80416666666667 | 163.80416666666667 | pass |
| java-spring-boot | 100 | 3.4629913221031137 | 8.0 | 16.325 | 16.325 | pass |
| java-spring-boot | 500 | 2.902917582136835 | 7.0 | 81.54583333333333 | 81.54583333333333 | pass |
| java-spring-boot | 1000 | 2.9283043821053436 | 8.0 | 163.82916666666668 | 163.82916666666668 | pass |
| node-express | 100 | 4.710251844314424 | 11.0 | 16.379166666666666 | 16.379166666666666 | pass |
| node-express | 500 | 4.000765970484604 | 10.0 | 81.59583333333333 | 81.59583333333333 | pass |
| node-express | 1000 | 3.8199745547073793 | 9.0 | 163.75 | 163.74166666666667 | pass |
| php-laravel | 100 | 55.53015139851168 | 155.03999999999996 | 16.2375 | 16.2375 | pass |
| php-laravel | 500 | 5000.655748727308 | 5002.0 | 43.37916666666667 | 0.0 | fail |
| php-laravel | 1000 | 5000.650885869304 | 5002.0 | 86.77916666666667 | 0.0 | fail |
| php-laravel-octane | 100 | 6.441687979539642 | 12.0 | 16.291666666666668 | 16.291666666666668 | pass |
| php-laravel-octane | 500 | 6.175468136129394 | 12.0 | 81.6625 | 81.6625 | pass |
| php-laravel-octane | 1000 | 6.472365774802067 | 13.0 | 163.67083333333332 | 163.67083333333332 | pass |
| python-modern-3.14.8-standard-jit0-gil1-async | 100 | 7.024099441907661 | 44.0 | 16.425 | 16.425 | pass |
| python-modern-3.14.8-standard-jit0-gil1-async | 500 | 6.450948398939425 | 44.0 | 81.71666666666667 | 81.71666666666667 | pass |
| python-modern-3.14.8-standard-jit0-gil1-async | 1000 | 6.39243793761935 | 44.0 | 163.64583333333334 | 163.64583333333334 | pass |
| rust-axum | 100 | 3.427844235174345 | 8.0 | 16.370833333333334 | 16.370833333333334 | pass |
| rust-axum | 500 | 2.914792718372342 | 7.0 | 81.7125 | 81.7125 | pass |
| rust-axum | 1000 | 2.712719967449903 | 6.0 | 163.85 | 163.85 | pass |

Reports: [95-page fixed report](reports/language-benchmark-report-2026-10-07.pdf), [54-page stress report](reports/language-benchmark-stress-report-2026-10-07.pdf).

Sentinel checks: fixed trials contain 24 passes and three failures (PHP-FPM at 500/1000, .NET at 1000). .NET at 1000 has distinct-account authentication 56/1000 (5.6%) and operation success 9.955165%; Octane at 1000 has 100% operation success, host CPU mean 26.504997% and host estimated memory mean 1606.253456 MiB. These host figures use historical unweighted sample reductions.
