#!/bin/sh
set -eu
php -r 'require "/app/app/Support/Protocol.php"; App\Support\Protocol::hashConcurrency();'
# The API is stateless, but Octane warms Laravel's encryption service. Derive
# its framework-only key with a distinct domain from the required JWT secret.
APP_KEY="$(php -r '$s=getenv("JWT_SECRET"); if(strlen($s ?: "")<32){fwrite(STDERR,"JWT_SECRET requires at least 32 bytes\n");exit(1);} echo "base64:".base64_encode(hash_hmac("sha256","laravel-octane-framework-key-v1",$s,true));')"
export APP_KEY
php artisan config:cache --quiet
exec php artisan octane:start --server=roadrunner --host="${BIND_IP:-0.0.0.0}" --port="${PORT:-8000}" --rpc-host=127.0.0.1 --rpc-port=6001 --workers=20 --max-requests=10000 --rr-config=/app/.rr.yaml --log-level=warn --no-interaction
