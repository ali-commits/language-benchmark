<?php

require __DIR__.'/../app/Support/Protocol.php';
use App\Support\Protocol;

function ensure(bool $condition, string $message): void
{
    if (! $condition) throw new RuntimeException($message);
}
putenv('BENCH_PROFILE');
putenv('PHP_WORKERS');
ensure(Protocol::workers() === 20, 'legacy workers preserved');
putenv('PHP_WORKERS=32');
ensure(Protocol::workers() === 32, 'production workers');
foreach (['0', '65', '', '-1', '1.5', '032', ' 32', 'secret'] as $value) {
    putenv('PHP_WORKERS='.$value);
    try { Protocol::workers(); throw new RuntimeException('invalid workers accepted'); }
    catch (InvalidArgumentException $e) { ensure($e->getMessage() === 'PHP_WORKERS must be an integer from 1 to 64', 'safe worker error'); }
}
putenv('PHP_WORKERS');
putenv('HASH_CONCURRENCY');
ensure(Protocol::hashConcurrency() === 2, 'legacy default must be two');
putenv('HASH_CONCURRENCY=4');
ensure(Protocol::hashConcurrency() === 4, 'tuned capacity must be four');
foreach (['', '0', '-1', '5', '99999999999999999999', '1.5', ' 4', '4 ', '+4', '04', 'secret-value'] as $value) {
    putenv('HASH_CONCURRENCY='.$value);
    try { Protocol::hashConcurrency(); throw new RuntimeException('invalid capacity accepted'); }
    catch (InvalidArgumentException $e) { ensure($e->getMessage() === 'HASH_CONCURRENCY must be an integer from 1 to 4', 'unsafe error'); }
}
putenv('HASH_CONCURRENCY=4');
putenv('RUN_DIR');
Protocol::publishTuningReceipt();
$directory = sys_get_temp_dir().'/php-tuning-'.bin2hex(random_bytes(8));
mkdir($directory);
putenv('RUN_DIR='.$directory);
putenv('RUN_ID');
try { Protocol::publishTuningReceipt(); throw new RuntimeException('missing run nonce accepted'); }
catch (InvalidArgumentException $e) { ensure($e->getMessage() === 'RUN_ID is required for tuning receipts', 'nonce error'); }
putenv('RUN_ID=test-nonce');
Protocol::publishTuningReceipt();
$receipt = json_decode(file_get_contents($directory.'/tuning-'.getmypid().'.json'), true, 512, JSON_THROW_ON_ERROR);
ensure($receipt === ['schema_version' => 1, 'run_id' => 'test-nonce', 'pid' => getmypid(), 'language' => 'php', 'workers_total' => 20, 'connections_per_worker' => 1, 'pool_budget' => 20, 'cpu_parallelism' => null, 'hash_concurrency' => 4, 'hash_scope' => 'global'], 'receipt must expose only safe effective fields');
ensure(count(glob($directory.'/*')) === 1, 'receipt must leave no temporary file');
putenv('PHP_WORKERS=32');
Protocol::publishTuningReceipt();
$production = json_decode(file_get_contents($directory.'/tuning-'.getmypid().'.json'), true);
ensure($production['workers_total'] === 32 && $production['pool_budget'] === 32, 'production receipt aggregate32');
putenv('BENCH_PROFILE=production4');
Protocol::publishTuningReceipt();
$production = json_decode(file_get_contents($directory.'/tuning-'.getmypid().'.json'), true);
ensure($production['schema_version'] === 2 && $production['workers_total'] === 32, 'production schema2 worker count');
ensure($production['memory_limit'] === ini_get('memory_limit') && $production['opcache_memory'] === (int)ini_get('opcache.memory_consumption'), 'actual ini configuration');
ensure($production['db_connection_reuse'] === 'worker-retained', 'truthful reuse policy');
ensure($production['db_session_reset'] === (getenv('DB_SESSION_RESET') === '1'), 'truthful cleanup policy');
putenv('BENCH_PROFILE');
putenv('PHP_WORKERS');
unlink($directory.'/tuning-'.getmypid().'.json');
rmdir($directory);
putenv('RUN_DIR');
echo "PHP default, tuned capacity, invalid settings and safe receipt passed\n";

require __DIR__.'/../app/Support/RuntimeSettings.php';
use App\Support\RuntimeSettings;
foreach (['PHP_MEMORY_LIMIT', 'OPCACHE_MEMORY', 'OPCACHE_INTERNED_STRINGS', 'DB_PERSISTENT', 'DB_SESSION_RESET', 'RR_MAX_WORKER_MEMORY'] as $key) {
    $original = getenv($key);
    putenv($key.'=invalid-value');
    try { RuntimeSettings::validate(); throw new RuntimeException('invalid runtime control accepted'); }
    catch (InvalidArgumentException $error) { ensure($error->getMessage() === $key.' has an unsupported value', 'safe runtime error'); }
    if ($original === false) putenv($key); else putenv($key.'='.$original);
}
RuntimeSettings::validate();
echo "runtime limits reject invalid input\n";
