FROM rust:1.99.0-bookworm@sha256:59037199c44290f2befcdd58dcc540164763fc296950255aaefeef096a1866b0 AS build
WORKDIR /app
COPY backends/rust/Cargo.toml backends/rust/Cargo.lock ./
COPY backends/rust/src ./src
RUN cargo build --release --locked
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --system --uid 10001 app
COPY --from=build /app/target/release/language-benchmark-rust /usr/local/bin/social-api
USER 10001
ENV BIND_IP=0.0.0.0 PORT=8000
EXPOSE 8000
ENTRYPOINT ["social-api"]
