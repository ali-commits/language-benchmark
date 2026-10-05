FROM golang:1.27.1-bookworm@sha256:69a7b9788769bec032d238959b61854e9ae87f57be9029ec04e9885fabf99195 AS build
WORKDIR /app
COPY backends/go/go.mod backends/go/go.sum ./
RUN go mod download
COPY backends/go/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /social-api .
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
LABEL org.language-benchmark.language="go" \
    org.language-benchmark.runtime-version="1.27.1" \
    org.language-benchmark.framework="net-http" \
    org.language-benchmark.framework-version="1.27.1" \
    org.language-benchmark.workers="1" \
    org.language-benchmark.connections-per-worker="20"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --system --uid 10001 app
COPY --from=build /social-api /usr/local/bin/social-api
USER 10001
ENV BIND_IP=0.0.0.0 PORT=8000 GOMAXPROCS=2 GOMEMLIMIT=2GiB
EXPOSE 8000
ENTRYPOINT ["social-api"]
