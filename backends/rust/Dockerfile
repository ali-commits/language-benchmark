FROM rust:1.99.0-bookworm@sha256:59037199c44290f2befcdd58dcc540164763fc296950255aaefeef096a1866b0 AS build
WORKDIR /app
COPY backends/rust/Cargo.toml backends/rust/Cargo.lock ./
COPY backends/rust/src ./src
RUN cargo build --release --locked
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
ARG DB_POOL_MAX_SIZE=20
ARG DB_POOL_MIN_SIZE=0
LABEL org.language-benchmark.language="rust" \
    org.language-benchmark.runtime-version="1.99.0" \
    org.language-benchmark.framework="axum" \
    org.language-benchmark.framework-version="0.8.9" \
    org.language-benchmark.workers="1" \
    org.language-benchmark.connections-per-worker="$DB_POOL_MAX_SIZE"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --system --uid 10001 app
COPY --from=build /app/target/release/language-benchmark-rust /usr/local/bin/social-api
USER 10001
ENV BIND_IP=0.0.0.0 PORT=8000
ARG HASH_CONCURRENCY=2
RUN case "$HASH_CONCURRENCY" in 1|2|3|4) ;; *) echo "invalid HASH_CONCURRENCY" >&2; exit 1 ;; esac
ENV HASH_CONCURRENCY=$HASH_CONCURRENCY
ARG CPU_PARALLELISM=2
RUN case "$CPU_PARALLELISM" in 1|2|3|4) ;; *) echo "invalid CPU_PARALLELISM" >&2; exit 1 ;; esac
ENV CPU_PARALLELISM=$CPU_PARALLELISM
ENV DB_POOL_MAX_SIZE=$DB_POOL_MAX_SIZE DB_POOL_MIN_SIZE=$DB_POOL_MIN_SIZE
EXPOSE 8000
ENTRYPOINT ["social-api"]
