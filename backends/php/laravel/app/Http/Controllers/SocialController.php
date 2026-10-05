<?php

namespace App\Http\Controllers;

use App\Support\Database;
use App\Support\InputError;
use App\Support\Protocol;
use Illuminate\Http\Exceptions\HttpResponseException;
use Illuminate\Http\Request;

final class SocialController
{
    private const POST = 'SELECT p.id,p.content,p.created_at,p.author_id,u.username,(SELECT count(*) FROM likes l WHERE l.post_id=p.id) AS like_count,(SELECT count(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=?) AS liked FROM posts p JOIN users u ON u.id=p.author_id';

    private function error(int $code, string $detail, bool $bearer = false): never
    {
        throw new HttpResponseException(response()->json(['detail' => $detail], $code, $bearer ? ['WWW-Authenticate' => 'Bearer'] : []));
    }

    private function exists(Database $db, string $table, int $id): void
    {
        if (! $db->query("SELECT 1 FROM $table WHERE id=?", [$id])) {
            $this->error(404, 'not found');
        }
    }

    private function item(array $row): array
    {
        $row['id'] = (int) $row['id'];
        $row['author'] = ['id' => (int) $row['author_id'], 'username' => $row['username']];
        unset($row['author_id'],$row['username']);
        $row['created_at'] = (new \DateTimeImmutable($row['created_at']))->setTimezone(new \DateTimeZone('UTC'))->format('Y-m-d\TH:i:s.u\Z');
        if (isset($row['like_count'])) {
            $row['like_count'] = (int) $row['like_count'];
            $row['comment_count'] = (int) $row['comment_count'];
            $row['liked'] = in_array($row['liked'], [true, 't', '1', 1], true);
        }

        return $row;
    }

    private function id(string $s, string $field): int
    {
        if (! preg_match('/^[+-]?\d+$/D', $s) || filter_var($s, FILTER_VALIDATE_INT) === false) {
            throw new InputError([['type' => 'int_parsing', 'loc' => ['path', $field], 'msg' => 'Input should be a valid integer, unable to parse string as an integer', 'input' => $s]]);
        }

        return (int) $s;
    }

    private function page(array $rows, int $limit): array
    {
        $more = count($rows) > $limit;
        $rows = array_map($this->item(...), array_slice($rows, 0, $limit));

        return ['items' => $rows, 'next_cursor' => $more ? Protocol::cursor($rows[count($rows) - 1]['created_at'], $rows[count($rows) - 1]['id']) : null];
    }

