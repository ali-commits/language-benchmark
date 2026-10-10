<?php

require __DIR__.'/../app/Support/Protocol.php';

function startHash(string $code = ''): array
{
    $source = 'require '.var_export(__DIR__.'/../app/Support/Protocol.php', true).'; '.$code.' echo App\\Support\\Protocol::password("valid-password-long");';
    $process = proc_open([PHP_BINARY, '-r', $source], [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    if (! is_resource($process)) throw new RuntimeException('child start failed');
    fclose($pipes[0]);
    return [$process, $pipes];
}
function finishHash(array $child): void
{
    [$process, $pipes] = $child;
    $read = [$pipes[1]]; $write = null; $except = null;
    if (stream_select($read, $write, $except, 5) !== 1) {
        proc_terminate($process);
        throw new RuntimeException('hash did not enter the available global slot');
    }
    $hash = stream_get_contents($pipes[1]);
    $error = stream_get_contents($pipes[2]);
    fclose($pipes[1]); fclose($pipes[2]);
    $status = proc_close($process);
    if ($status !== 0 || ! preg_match('~^\$argon2id\$v=19\$m=65536,t=3,p=1\$([^$]+)\$([^$]+)$~D', $hash, $m)) throw new RuntimeException('hash policy/child failure: '.$error);
    if (strlen(base64_decode($m[1])) !== 16 || strlen(base64_decode($m[2])) !== 32) throw new RuntimeException('salt/hash size differs');
}
foreach ([2, 4] as $capacity) {
    putenv('HASH_CONCURRENCY='.$capacity);
    // Every slot is tested as the sole available slot, including tuned slots 2/3.
    for ($available = 0; $available < $capacity; $available++) {
        $locks = [];
        for ($slot = 0; $slot < $capacity; $slot++) {
            $f = fopen('/tmp/language-benchmark-hash-'.$slot, 'c');
            if (! flock($f, LOCK_EX | LOCK_NB)) throw new RuntimeException('test requires idle dedicated container');
            $locks[$slot] = $f;
        }
        $child = startHash();
        usleep(150000);
        if (! proc_get_status($child[0])['running']) throw new RuntimeException('hash bypassed occupied global slots');
        flock($locks[$available], LOCK_UN); fclose($locks[$available]); unset($locks[$available]);
        finishHash($child);
        foreach ($locks as $f) { flock($f, LOCK_UN); fclose($f); }
    }
}
// Hold admitted work at a deterministic barrier to prove four real processes
// enter simultaneously while the fifth waits on the same global flock files.
$children = [];
$source = 'namespace App\\Support; function password_hash(...$args) { echo "admitted\\n"; fflush(STDOUT); fgets(STDIN); return "done"; } require '.var_export(__DIR__.'/../app/Support/Protocol.php', true).'; Protocol::password("x");';
for ($i = 0; $i < 5; $i++) {
    $process = proc_open([PHP_BINARY, '-r', $source], [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    stream_set_blocking($pipes[1], false);
    $children[] = [$process, $pipes];
    if ($i < 4) {
        $deadline = microtime(true) + 3;
        $output = '';
        while (! str_contains($output, "admitted\n") && microtime(true) < $deadline) { $output .= stream_get_contents($pipes[1]); usleep(1000); }
        if (! str_contains($output, "admitted\n")) throw new RuntimeException('four processes could not enter global slots');
    }
}
usleep(150000);
if (stream_get_contents($children[4][1][1]) !== '') throw new RuntimeException('fifth process bypassed global capacity');
// Killing an admitted process releases its kernel-owned flock for the waiter.
proc_terminate($children[0][0]);
foreach ($children[0][1] as $pipe) fclose($pipe);
proc_close($children[0][0]);
$deadline = microtime(true) + 3;
$output = '';
while (! str_contains($output, "admitted\n") && microtime(true) < $deadline) { $output .= stream_get_contents($children[4][1][1]); usleep(1000); }
if (! str_contains($output, "admitted\n")) throw new RuntimeException('killed holder leaked slot');
for ($i = 1; $i < 5; $i++) {
    fwrite($children[$i][1][0], "release\n");
    fclose($children[$i][1][0]); fclose($children[$i][1][1]); fclose($children[$i][1][2]);
    if (proc_close($children[$i][0]) !== 0) throw new RuntimeException('admitted child failed');
}
// Terminating a waiter must neither steal nor leak any permit.
$locks = [];
for ($slot = 0; $slot < 4; $slot++) {
    $f = fopen('/tmp/language-benchmark-hash-'.$slot, 'c');
    if (! flock($f, LOCK_EX | LOCK_NB)) throw new RuntimeException('slot leaked');
    $locks[] = $f;
}
$child = startHash(); usleep(150000); proc_terminate($child[0]);
fclose($child[1][1]); fclose($child[1][2]); proc_close($child[0]);
foreach ($locks as $f) { flock($f, LOCK_UN); fclose($f); }
finishHash(startHash());
// Inject a hashing failure after admission; the real flock must be released.
$code = 'namespace App\\Support; function password_hash(...$args) { throw new \\RuntimeException("injected hash failure"); } require '.var_export(__DIR__.'/../app/Support/Protocol.php', true).'; try { Protocol::password("x"); } catch (\\RuntimeException) {}';
$process = proc_open([PHP_BINARY, '-r', $code], [0 => ['file', '/dev/null', 'r'], 1 => ['file', '/dev/null', 'w'], 2 => ['pipe', 'w']], $pipes);
$error = stream_get_contents($pipes[2]); fclose($pipes[2]);
if (proc_close($process) !== 0) throw new RuntimeException($error);
for ($slot = 0; $slot < 4; $slot++) {
    $f = fopen('/tmp/language-benchmark-hash-'.$slot, 'c');
    if (! flock($f, LOCK_EX | LOCK_NB)) throw new RuntimeException('failure leaked global slot');
    flock($f, LOCK_UN); fclose($f);
}
echo "global two/four-slot admission, fifth waiter, cancellation, failure release and canonical Argon2id policy passed\n";
