<?php
namespace App\Support;

final class RuntimeSettings
{
    public static function validate(): void
    {
        Protocol::hashConcurrency();
        Protocol::workers();
        foreach ([
            'PHP_MEMORY_LIMIT' => ['128M', '256M'],
            'OPCACHE_MEMORY' => ['128', '256'],
            'OPCACHE_INTERNED_STRINGS' => ['8', '16'],
            'DB_PERSISTENT' => ['0', '1'],
            'DB_SESSION_RESET' => ['0', '1'],
            'RR_MAX_WORKER_MEMORY' => ['0', '256'],
        ] as $key => $allowed) {
            $value = getenv($key);
            if ($value !== false && !in_array($value, $allowed, true)) {
                throw new \InvalidArgumentException($key.' has an unsupported value');
            }
        }
    }
}
