FROM php:8.5.11-fpm-bookworm@sha256:53eab56a8f43f51a92119f6c29b3448af98eec288ff17f92829354a4b4c9ca05 AS base
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
    org.language-benchmark.framework="laravel" \
    org.language-benchmark.framework-version="13.34.0" \
    org.language-benchmark.workers="${PHP_WORKERS}" \
    org.language-benchmark.connections-per-worker="1"
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev nginx unzip && docker-php-ext-install pdo_pgsql && rm -rf /var/lib/apt/lists/*
COPY --from=composer:2.10.3@sha256:af98f42dfff7c68ba8d53c2164fd9fde1087b7d449514baa38c418b1f6bc4bac /usr/bin/composer /usr/local/bin/composer
WORKDIR /app
COPY backends/php/laravel/composer.json backends/php/laravel/composer.lock ./
RUN composer install --no-dev --no-interaction --prefer-dist --no-scripts --no-autoloader
ARG HASH_CONCURRENCY=2
ENV HASH_CONCURRENCY=${HASH_CONCURRENCY}
COPY backends/php/laravel/ ./
RUN composer dump-autoload --classmap-authoritative --no-dev && mkdir -p bootstrap/cache storage/framework/cache storage/logs && chown -R www-data:www-data bootstrap/cache storage && chmod +x start.sh && php tests/run.php && php tests/tuning.php && php artisan config:cache && php artisan route:cache
COPY backends/php/laravel/php.ini /usr/local/etc/php/conf.d/benchmark.ini
EXPOSE 8000
CMD ["/app/start.sh"]
