FROM oven/bun:1.4.2-debian AS dependencies
WORKDIR /app
COPY backends/javascript/express/package.json backends/javascript/express/bun.lock ./
RUN bun install --frozen-lockfile --production

FROM node:26.10.0-bookworm-slim
ARG WORKERS=2
ARG CONNECTIONS_PER_WORKER=10
RUN case "$WORKERS:$CONNECTIONS_PER_WORKER" in 2:10|4:5) ;; *) exit 1 ;; esac
LABEL org.language-benchmark.language="node" \
    org.language-benchmark.runtime-version="26.10.0" \
    org.language-benchmark.framework="express" \
    org.language-benchmark.framework-version="5.2.1" \
    org.language-benchmark.workers="${WORKERS}" \
    org.language-benchmark.connections-per-worker="${CONNECTIONS_PER_WORKER}"
ENV WORKERS=${WORKERS} NODE_ENV=production UV_THREADPOOL_SIZE=2
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY backends/javascript/express/package.json ./
COPY backends/javascript/express/src ./src
USER node
EXPOSE 8000
CMD ["node","src/server.js"]
