<?php

require __DIR__.'/../app/Support/Protocol.php';
use App\Support\Protocol;

function ensure(bool $condition, string $message): void
{
    if (! $condition) throw new RuntimeException($message);
}
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
unlink($directory.'/tuning-'.getmypid().'.json');
rmdir($directory);
putenv('RUN_DIR');
echo "PHP default, tuned capacity, invalid settings and safe receipt passed\n";
