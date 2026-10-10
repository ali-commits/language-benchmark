# Java / Spring Boot

One implementation: Java **27+35**, Spring Boot **4.1.1**, PostgreSQL JDBC **42.7.13**, Bouncy Castle **1.86**. The pinned Spring Boot BOM manages transitive dependencies; `dependencies.lock` records the runtime dependency graph and `verify-lock.sh` verifies it.

A Spring MVC application uses virtual request threads, JDBC with Hikari's aggregate **20-connection** pool, parameterized queries, and scoped Spring transactions. Two concurrent Argon2id jobs are allowed; login checks out a database connection only for the account lookup and releases it before expensive password verification. The JVM's heap is capped at 65% of container RAM, leaving room for Argon2/native memory.

Build from repository root:

```sh
docker build -f backends/java/spring-boot/Dockerfile -t lb-java-spring:27 .
docker run --rm --cpus=2 --memory=3g --memory-swap=3g -p 8000:8000 \
  -e DATABASE_URL='postgresql://user:password@db:5432/language_benchmark' \
  -e JWT_SECRET='replace-with-at-least-32-bytes-secret' lb-java-spring:27
```

The build runs native JUnit tests. With Java27 and Maven3.9.16 installed: `mvn test` and `MAVEN_CMD=mvn ./verify-lock.sh` in this directory. Shared HTTP verification uses root `uv run -m pytest tests/contract tests/multilanguage`, with `TARGET_URL` and a dedicated `TEST_DATABASE_URL` named `language_benchmark*_test`. Local worker verification used disposable PostgreSQL at port55438 and API18038.

`DATABASE_URL` accepts `postgresql://` (or Python's `postgresql+psycopg://`) including URL-escaped credentials and libpq-style `sslmode` / `sslrootcert` query parameters. `MODE=aws` requires verified TLS. Mount the trusted RDS CA inside the container and refer to that container path. `JWT_SECRET` requires32 UTF-8 bytes; `JWT_TTL_SECONDS` defaults3600 (600–86400). `PORT` defaults8000, `BIND_IP` defaults0.0.0.0. `RUN_ID` and `RUN_DIR` are optional orchestration metadata; no database migration/seeding occurs at startup.

The body filter limits POST/PUT/PATCH to16384 bytes, including chunked bodies. Bigint IDs remain64-bit integers and pagination retains PostgreSQL microseconds. Health routes provide liveness, database readiness and actual connection checkout metrics.

Version sources: [Java27 GA](https://jdk.java.net/27/), [Temurin official image metadata](https://github.com/docker-library/official-images/blob/master/library/eclipse-temurin), [Spring Boot4.1.1](https://spring.io/blog/2026/08/20/spring-boot-4-1-1-available-now/). Java27 is the user's latest-stable selection; this application was built and tested on27 rather than relying on the Spring documentation's earlier supported-version range. No native-image/AOT variant is included.

Verified locally: all **62 external contract/interoperability tests** passed, including the captured Python validation-response oracle. Wait for `/health/ready` before starting verification or measurement. The shared harness emits one existing Starlette/httpx deprecation warning.

All database sessions identify as `lb-backend` for the shared restore guard and use a10-second PostgreSQL statement timeout. AWS TLS settings reject duplicate or ambiguous parameters. JWT validation includes optional `nbf` and audience arrays.

`HASH_CONCURRENCY` accepts integers 1–4 and defaults to 2. It controls the process-wide Argon2 admission capacity without changing Argon2 parameters or the 20-connection database pool. The Docker build argument of the same name preserves the default and can bake an explicit tuned capacity of 4. Invalid or empty supplied values fail startup.

When `RUN_DIR` is set, startup atomically publishes a credential-free `tuning-<serving PID>.json` receipt using `RUN_ID`, observed runtime CPU parallelism, effective hash slots, and pool capacity. The receipt is readable by the host-side verifier.

Tuned deployments override `JAVA_TOOL_OPTIONS` with `-XX:MaxRAMPercentage=65 -XX:ActiveProcessorCount=4` under the 6GiB container limit. The historical image default remains two processors; receipts observe `Runtime.availableProcessors()`.