    public function __invoke(Request $request)
    {
        $secret = getenv('JWT_SECRET') ?: '';
        if (strlen($secret) < 32) {
            throw new \RuntimeException('JWT_SECRET must have at least 32 bytes');
        }$ttl = (int) (getenv('JWT_TTL_SECONDS') ?: 3600);
        if ($ttl < 600 || $ttl > 86400) {
            throw new \RuntimeException('invalid JWT_TTL_SECONDS');
        }
        $method = $request->method();
        $path = '/'.$request->path();
        $db = new Database;
        if (in_array($method, ['POST', 'PUT', 'PATCH']) && strlen($request->getContent()) > 16384) {
            $this->error(413, 'request body too large');
        }
        if ($path === '/health/live') {
            return ['status' => 'ok'];
        }
        if ($path === '/health/ready') {
            $db->query('SELECT 1');

            return ['status' => 'ok'];
        }
        if ($path === '/health/metrics') {
            return Database::metrics();
        }
        $body = function (string $kind) use ($request) {
            if ($request->getContent() === '') {
                throw new InputError([['type' => 'missing', 'loc' => ['body'], 'msg' => 'Field required', 'input' => null]]);
            }
            try {
                $v = json_decode($request->getContent(), false, 512, JSON_THROW_ON_ERROR);
            } catch (\JsonException $e) {
                if($e->getCode()===JSON_ERROR_UTF8)$this->error(400,'There was an error parsing the body');
                throw new InputError([['type' => 'json_invalid', 'loc' => ['body', 0], 'msg' => 'JSON decode error', 'input' => new \stdClass, 'ctx' => ['error' => 'Expecting value']]]);
            }if ($v === null) {
                throw new InputError([['type' => 'missing', 'loc' => ['body'], 'msg' => 'Field required', 'input' => null]]);
            }
            if (! ($v instanceof \stdClass)) {
                throw new InputError([['type' => 'model_attributes_type', 'loc' => ['body'], 'msg' => 'Input should be a valid dictionary or object to extract fields from', 'input' => $v]]);
            }

            return Protocol::validate(get_object_vars($v), $kind);
        };
        if ($method === 'POST' && $path === '/api/v1/auth/register') {
            $v = $body('register');
            $hash = Protocol::password($v['password']);
            try {
                $user = $db->query('INSERT INTO users(username,password_hash) VALUES (?,?) RETURNING id,username', [$v['username'], $hash])[0];
                $user['id'] = (int) $user['id'];

                return response()->json($user, 201);
            } catch (\PDOException $e) {
                if ($e->getCode() === '23505') {
                    $this->error(409, 'username already exists');
                }throw $e;
            }
        }
        if ($method === 'POST' && $path === '/api/v1/auth/login') {
            $v = $body('login');
            $row = $db->query('SELECT id,password_hash FROM users WHERE username=?', [$v['username']])[0] ?? null;
            $db->release();
            // A fixed PHC has the same policy/work as all seeded accounts.
            $dummy = '$argon2id$v=19$m=65536,t=3,p=1$MDEyMzQ1Njc4OWFiY2RlZg$6LZzQxzV/D4kfEE2uvT2MhHRKaCWGjBE36GO9JsDk3k';
            $valid = Protocol::password($v['password'], $row['password_hash'] ?? $dummy);
            if (! $valid || $row === null) {
                $this->error(401, 'invalid credentials', true);
            }

            return ['access_token' => Protocol::issue((int) $row['id'], $secret, $ttl, time()), 'token_type' => 'bearer', 'expires_in' => $ttl];
        }
        try {
            $auth = $request->header('Authorization') ?? '';
            if (! preg_match('/^Bearer\s+(.+)$/iD', $auth, $matches)) {
                throw new \InvalidArgumentException;
            }$viewer = Protocol::identity($matches[1], $secret, time());
        } catch (\InvalidArgumentException) {
            $this->error(401, 'invalid credentials', true);
        }
        if ($path === '/api/v1/users/me' && $method === 'GET') {
            $r = $db->query('SELECT id,username FROM users WHERE id=?', [$viewer])[0] ?? null;
            if (! $r) {
                $this->error(401, 'invalid credentials');
            }$r['id'] = (int) $r['id'];

            return $r;
        }
        if ($path === '/api/v1/posts' && $method === 'POST') {
            $v = $body('post');
            $r = $db->transaction(function () use ($db, $viewer, $v) {
                $id = $db->query('INSERT INTO posts(author_id,content) VALUES (?,?) RETURNING id', [$viewer, $v['content']])[0]['id'];

                return $this->item($db->query(self::POST.' WHERE p.id=?', [$viewer, $id])[0]);
            });

            return response()->json($r, 201);
        }
        if (preg_match('~^/api/v1/posts/([^/]+)(?:/(like|comments))?$~D', $path, $m)) {
            $post = $this->id($m[1], 'post_id');
            $operation = $m[2] ?? '';
            if ($operation === '' && $method === 'GET') {
                $r = $db->query(self::POST.' WHERE p.id=?', [$viewer, $post])[0] ?? null;
                if (! $r) {
                    $this->error(404, 'not found');
                }

                return $this->item($r);
            }
            if ($operation === 'like' && in_array($method, ['PUT', 'DELETE'])) {
                $db->transaction(function () use ($db, $post, $viewer, $method) {
                    $this->exists($db, 'posts', $post);
                    $db->query($method === 'PUT' ? 'INSERT INTO likes(post_id,user_id) VALUES (?,?) ON CONFLICT DO NOTHING' : 'DELETE FROM likes WHERE post_id=? AND user_id=?', [$post, $viewer]);
                });

                return response('', 204);
            }
            if ($operation === 'comments' && $method === 'POST') {
                $v = $body('comment');
                $r = $db->transaction(function () use ($db, $post, $viewer, $v) {
                    $this->exists($db, 'posts', $post);

                    return $this->item($db->query('WITH inserted AS (INSERT INTO comments(post_id,author_id,content) VALUES (?,?,?) RETURNING *) SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM inserted c JOIN users u ON u.id=c.author_id', [$post, $viewer, $v['content']])[0]);
                });

                return response()->json($r, 201);
            }
        }
        if (preg_match('~^/api/v1/users/([^/]+)/follow$~D', $path, $m) && in_array($method, ['PUT', 'DELETE'])) {
            $target = $this->id($m[1], 'target_id');
            if ($target === $viewer) {
                $this->error(422, 'cannot follow yourself');
            }$db->transaction(function () use ($db, $target, $viewer, $method) {
                $this->exists($db, 'users', $target);
                $db->query($method === 'PUT' ? 'INSERT INTO follows(follower_id,followed_id) VALUES (?,?) ON CONFLICT DO NOTHING' : 'DELETE FROM follows WHERE follower_id=? AND followed_id=?', [$viewer, $target]);
            });

            return response('', 204);
        }
        if ($method === 'GET' && ($path === '/api/v1/feed' || isset($operation) && $operation === 'comments')) {
            $raw = $request->query('limit', '20');
            if (! is_string($raw) || ! preg_match('/^[+-]?\d+$/D', $raw)) {
                throw new InputError([['type' => 'int_parsing', 'loc' => ['query', 'limit'], 'msg' => 'Input should be a valid integer, unable to parse string as an integer', 'input' => $raw]]);
            }$limit = (int) $raw;
            if ($limit < 1 || $limit > 100) {
                throw new InputError([['type' => $limit < 1 ? 'greater_than_equal' : 'less_than_equal', 'loc' => ['query', 'limit'], 'msg' => $limit < 1 ? 'Input should be greater than or equal to 1' : 'Input should be less than or equal to 100', 'input' => $raw, 'ctx' => [$limit < 1 ? 'ge' : 'le' => $limit < 1 ? 1 : 100]]]);
            }
            try {
                [$stamp,$id] = Protocol::decodeCursor($request->query('cursor'));
            } catch (\InvalidArgumentException) {
                $this->error(422, 'invalid cursor');
            }
            if ($path === '/api/v1/feed') {
                $rows = $db->query(self::POST.' WHERE p.author_id IN (SELECT followed_id FROM follows WHERE follower_id=? UNION ALL SELECT CAST(? AS bigint)) AND (CAST(? AS timestamptz) IS NULL OR (p.created_at,p.id)<(CAST(? AS timestamptz),CAST(? AS bigint))) ORDER BY p.created_at DESC,p.id DESC LIMIT ?', [$viewer, $viewer, $viewer, $stamp, $stamp, $id, $limit + 1]);
            } else {
                $this->exists($db, 'posts', $post);
                $rows = $db->query('SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM comments c JOIN users u ON u.id=c.author_id WHERE c.post_id=? AND (CAST(? AS timestamptz) IS NULL OR (c.created_at,c.id)<(CAST(? AS timestamptz),CAST(? AS bigint))) ORDER BY c.created_at DESC,c.id DESC LIMIT ?', [$post, $stamp, $stamp, $id, $limit + 1]);
            }

            return $this->page($rows, $limit);
        }
        $this->error(404, 'Not Found');
    }
}
