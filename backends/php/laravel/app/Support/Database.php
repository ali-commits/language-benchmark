<?php

namespace App\Support;

final class Database
{
    private ?\PDO $connection = null;

    public static int $acquires = 0;

    public static float $acquireMs = 0;

    public function connection(): \PDO
    {
        if ($this->connection !== null) {
            return $this->connection;
        }
        $config = self::configuration(getenv('DATABASE_URL'), getenv('MODE') ?: 'local');
        $start = hrtime(true);
        $this->connection = new \PDO($config['dsn'], $config['user'], $config['password'], [\PDO::ATTR_ERRMODE => \PDO::ERRMODE_EXCEPTION, \PDO::ATTR_EMULATE_PREPARES => false, \PDO::ATTR_PERSISTENT => self::persistent()]);
        if (self::persistent()) {
            $this->resetSession();
        }
        self::$acquires++;
        $elapsed = (hrtime(true) - $start) / 1e6;
        self::$acquireMs += $elapsed;
        $this->record($elapsed);

        return $this->connection;
    }

    public static function configuration(string $value, string $mode): array
    {
        $url = parse_url(str_replace('postgresql+psycopg://', 'postgresql://', $value));
        if ($url === false || ($url['scheme'] ?? '') !== 'postgresql' || empty($url['host']) || empty($url['path'])) {
            throw new \InvalidArgumentException('PostgreSQL URL required');
        }
        $q = [];
        foreach (explode('&', $url['query'] ?? '') as $part) {
            if ($part === '') {
                continue;
            }$kv = explode('=', $part, 2);
            $key = urldecode($kv[0]);
            $q[$key][] = urldecode($kv[1] ?? '');
        }
        foreach (['sslmode', 'sslrootcert'] as $key) {
            if (isset($q[$key]) && count($q[$key]) !== 1) {
                throw new \InvalidArgumentException('ambiguous TLS configuration');
            }
        }
        if ($mode === 'aws' && (($q['sslmode'] ?? []) !== ['verify-full'] || count($q['sslrootcert'] ?? []) !== 1 || trim($q['sslrootcert'][0]) === '')) {
            throw new \InvalidArgumentException('AWS requires verify-full and sslrootcert');
        }
        $dsn = 'pgsql:host='.$url['host'].';port='.($url['port'] ?? 5432).';dbname='.rawurldecode(substr($url['path'], 1));
        foreach (['sslmode', 'sslrootcert'] as $key) {
            if (isset($q[$key])) {
                $dsn .= ";$key=".$q[$key][0];
            }
        }
        $dsn .= ";application_name=lb-backend;options='-c statement_timeout=10000'";

        return ['dsn' => $dsn, 'user' => rawurldecode($url['user'] ?? ''), 'password' => rawurldecode($url['pass'] ?? '')];
    }

    private function record(float $elapsed): void
    {
        $f = fopen('/tmp/language-benchmark-db-metrics', 'c+');
        flock($f, LOCK_EX);
        $v = json_decode(stream_get_contents($f), true) ?? [0, 0];
        $v[0]++;
        $v[1] += $elapsed;
        ftruncate($f, 0);
        rewind($f);
        fwrite($f, json_encode($v));
        flock($f, LOCK_UN);
        fclose($f);
    }

    public static function metrics(): array
    {
        $f = fopen('/tmp/language-benchmark-db-metrics', 'c+');
        flock($f, LOCK_SH);
        $v = json_decode(stream_get_contents($f), true) ?? [0, 0];
        flock($f, LOCK_UN);
        fclose($f);

        return ['connection_acquires' => $v[0], 'connection_acquire_ms' => $v[1]];
    }

    public static function persistent(): bool
    {
        $value = getenv('DB_PERSISTENT');
        if ($value === false || $value === '0') return false;
        if ($value !== '1') throw new \InvalidArgumentException('DB_PERSISTENT must be 0 or 1');
        return true;
    }

    private function resetSession(): void
    {
        if ($this->connection === null) return;
        if ($this->connection->inTransaction()) $this->connection->rollBack();
        // DISCARD restores temporary objects, roles, settings, listeners and locks.
        // No PDOStatement survives a query, so native prepared handles are not reused.
        $this->connection->exec('DISCARD ALL');
        $this->connection->exec("SET application_name = 'lb-backend'; SET statement_timeout = '10000'");
    }

    public function release(): void
    {
        try {
            if (self::persistent()) $this->resetSession();
        } catch (\PDOException $error) {
            // A disconnected session cannot contaminate the next request. New PDO
            // performs the driver's persistent-connection liveness check.
            if (!str_starts_with((string)($error->errorInfo[0] ?? $error->getCode()), '08')
                && !in_array($error->getCode(), ['57P01', '57P02', '57P03'], true)
                && preg_match('/server closed the connection|no connection to the server|connection not open/i', $error->getMessage()) !== 1) throw $error;
        } finally {
            $this->connection = null;
        }
    }


    public function query(string $sql, array $params = []): array
    {
        $s = $this->connection()->prepare($sql);
        $s->execute($params);

        return $s->columnCount() ? $s->fetchAll(\PDO::FETCH_ASSOC) : [];
    }

    public function transaction(callable $fn): mixed
    {
        $p = $this->connection();
        $p->beginTransaction();
        try {
            $r = $fn();
            $p->commit();

            return $r;
        } catch (\Throwable $e) {
            $p->rollBack();
            throw $e;
        }
    }
}
