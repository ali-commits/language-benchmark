<?php
require __DIR__.'/../app/Support/Database.php';
use App\Support\Database;
function expect($test, $message) { if (!$test) throw new RuntimeException($message); }
if (!str_contains(getenv('DATABASE_URL') ?: '', 'php_production_test')) throw new RuntimeException('dedicated disposable database required');
putenv('DB_PERSISTENT=1');
putenv('DB_SESSION_RESET=1');
$db = new Database;
$pid = (int)$db->query('SELECT pg_backend_pid() AS pid')[0]['pid'];
expect($db->query('SHOW statement_timeout')[0]['statement_timeout'] === '10s', 'initial timeout retained');
$db->query("SET application_name='request-user-one'");
$db->query("SET statement_timeout='1234'");
$db->query('CREATE TEMP TABLE request_secret(id int)');
$db->connection()->beginTransaction();
$db->query('CREATE TABLE abandoned_request(id int)');
$db->release();
$db = new Database;
expect((int)$db->query('SELECT pg_backend_pid() AS pid')[0]['pid'] === $pid, 'persistent physical connection reused across new request objects');
expect($db->query('SHOW application_name')[0]['application_name'] === 'lb-backend', 'per-request settings must not leak');
expect($db->query('SHOW statement_timeout')[0]['statement_timeout'] === '10s', 'reset timeout explicitly restored');
expect($db->query('SELECT ?::int AS value', [42])[0]['value'] === 42, 'native prepared query works after discard');
expect($db->query("SELECT to_regclass('abandoned_request') AS t")[0]['t'] === null, 'abandoned transaction rolled back');
expect($db->query("SELECT to_regclass('request_secret') AS t")[0]['t'] === null, 'temporary data from another user discarded');
try { $db->transaction(function () use ($db) { $db->query('CREATE TABLE failed_request(id int)'); throw new RuntimeException('original failure'); }); }
catch (RuntimeException $e) { expect($e->getMessage() === 'original failure', 'transaction original error retained'); }
expect($db->query("SELECT to_regclass('failed_request') AS t")[0]['t'] === null, 'failed transaction rolled back');
$config = Database::configuration(getenv('DATABASE_URL'), 'local');
$admin = new PDO($config['dsn'], $config['user'], $config['password']);
$admin->exec('SELECT pg_terminate_backend('.$pid.')');
try { $db->query('CREATE TABLE never_replayed(id int)'); throw new RuntimeException('dead write unexpectedly succeeded'); }
catch (PDOException) {}
$db->release();
$db = new Database;
$fresh = (int)$db->query('SELECT pg_backend_pid() AS pid')[0]['pid'];
expect($fresh !== $pid, 'new request recovers lost persistent connection');
expect($db->query("SELECT to_regclass('never_replayed') AS t")[0]['t'] === null, 'disconnected write must never replay');
$db->release();
echo "persistent connection reuse, user isolation, rollback, lost-connection recovery and no write replay passed\n";
