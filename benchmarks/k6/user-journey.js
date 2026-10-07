import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import http from 'k6/http';
import { Counter, Gauge, Rate, Trend } from 'k6/metrics';
import * as dataTypes from './lib/data.js';

const cfg = dataTypes.journeyConfig(
  JSON.parse(open(__ENV.SCENARIO_CONFIG || '/inputs/scenario.json')),
);
const actors = new SharedArray('unique journey actors', () =>
  dataTypes.actors(JSON.parse(open(__ENV.LOAD_DATA || '/inputs/load-data.json'))),
);
if (cfg.users < 1 || actors.length < cfg.users || cfg.wait_min < 0 || cfg.wait_max < cfg.wait_min)
  throw new Error('invalid user journey configuration');
const attempted = new Counter('operations_attempted');
const succeeded = new Counter('operations_succeeded');
const failed = new Counter('operations_failed');
const timeouts = new Counter('journey_timeouts');
const resourceErrors = new Counter('generator_resource_errors');
const networkErrors = new Counter('journey_network_errors');
const latency = new Trend('operation_duration', true);
const failure = new Rate('journey_failure_rate');
const authenticated = new Counter('users_authenticated');
const active = new Gauge('journey_active_users');
const journeys = new Counter('user_journeys_completed');
const scenarioStarted = new Gauge('scenario_started_epoch');
const deadline = cfg.ramp_seconds + cfg.settle_seconds;
const successTarget = cfg.success_fraction === undefined ? 0.99 : cfg.success_fraction;
if (!Number.isFinite(successTarget) || successTarget < 0.9 || successTarget > 1)
  throw new Error('invalid operation success target');
const stages = [
  { duration: cfg.ramp_seconds + 's', target: cfg.users },
  ...(cfg.settle_seconds > 0 ? [{ duration: cfg.settle_seconds + 's', target: cfg.users }] : []),
  { duration: cfg.hold_seconds + 's', target: cfg.users },
];
export const options = {
  tags: cfg.tags || {},
  scenarios: {
    users: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages,
      gracefulRampDown: '30s',
      gracefulStop: '30s',
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'p(99.9)', 'max'],
  thresholds: {
    'journey_failure_rate{phase:steady}': [
      'rate<=' + String(Number((1 - successTarget).toFixed(12))),
    ],
  },
  systemTags: ['status', 'method', 'name', 'scenario'],
};
/** @type {string | null} */
let token = null;
let everAuthenticated = false;
let anchorEmitted = false;
let state = (__VU * 2654435761) >>> 0;
function random() {
  state ^= state << 13;
  state ^= state >>> 17;
  state ^= state << 5;
  return (state >>> 0) / 4294967296;
}
function wait() {
  sleep(cfg.wait_min + random() * (cfg.wait_max - cfg.wait_min));
}
function phase() {
  const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
  return elapsed < cfg.ramp_seconds
    ? 'ramp'
    : elapsed < deadline
      ? 'settle'
      : elapsed < deadline + cfg.hold_seconds
        ? 'steady'
        : 'drain';
}
/** @param {string} op @param {string} method @param {string} path @param {unknown} body @param {number} expected */
function request(op, method, path, body, expected) {
  const tags = { operation: op, name: op, phase: phase() };
  active.add(exec.instance.vusActive, tags);
  attempted.add(1, tags);
  const start = Date.now();
  const res = http.request(
    method,
    __ENV.BASE_URL + '/api/v1' + path,
    body === null ? null : JSON.stringify(body),
    {
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: 'Bearer ' + token } : {}),
      },
      tags,
      timeout: cfg.timeout_seconds + 's',
    },
  );
  /** @type {Record<string, unknown> | null} */
  let json = null;
  let ok = res.status === expected;
  if (ok && expected !== 204) {
    try {
      json = dataTypes.record(res.json());
      ok =
        op === 'login'
          ? typeof json.access_token === 'string'
          : op === 'feed' || op === 'comments'
            ? Array.isArray(json.items)
            : Number.isInteger(json.id) && typeof json.content === 'string';
    } catch (_) {
      ok = false;
    }
  }
  const elapsed = Date.now() - start;
  // Slow completed replies count as unresponsive under the explicit per-operation deadline.
  ok = ok && elapsed <= cfg.timeout_seconds * 1000;
  if (res.error_code === 1050 || res.error_code === 1211 || elapsed > cfg.timeout_seconds * 1000)
    timeouts.add(1, tags);
  if (res.status === 0) {
    networkErrors.add(1, { ...tags, error_code: String(res.error_code || 0) });
    if (
      /cannot assign requested address|too many open files|no buffer space|out of memory/i.test(
        res.error || '',
      )
    )
      resourceErrors.add(1, tags);
  }
  latency.add(elapsed, tags);
  failure.add(!ok, tags);
  check(res, { 'valid response within deadline': () => ok }, tags);
  if (ok) succeeded.add(1, tags);
  else failed.add(1, tags);
  if (res.status === 401 && op !== 'login') token = null;
  return { ok, json, tags };
}
export default function () {
  if (__VU === 1 && !anchorEmitted) {
    scenarioStarted.add(exec.scenario.startTime / 1000, { phase: 'ramp' });
    anchorEmitted = true;
  }
  const actor = actors[__VU - 1];
  if (!token) {
    const r = request(
      'login',
      'POST',
      '/auth/login',
      { username: actor.username, password: actor.password },
      200,
    );
    if (!r.ok) {
      wait();
      return;
    }
    token = dataTypes.string(r.json?.access_token);
    if (!everAuthenticated) {
      authenticated.add(1, r.tags);
      everAuthenticated = true;
    }
  }
  const feed = request('feed', 'GET', '/feed?limit=20', null, 200);
  if (!feed.ok) {
    wait();
    return;
  }
  const items = dataTypes.array(feed.json?.items);
  if (items.length === 0) {
    wait();
    return;
  }
  const post = dataTypes.record(items[Math.floor(random() * items.length)]);
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
  journeys.add(1, { phase: phase() });
}
/** @param {unknown} data */
export function handleSummary(data) {
  return { '/output/summary.json': JSON.stringify(data, null, 2) };
}
