# Multilanguage Backends Implementation Plan

> For agentic workers: Use superpowers:subagent-driven-development for owned implementations and contract review.

**Goal:** Build and verify six idiomatic APIs with identical public behavior.
**Architecture:** Independent language directories share schema and contract. Node and Bun share the Express source. Parent owns integration/tests/runtime staging/Compose; three workers each own two implementations.
**Tech Stack:** Spring Boot/latest stable JDK, Laravel/PHP, net/http/pgx, Axum/Tokio, Express on Node/Bun, PostgreSQL, Docker, uv tests.
**Spec:** docs/superpowers/specs/2026-10-05-multilanguage-backends.md

## Global Constraints

- Main checkout, no worktrees or changes to active AWS/Python experiment.
- Exactly2 CPU quota,3GiB,no swap,20 aggregate database connections.
- Identical schema/API, Argon2id65536KiB/time3/parallelism1/salt16/hash32 and JWT claims.
- Preserve timestamps/bigints, strict input validation, cursor interoperability.
- Bun for JS/TS package management; native locks elsewhere; uv for root tooling.

## Review Focus

- Cursor ties and microseconds must not skip rows.
- Error statuses/schema, extra keys, JSON types,413 and empty204 bodies must match.
- Committed writes are visible immediately; likes/follows remain idempotent.
- PHC hashes and JWT claim/algorithm checks interoperate.
- Production topologies enforce aggregate pools; no developer servers.

## Task1: Shared staging and verification (parent)

Files: tests/contract, tests/multilanguage, compose.languages.yaml, new language runtime records, docs.
- [x] Extend external/differential tests for validation, auth, following feeds, ties, idempotence, missing resources, seed PHCs, health and commits.
- [x] Stage six local runtime definitions/profiles separately from the Python matrix.
- [x] Build all images and run shared contracts against disposable PostgreSQL.
- [x] Document actual versions, topologies, verification and limits.

## Task2: Node and Bun (worker A)

Ownership: backends/javascript/express, backends/node, backends/bun.
- [x] Research stable versions; implement shared Express API with precise IDs/timestamps, bounded hashing and two-worker pools.
- [x] Add Bun locks, pinned Dockerfiles, unit tests, READMEs.
- [x] Build/test and report exact commands and results.

## Task3: Go and Rust (worker B)

Ownership: backends/go, backends/rust.
- [x] Research versions; implement full APIs with bounded hashing, pools and transactional mutations.
- [x] Add pinned Dockerfiles, native locks, tests and READMEs.
- [x] Build/test and report commands/results.

## Task4: Java and PHP (worker C)

Ownership: backends/java, backends/php.
- [x] Research versions; implement Spring virtual-thread/JDBC services and production Laravel workers.
- [x] Add pinned Dockerfiles/locks, tests/README and pool/worker limits.
- [x] Build/test and report commands/results.

## Task5: Review (parent with scoped subagent review)

- [x] All six pass shared HTTP tests, independently reviewed for semantics and quality.
- [x] Fix and recheck discrepancies without interfering with AWS.
- [x] Commit coherent results and report setup/verification.

User refinement: use exactly one latest stable language/framework version per requested pair; no LTS preference or extra runtime variants. Verify versions from official upstream metadata and pin exact resolved versions.
