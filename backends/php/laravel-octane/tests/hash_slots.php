<?php

require __DIR__.'/../vendor/autoload.php';
$locks = [];
foreach ([0, 1] as $slot) {
    $f = fopen('/tmp/language-benchmark-hash-'.$slot, 'c');
    if (! flock($f, LOCK_EX | LOCK_NB)) {
        throw new RuntimeException('test requires idle dedicated app');
    }$locks[] = $f;
}
$code = 'require "/app/vendor/autoload.php"; echo App\\Support\\Protocol::password("valid-password-long");';
$process = proc_open([PHP_BINARY, '-r', $code], [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
if (! is_resource($process)) {
    throw new RuntimeException('child start failed');
}
fclose($pipes[0]);
usleep(250000);
if (! proc_get_status($process)['running']) {
    throw new RuntimeException('hash bypassed the two occupied global slots');
}
flock($locks[0], LOCK_UN);
fclose($locks[0]);
$hash = stream_get_contents($pipes[1]);
$error = stream_get_contents($pipes[2]);
fclose($pipes[1]);
fclose($pipes[2]);
$status = proc_close($process);
flock($locks[1], LOCK_UN);
fclose($locks[1]);
if ($status !== 0 || ! preg_match('~^\$argon2id\$v=19\$m=65536,t=3,p=1\$([^$]+)\$([^$]+)$~D', $hash, $m)) {
    throw new RuntimeException('hash policy/child failure: '.$error);
}
if (strlen(base64_decode($m[1])) !== 16 || strlen(base64_decode($m[2])) !== 32) {
    throw new RuntimeException('salt/hash size differs');
}
echo "global two-slot admission and canonical Argon2id PHC policy passed\n";
