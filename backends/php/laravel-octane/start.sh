#!/bin/sh
set -eu
php -r 'require "/app/app/Support/Protocol.php"; require "/app/app/Support/RuntimeSettings.php"; App\Support\RuntimeSettings::validate();'
# The API is stateless, but Octane warms Laravel's encryption service. Derive
# its framework-only key with a distinct domain from the required JWT secret.
APP_KEY="$(php -r '$s=getenv("JWT_SECRET"); if(strlen($s ?: "")<32){fwrite(STDERR,"JWT_SECRET requires at least 32 bytes\n");exit(1);} echo "base64:".base64_encode(hash_hmac("sha256","laravel-octane-framework-key-v1",$s,true));')"
export APP_KEY
php artisan config:cache --quiet
# Preserve legacy pool behavior; production profile opts into RR soft recycling.
rr_config=/app/.rr.yaml
if [ "${RR_MAX_WORKER_MEMORY}" != 0 ]; then
    case "$RR_MAX_WORKER_MEMORY" in 256) ;; *) echo "RR_MAX_WORKER_MEMORY must be 0 or 256" >&2; exit 1;; esac
    sed "/    num_workers: 20/s/20/${PHP_WORKERS}/; /    max_jobs: 10000/a\\    supervisor:\\n      max_worker_memory: ${RR_MAX_WORKER_MEMORY}" /app/.rr.yaml > /tmp/benchmark-rr.yaml
    rr_config=/tmp/benchmark-rr.yaml
fi
exec php artisan octane:start --server=roadrunner --host="${BIND_IP:-0.0.0.0}" --port="${PORT:-8000}" --rpc-host=127.0.0.1 --rpc-port=6001 --workers="${PHP_WORKERS}" --max-requests=10000 --rr-config="$rr_config" --log-level=warn --no-interaction
