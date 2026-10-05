# Multilanguage verification evidence

Checked locally on2026-10-05; no AWS capacity claims.

| Cell | Runtime/framework | Shared HTTP | Verified TLS + hostname rejection | Image ID |
| --- | --- | --- | --- | --- |
| java-spring-boot | 27 / 4.1.1 | 62 passed | yes | sha256:26793018c75bc0fcf78e11e14b9d7f76975dde092bb9b3c6f1e99a2d628737a3 |
| php-laravel | 8.5.11 / 13.34.0 | 62 passed | yes | sha256:872a1820e7896762ac77e43d086913493034c295baf91c3e6aed7fad3cfac1fe |
| go-net-http | 1.27.1 / 1.27.1 | 62 passed | yes | sha256:e314afd56759f7e291bbcc9ba06b6814493962eee6f8f633dfe8c889aaf9a823 |
| rust-axum | 1.99.0 / 0.8.9 | 62 passed | yes | sha256:d025742451a30889c92e9dc5a89cf0fca5ade1d138161cf1623631db69a97375 |
| node-express | 26.10.0 / 5.2.1 | 62 passed | yes | sha256:7909661e6e8ac8751e612e664873421fecc161a345002bdcfaa431cb433d33e5 |
| bun-express | 1.4.2 / 5.2.1 | 62 passed | yes | sha256:98768652fe972dee68614e9bad694b107cba7dd51564118dd1fcdde6115ac14e |

Every row was run with Docker NanoCpus2000000000, Memory3221225472 and MemorySwap3221225472. The verifier used a disposable local PostgreSQL17.6 database with a generated CA and leaf certificate; it then retried with an incorrect certificate hostname and confirmed readiness never succeeded.

The existing Python3.10 image independently passed the same62 HTTP cases with verified TLS. Root Python3.14 tests explicitly skip the three in-process end-of-day clock assertions because datetime.fromisoformat accepts24:00 on that interpreter; canonical3.10 and all six new cells reject it. No Python application behavior changed.

Native verification reported by subagents: Java11 JUnit tests and dependency-lock check; PHP protocol/config/JWT regressions; Go14 tests with race/vet; Rust13 tests with locked dependencies/clippy; Node/Bun16 native tests each. Independent scoped reviews were completed, findings fixed and targeted regressions rerun.

Raw reproduction logs are private local artifacts under artifacts/language-verification-reviewed, artifacts/language-verification-express-final and artifacts/language-verification-python310-reference. Reproduce with bench.languages verify as documented in multilanguage-backends.md.
