<?php

namespace App\Support;

use InvalidArgumentException;

final class Protocol
{
    public static function b64(string $s): string
    {
        return rtrim(strtr(base64_encode($s), '+/', '-_'), '=');
    }

    public static function issue(int $id, string $secret, int $ttl, int $now): string
    {
        $header = self::b64('{"alg":"HS256","typ":"JWT"}');
        $body = self::b64(json_encode(['sub' => (string) $id, 'iat' => $now, 'exp' => $now + $ttl, 'iss' => 'language-benchmark', 'aud' => 'social-api'], JSON_THROW_ON_ERROR));

        return "$header.$body.".self::b64(hash_hmac('sha256', "$header.$body", $secret, true));
    }

    public static function decode(string $s): string
    {
        $r = base64_decode(strtr($s, '-_', '+/'), true);
        if ($r === false) {
            throw new InvalidArgumentException;
        }

        return $r;
    }

    public static function identity(string $token, string $secret, int $now): int
    {
        try {
            $parts = explode('.', $token);
            if (count($parts) !== 3) {
                throw new InvalidArgumentException;
            }[$h,$b,$signature] = $parts;
            $head = json_decode(self::decode($h), true, 512, JSON_THROW_ON_ERROR);
            $payload = json_decode(self::decode($b), false, 512, JSON_THROW_ON_ERROR);
            if (! ($payload instanceof \stdClass)) {
                throw new InvalidArgumentException;
            }
            $data = get_object_vars($payload);
            if (($head['alg'] ?? '') !== 'HS256' || ! hash_equals(hash_hmac('sha256', "$h.$b", $secret, true), self::decode($signature)) || ($data['iss'] ?? null) !== 'language-benchmark' || ! self::audience($data['aud'] ?? null) || ! is_int($data['iat'] ?? null) || ! is_int($data['exp'] ?? null) || $data['exp'] <= $now || $data['iat'] > $now + 30 || ! is_string($data['sub'] ?? null) || ! preg_match('/^\+?\d+$/D', $data['sub'])) {
                throw new InvalidArgumentException;
            }
            if (array_key_exists('nbf', $data)) {
                $nbf = $data['nbf'];
                if (! is_int($nbf) && ! (is_string($nbf) && preg_match('/^[+-]?\\d+$/D', $nbf))) {
                    throw new InvalidArgumentException;
                }
                if ((int) $nbf > $now) {
                    throw new InvalidArgumentException;
                }
            }
            $id = filter_var($data['sub'], FILTER_VALIDATE_INT);
            if ($id === false || $id <= 0) {
                throw new InvalidArgumentException;
            }

            return $id;
        } catch (\Throwable) {
            throw new InvalidArgumentException('invalid token');
        }
    }

    private static function audience(mixed $value): bool
    {
        if (is_string($value)) {
            return $value === 'social-api';
        }
        if (! is_array($value) || ! array_is_list($value)) {
            return false;
        }
        foreach ($value as $entry) {
            if (! is_string($entry)) {
                return false;
            }
        }

        return in_array('social-api', $value, true);
    }

    public static function cursor(string $stamp, int $id): string
    {
        return self::b64(json_encode([$stamp, $id], JSON_THROW_ON_ERROR));
    }

    public static function decodeCursor(?string $cursor): array
    {
        if ($cursor === null) {
            return [null, 0];
        }try {
            if (strlen($cursor) < 1 || strlen($cursor) > 256) {
                throw new InvalidArgumentException;
            }$data = json_decode(self::decode($cursor), true, 512, JSON_THROW_ON_ERROR);
            if (! is_array($data) || ! array_is_list($data) || count($data) !== 2 || ! is_string($data[0]) || ! is_int($data[1]) || $data[1] <= 0 || ! preg_match('/(?:Z|[+-]\d\d:\d\d)$/D', $data[0])) {
                throw new InvalidArgumentException;
            }
            $stamp = new \DateTimeImmutable($data[0]);
            $errors = \DateTimeImmutable::getLastErrors();
            if ((int) $stamp->format('Y') < 1 || (int) $stamp->format('Y') > 9999 || (int) $stamp->setTimezone(new \DateTimeZone('UTC'))->format('Y') < 1 || (int) $stamp->setTimezone(new \DateTimeZone('UTC'))->format('Y') > 9999) {
                throw new InvalidArgumentException;
            }
            if ($errors !== false && ($errors['warning_count'] || $errors['error_count'])) {
                throw new InvalidArgumentException;
            }

            return [$stamp->setTimezone(new \DateTimeZone('UTC'))->format('Y-m-d\TH:i:s.uP'), $data[1]];
        } catch (\Throwable) {
            throw new InvalidArgumentException('invalid cursor');
        }
    }

