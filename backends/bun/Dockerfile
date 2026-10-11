FROM oven/bun:1.4.2-debian AS dependencies
WORKDIR /app
COPY backends/javascript/express/package.json backends/javascript/express/bun.lock ./
RUN bun install --frozen-lockfile --production

FROM oven/bun:1.4.2-debian
ARG WORKERS=2
ARG CONNECTIONS_PER_WORKER=10
ARG POOL_BUDGET=20
ARG UV_THREADPOOL_SIZE=2
RUN test "$((WORKERS * CONNECTIONS_PER_WORKER))" = "$POOL_BUDGET"
RUN case "$WORKERS:$CONNECTIONS_PER_WORKER" in 2:10|4:5|4:10) ;; *) exit 1 ;; esac
LABEL org.language-benchmark.language="bun" \
    org.language-benchmark.runtime-version="1.4.2" \
    org.language-benchmark.framework="express" \
    org.language-benchmark.framework-version="5.2.1" \
    org.language-benchmark.workers="${WORKERS}" \
    org.language-benchmark.connections-per-worker="${CONNECTIONS_PER_WORKER}" \
    org.language-benchmark.pool-budget="${POOL_BUDGET}"
ENV WORKERS=${WORKERS} NODE_ENV=production UV_THREADPOOL_SIZE=${UV_THREADPOOL_SIZE}
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY backends/javascript/express/package.json ./
COPY backends/javascript/express/src ./src
USER bun
EXPOSE 8000
CMD ["bun","src/server.js"]
