<?php

require __DIR__.'/../vendor/autoload.php';
use App\Support\Database;

function expect($test, $message)
{
    if (! $test) {
        throw new RuntimeException($message);
    }
}
if (! str_contains(getenv('DATABASE_URL') ?: '', 'octane_test')) {
    throw new RuntimeException('dedicated disposable Octane database required');
}
$db = new Database;
$before = (int) $db->query('SELECT pg_backend_pid() AS pid')[0]['pid'];
$db->release();
$after = (int) $db->query('SELECT pg_backend_pid() AS pid')[0]['pid'];
expect($before === $after, 'request release must reuse the same worker connection');
echo "connection reused across logical requests\n";
$db->query('CREATE TABLE IF NOT EXISTS octane_transaction_guard(id bigint PRIMARY KEY)');
$db->query('TRUNCATE octane_transaction_guard');
try {
    $db->transaction(function () use ($db) {
        $db->query('INSERT INTO octane_transaction_guard VALUES(1)');
        throw new RuntimeException('rollback proof');
    });
} catch (RuntimeException $error) {
    expect($error->getMessage() === 'rollback proof', 'original transaction failure preserved');
}
expect((int) $db->query('SELECT count(*) AS n FROM octane_transaction_guard')[0]['n'] === 0, 'failed transaction rolls back');
// Any abandoned transaction is cleaned before the next logical request.
$db->connection()->beginTransaction();
$db->query('INSERT INTO octane_transaction_guard VALUES(2)');
$db->finishRequest();
expect((int) $db->query('SELECT count(*) AS n FROM octane_transaction_guard')[0]['n'] === 0, 'abandoned request transaction must not leak');
$config = Database::configuration(getenv('DATABASE_URL'), 'local');
$admin = new PDO($config['dsn'], $config['user'], $config['password']);
$old = (int) $db->query('SELECT pg_backend_pid() AS pid')[0]['pid'];
$admin->exec('SELECT pg_terminate_backend('.$old.')');
$db->finishRequest();
$fresh = (int) $db->query('SELECT pg_backend_pid() AS pid')[0]['pid'];
expect($old !== $fresh, 'lost idle connection reconnects for next read');
$db->transaction(fn () => $db->query('INSERT INTO octane_transaction_guard VALUES(3)'));
expect((int) $db->query('SELECT count(*) AS n FROM octane_transaction_guard')[0]['n'] === 1, 'healthy write after reconnect commits');
$db->query('DROP TABLE octane_transaction_guard');
echo "rollback, cleanup and reconnect persistence checks passed\n";
