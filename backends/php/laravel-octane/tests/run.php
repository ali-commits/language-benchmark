<?php

require __DIR__.'/../vendor/autoload.php';
use App\Support\Database;
use App\Support\InputError;
use App\Support\Protocol;

function check($condition)
{
    if (! $condition) {
        throw new RuntimeException('Assertion failed');
    }
}
$secret = str_repeat('s', 32);
$token = Protocol::issue(9223372036854775806, $secret, 3600, 1000);
check(Protocol::identity($token, $secret, 1001) === 9223372036854775806);
try {
    Protocol::identity($token, $secret, 5000);
    throw new RuntimeException('expired accepted');
} catch (InvalidArgumentException) {
}
$cursor = Protocol::cursor('2026-10-05T01:02:03.123456+00:00', 9223372036854775806);
check(Protocol::decodeCursor($cursor)[1] === 9223372036854775806);
check(Protocol::validate(['username' => 'Test_User', 'password' => 'twelve-chars-password'], 'register')['username'] === 'test_user');
foreach ([['content' => 3], ['content' => '  '], ['content' => 'ok', 'extra' => true]] as $body) {
    try {
        Protocol::validate($body, 'post');
        throw new RuntimeException('invalid accepted');
    } catch (InputError) {
    }
}
// config and optional JWT claims regressions

$config = Database::configuration('postgresql://u%2Bser:literal+and%2Bencoded@localhost/example', 'local');
check($config['user'] === 'u+ser' && $config['password'] === 'literal+and+encoded');
check(str_contains($config['dsn'], 'application_name=lb-backend') && str_contains($config['dsn'], 'statement_timeout=10000'));
foreach (['sslmode=disable&note=sslmode=verify-full&sslrootcert=/ca', 'sslmode=verify-full&sslmode=disable&sslrootcert=/ca', 'sslmode=verify-full&sslrootcert='] as $query) {
    try {
        Database::configuration('postgresql://u:p@localhost/db?'.$query, 'aws');
        throw new RuntimeException('invalid TLS accepted');
    } catch (InvalidArgumentException) {
    }
}
$head = Protocol::b64('{"alg":"HS256"}');
$claims = ['sub' => '1', 'iat' => 1000, 'exp' => 4600, 'iss' => 'language-benchmark', 'aud' => ['other', 'social-api'], 'nbf' => 999];
$sign = function ($claims) use ($head, $secret) {
    $b = Protocol::b64(json_encode($claims));

    return "$head.$b.".Protocol::b64(hash_hmac('sha256', "$head.$b", $secret, true));
};
check(Protocol::identity($sign($claims), $secret, 1001) === 1);
$claims['nbf'] = '999';
check(Protocol::identity($sign($claims), $secret, 1001) === 1);
$claims['nbf'] = 1500;
try {
    Protocol::identity($sign($claims), $secret, 1001);
    throw new RuntimeException('future nbf accepted');
} catch (InvalidArgumentException) {
}

$claims['nbf'] = 999;
$claims['aud'] = (object) ['0' => 'social-api'];
try {
    Protocol::identity($sign($claims), $secret, 1001);
    throw new RuntimeException('object audience accepted');
} catch (InvalidArgumentException) {
}
echo "PHP protocol and configuration tests passed\n";
