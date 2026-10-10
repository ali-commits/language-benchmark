<?php

// Run inside a dedicated serving container with RUN_DIR/RUN_ID configured.
for ($i = 0; $i < 100; $i++) {
    $response = json_decode(file_get_contents('http://127.0.0.1:8000/health/ready'), true, 512, JSON_THROW_ON_ERROR);
    if (($response['status'] ?? null) !== 'ok') throw new RuntimeException('readiness failed');
}
$files = glob(getenv('RUN_DIR').'/tuning-*.json');
if (count($files) !== 20) throw new RuntimeException('expected twenty initialized serving workers');
$workers = [];
foreach (glob('/proc/[0-9]*/cmdline') as $file) {
    $command = file_get_contents($file);
    if (str_starts_with($command, 'php-fpm: pool www') || str_contains($command, "\0/app/vendor/bin/roadrunner-worker\0")) {
        $workers[] = (int) basename(dirname($file));
    }
}
$pids = [];
foreach ($files as $file) {
    $receipt = json_decode(file_get_contents($file), true, 512, JSON_THROW_ON_ERROR);
    $expected = ['schema_version' => 1, 'run_id' => getenv('RUN_ID'), 'pid' => $receipt['pid'], 'language' => 'php', 'workers_total' => 20, 'connections_per_worker' => 1, 'pool_budget' => 20, 'cpu_parallelism' => null, 'hash_concurrency' => (int) getenv('HASH_CONCURRENCY'), 'hash_scope' => 'global'];
    if ($receipt !== $expected || basename($file) !== 'tuning-'.$receipt['pid'].'.json') throw new RuntimeException('invalid effective process receipt');
    $pids[] = $receipt['pid'];
}
sort($pids); sort($workers);
if ($pids !== $workers) throw new RuntimeException('receipt PIDs differ from actual serving workers');
echo "20 live serving workers, effective hash capacity and safe nonce receipts verified\n";