    public static function validate(mixed $input, string $kind): array
    {
        $fields = in_array($kind, ['login', 'register']) ? ['username' => [3, 32], 'password' => [$kind === 'register' ? 12 : 1, 128]] : ['content' => [1, $kind === 'comment' ? 1000 : 280]];
        $errors = [];
        if (! is_array($input) || array_is_list($input) && $input !== []) {
            throw new InputError([['type' => 'model_attributes_type', 'loc' => ['body'], 'msg' => 'Input should be a valid dictionary or object to extract fields from', 'input' => $input]]);
        }
        foreach ($fields as $name => [$min,$max]) {
            if (! array_key_exists($name, $input)) {
                $errors[] = ['type' => 'missing', 'loc' => ['body', $name], 'msg' => 'Field required', 'input' => (object) $input];

                continue;
            }
            $v = $input[$name];
            $base = ['loc' => ['body', $name], 'input' => $v];
            if (! is_string($v)) {
                $errors[] = array_merge(['type' => 'string_type', 'msg' => 'Input should be a valid string'], $base);

                continue;
            }
            $n = mb_strlen($v, 'UTF-8');
            if ($n < $min) {
                $errors[] = array_merge(['type' => 'string_too_short', 'msg' => "String should have at least $min ".($min === 1 ? 'character' : 'characters'), 'ctx' => ['min_length' => $min]], $base);
            } elseif ($n > $max) {
                $errors[] = array_merge(['type' => 'string_too_long', 'msg' => "String should have at most $max characters", 'ctx' => ['max_length' => $max]], $base);
            } elseif ($name === 'username' && ! preg_match('/^[A-Za-z0-9_]+$/D', $v)) {
                $errors[] = array_merge(['type' => 'string_pattern_mismatch', 'msg' => "String should match pattern '^[A-Za-z0-9_]+$'", 'ctx' => ['pattern' => '^[A-Za-z0-9_]+$']], $base);
            } elseif ($name === 'content' && preg_match('/^[\s\x1c-\x1f]*$/uD', $v)) {
                $errors[] = array_merge(['type' => 'value_error', 'msg' => 'Value error, content must not be blank', 'ctx' => ['error' => new \stdClass]], $base);
            }
        }
        foreach (array_diff(array_keys($input), array_keys($fields)) as $key) {
            $errors[] = ['type' => 'extra_forbidden', 'loc' => ['body', $key], 'msg' => 'Extra inputs are not permitted', 'input' => $input[$key]];
        }
        if ($errors) {
            throw new InputError($errors);
        }if (isset($input['username'])) {
            $input['username'] = strtolower($input['username']);
        }

        return $input;
    }

    public static function hashConcurrency(): int
    {
        $value = getenv('HASH_CONCURRENCY');
        if ($value === false) {
            return 2;
        }
        if (! preg_match('/^[1-4]$/D', $value)) {
            throw new InvalidArgumentException('HASH_CONCURRENCY must be an integer from 1 to 4');
        }

        return (int) $value;
    }

    public static function workers(): int
    {
        $value = getenv('PHP_WORKERS');
        if ($value === false) return 20;
        if (! preg_match('/^(?:[1-9]|[1-5][0-9]|6[0-4])$/D', $value)) {
            throw new InvalidArgumentException('PHP_WORKERS must be an integer from 1 to 64');
        }
        return (int) $value;
    }

    public static function publishTuningReceipt(): void
    {
        $capacity = self::hashConcurrency();
        $directory = getenv('RUN_DIR');
        if ($directory === false || $directory === '') {
            return;
        }
        $runId = getenv('RUN_ID');
        if ($runId === false || $runId === '') {
            throw new InvalidArgumentException('RUN_ID is required for tuning receipts');
        }
        if (! is_dir($directory) || ! is_writable($directory)) {
            throw new \RuntimeException('Tuning receipt directory is unavailable');
        }
        $pid = getmypid();
        $production = getenv('BENCH_PROFILE') === 'production4';
        $receipt = [
            'schema_version' => $production ? 2 : 1,
            'run_id' => $runId,
            'pid' => $pid,
            'language' => 'php',
            'workers_total' => self::workers(),
            'connections_per_worker' => 1,
            'pool_budget' => self::workers(),
            'cpu_parallelism' => null,
            'hash_concurrency' => $capacity,
            'hash_scope' => 'global',
        ];
        if ($production) {
            $receipt += [
                'db_connection_reuse' => 'worker-retained',
                'db_session_reset' => getenv('DB_SESSION_RESET') === '1',
                'memory_limit' => ini_get('memory_limit'),
                'opcache_memory' => (int) ini_get('opcache.memory_consumption'),
                'opcache_interned_strings' => (int) ini_get('opcache.interned_strings_buffer'),
            ];
        }
        $payload = json_encode($receipt, JSON_THROW_ON_ERROR);
        $destination = $directory.'/tuning-'.$pid.'.json';
        // FPM initializes Laravel per request; retain the same process receipt.
        if (is_file($destination) && file_get_contents($destination) === $payload) {
            return;
        }
        $temporary = tempnam($directory, '.tuning-');
        if ($temporary === false) {
            throw new \RuntimeException('Cannot create tuning receipt');
        }
        try {
            if (file_put_contents($temporary, $payload) !== strlen($payload) || ! rename($temporary, $destination)) {
                throw new \RuntimeException('Cannot publish tuning receipt');
            }
        } finally {
            if (is_file($temporary)) {
                unlink($temporary);
            }
        }
    }

    public static function password(string $password, ?string $hash = null): string|bool
    {
        // File locks coordinate memory-expensive Argon2 work across all workers.
        $capacity = self::hashConcurrency();
        while (true) {
            for ($slot = 0; $slot < $capacity; $slot++) {
                $f = fopen('/tmp/language-benchmark-hash-'.$slot, 'c');
                if ($f === false) {
                    throw new \RuntimeException('Cannot open global hash slot');
                }
                if (flock($f, LOCK_EX | LOCK_NB)) {
                    try {
                        return $hash === null ? password_hash($password, PASSWORD_ARGON2ID, ['memory_cost' => 65536, 'time_cost' => 3, 'threads' => 1]) : password_verify($password, $hash);
                    } finally {
                        flock($f, LOCK_UN);
                        fclose($f);
                    }
                }
                fclose($f);
            }
            usleep(1000);
        }
    }
}
