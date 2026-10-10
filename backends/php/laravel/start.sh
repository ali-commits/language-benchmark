#!/bin/sh
set -eu
php -r 'require "/app/app/Support/Protocol.php"; App\Support\Protocol::hashConcurrency();'
sed "s/listen 0.0.0.0:8000;/listen ${BIND_IP:-0.0.0.0}:${PORT:-8000};/" /app/nginx.conf > /tmp/nginx.conf
php-fpm --fpm-config /app/fpm.conf &
fpm=$!
nginx -c /tmp/nginx.conf -g 'daemon off;' &
nginx=$!
trap 'kill -QUIT "$nginx"; kill -QUIT "$fpm"; wait; exit 0' TERM INT
while kill -0 "$fpm" 2>/dev/null && kill -0 "$nginx" 2>/dev/null; do sleep 1; done
kill "$fpm" "$nginx" 2>/dev/null || true
wait || true
exit 1
