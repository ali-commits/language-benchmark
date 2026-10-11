FROM ghcr.io/roadrunner-server/roadrunner:2025.1.15@sha256:18378c1313a71ce1c3cfc493c99e75c189ef5fb2eaf85290b8a99080fcb0f64f AS engine
FROM php:8.5.11-cli-bookworm@sha256:819080ed413631f6ec564184dd9acd08d9425bd863b9f8b5876b5ea2147d55e7
ARG PHP_WORKERS=20
ARG PHP_MEMORY_LIMIT=256M
ARG OPCACHE_MEMORY=128
ARG OPCACHE_INTERNED_STRINGS=8
ARG DB_PERSISTENT=0
ARG DB_SESSION_RESET=0
ARG RR_MAX_WORKER_MEMORY=0
ENV PHP_WORKERS=${PHP_WORKERS} PHP_MEMORY_LIMIT=${PHP_MEMORY_LIMIT} OPCACHE_MEMORY=${OPCACHE_MEMORY} OPCACHE_INTERNED_STRINGS=${OPCACHE_INTERNED_STRINGS} DB_PERSISTENT=${DB_PERSISTENT} DB_SESSION_RESET=${DB_SESSION_RESET} RR_MAX_WORKER_MEMORY=${RR_MAX_WORKER_MEMORY}
LABEL org.language-benchmark.language="php" \
 org.language-benchmark.runtime-version="8.5.11" \
 org.language-benchmark.framework="laravel-octane" \
 org.language-benchmark.framework-version="2.21.0" \
 org.language-benchmark.workers="${PHP_WORKERS}" \
 org.language-benchmark.connections-per-worker="1"
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev unzip && docker-php-ext-install pdo_pgsql sockets pcntl && rm -rf /var/lib/apt/lists/*
COPY --from=composer:2.10.3@sha256:af98f42dfff7c68ba8d53c2164fd9fde1087b7d449514baa38c418b1f6bc4bac /usr/bin/composer /usr/local/bin/composer
WORKDIR /app
COPY backends/php/laravel-octane/composer.json backends/php/laravel-octane/composer.lock ./
RUN composer install --no-dev --no-interaction --prefer-dist --no-scripts --no-autoloader
ARG HASH_CONCURRENCY=2
ENV HASH_CONCURRENCY=${HASH_CONCURRENCY}
COPY backends/php/laravel-octane/app ./app
COPY backends/php/laravel-octane/bootstrap ./bootstrap
COPY backends/php/laravel-octane/config ./config
COPY backends/php/laravel-octane/routes ./routes
COPY backends/php/laravel-octane/tests ./tests
COPY backends/php/laravel-octane/artisan backends/php/laravel-octane/start.sh backends/php/laravel-octane/.rr.yaml ./
COPY --from=engine /usr/bin/rr /app/rr
COPY backends/php/laravel-octane/php.ini /usr/local/etc/php/conf.d/benchmark.ini
RUN mkdir -p public bootstrap/cache storage/logs storage/framework/cache && composer dump-autoload --classmap-authoritative --no-dev && php tests/run.php && php tests/tuning.php && php artisan config:cache && php artisan route:cache && chmod +x start.sh && chown -R www-data:www-data /app/storage /app/bootstrap/cache
USER www-data
ENV BIND_IP=0.0.0.0 PORT=8000
EXPOSE 8000
ENTRYPOINT ["/app/start.sh"]
