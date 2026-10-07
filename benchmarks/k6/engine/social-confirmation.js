// Historical user-journey mix and PRNG, adapted to the engine's all-operation metrics.
// Declare actors.json ({actors: [{username, password}]}) as a workload dependency.
import { sleep } from 'k6';
import { SharedArray } from 'k6/data';
import execution from 'k6/execution';
import http from 'k6/http';

/** @param {unknown} value @returns {Record<string, unknown>} */
function record(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new Error('expected object');
  return Object.fromEntries(Object.entries(value));
}
/** @param {unknown} value @returns {string} */
function string(value) {
  if (typeof value !== 'string') throw new Error('expected string');
  return value;
}
/** @param {unknown} value @returns {number} */
function number(value) {
  if (typeof value !== 'number' || !Number.isFinite(value))
    throw new Error('expected finite number');
  return value;
}
/** @param {unknown} value @returns {unknown[]} */
function array(value) {
  if (!Array.isArray(value)) throw new Error('expected array');
  return value;
}
const actors = new SharedArray('confirmation actors', () =>
  array(record(JSON.parse(open('./actors.json'))).actors).map((item) => {
    const actor = record(item);
    return { username: string(actor.username), password: string(actor.password) };
  }),
);
/** @type {string | null} */
let token = null;
let everAuthenticated = false;
let state = (__VU * 2654435761) >>> 0;
function random() {
  state ^= state << 13;
  state ^= state >>> 17;
  state ^= state << 5;
  return (state >>> 0) / 4294967296;
}

/** @param {import('./contracts').WorkloadContext} context */
export default function (context) {
  // One load scenario owns all VUs, preserving one distinct account per VU.
  const index = execution.vu.idInTest - 1;
  const actor = actors[index];
  if (!actor) throw new Error('missing confirmation actor');
  const waitMin = number(context.parameters.wait_min ?? 3);
  const waitMax = number(context.parameters.wait_max ?? 10);
  const timeoutSeconds = number(context.parameters.timeout_seconds ?? 5);
  if (waitMin < 0 || waitMax < waitMin || timeoutSeconds <= 0)
    throw new Error('invalid confirmation timing');
  function wait() {
    sleep(waitMin + random() * (waitMax - waitMin));
  }
  /** @param {string} op @param {string} method @param {string} path @param {unknown} body @param {number} expected */
  function request(op, method, path, body, expected) {
    return context.operation(
      op,
      () => {
        const start = Date.now();
        const res = http.request(
          method,
          context.baseUrl + '/api/v1' + path,
          body === null ? null : JSON.stringify(body),
          {
            headers: {
              'Content-Type': 'application/json',
              ...(token ? { Authorization: 'Bearer ' + token } : {}),
            },
            timeout: timeoutSeconds + 's',
          },
        );
        /** @type {Record<string, unknown> | null} */
        let json = null;
        let ok = res.status === expected;
        if (ok && expected !== 204) {
          try {
            json = record(res.json());
            ok =
              op === 'login'
                ? typeof json.access_token === 'string'
                : op === 'feed' || op === 'comments'
                  ? Array.isArray(json.items)
                  : Number.isInteger(json.id) && typeof json.content === 'string';
          } catch {
            ok = false;
          }
        }
        ok = ok && Date.now() - start <= timeoutSeconds * 1000;
        if (res.status === 401 && op !== 'login') token = null;
        return { ok, json };
      },
      (result) => result.ok,
    );
  }
  if (!token) {
    const login = request(
      'login',
      'POST',
      '/auth/login',
      { username: actor.username, password: actor.password },
      200,
    );
    if (!login.ok) {
      wait();
      return;
    }
    token = string(login.json?.access_token);
    if (!everAuthenticated) {
      context.record('authenticated_account', {
        value: 1,
        unit: 'count',
        provider_fields: { account: index + 1 },
      });
      everAuthenticated = true;
    }
  }
  const feed = request('feed', 'GET', '/feed?limit=20', null, 200);
  if (!feed.ok) {
    wait();
    return;
  }
  const items = array(feed.json?.items);
  if (items.length === 0) {
    wait();
    return;
  }
  const post = record(items[Math.floor(random() * items.length)]);
  wait();
  request(
    post.liked ? 'unlike' : 'like',
    post.liked ? 'DELETE' : 'PUT',
    '/posts/' + post.id + '/like',
    null,
    204,
  );
  wait();
  if (random() < 0.2) request('comments', 'GET', '/posts/' + post.id + '/comments', null, 200);
  request(
    'comment',
    'POST',
    '/posts/' + post.id + '/comments',
    { content: 'A reply from ' + actor.username },
    201,
  );
  wait();
  if (random() < 0.1) {
    request('post', 'POST', '/posts', { content: 'An update from ' + actor.username }, 201);
    wait();
  }
}
