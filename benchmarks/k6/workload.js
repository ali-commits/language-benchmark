import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import * as dataTypes from './lib/data.js';
import { perform } from './lib/requests.js';
import { choice, operation } from './lib/selection.js';

const cfg = dataTypes.capacityConfig(
  JSON.parse(open(__ENV.SCENARIO_CONFIG || '/inputs/scenario.json')),
);
const path = __ENV.LOAD_DATA || '/inputs/load-data.json';
const actors = new SharedArray('actors', () => dataTypes.actors(JSON.parse(open(path))));
const posts = new SharedArray('posts', () => dataTypes.posts(JSON.parse(open(path))));
const pairs = new SharedArray('pairs', () => dataTypes.pairs(JSON.parse(open(path))));
const expiry = cfg.expires_at;
if (!cfg.rate || cfg.rate < 1 || !cfg.vus || cfg.vus < 1 || cfg.duration < 1)
  throw new Error('invalid bounded scenario');
if (expiry * 1000 < Date.now() + (cfg.duration + 60) * 1000)
  throw new Error('tokens expire during run');
if (pairs.length < cfg.rate * cfg.duration + cfg.vus)
  throw new Error('mutation pairs insufficient for offered operations');
export const options = {
  tags: cfg.tags || {},
  scenarios: {
    capacity: {
      executor: 'constant-arrival-rate',
      rate: cfg.rate,
      timeUnit: '1s',
      duration: cfg.duration + 's',
      preAllocatedVUs: cfg.vus,
      maxVUs: cfg.vus,
      gracefulStop: '20s',
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(50)', 'p(95)', 'p(99)', 'max'],
  systemTags: ['status', 'method', 'name', 'scenario'],
  thresholds: {
    operation_duration: cfg.operation === 'login' ? ['p(95)<=500'] : ['p(95)<=200', 'p(99)<=500'],
    operation_failure_rate: ['rate<=0.001'],
    dropped_iterations: ['count==0'],
  },
};
/** @type {[number, number] | null} */
let pendingUnlike = null;
export default function () {
  const i = exec.scenario.iterationInTest;
  const n = choice(i, cfg.seed || 20261004);
  let actor = actors[n % actors.length];
  const post = posts[n % posts.length];
  const op = cfg.operation === 'mixed' ? operation(n) : cfg.operation;
  if (op === 'like') {
    const pair = pendingUnlike || pairs[i];
    actor = actors[pair[0]];
    const removing = !!pendingUnlike;
    const ok = perform(op, actor, post, null, __ENV.BASE_URL, pair[1], removing);
    // Unique initial pairs are never reused for PUT; DELETE only follows a successful PUT in this VU.
    if (ok) pendingUnlike = removing ? null : pair;
  } else perform(op, actor, post, i % 4 === 0 ? actor.cursor : null, __ENV.BASE_URL, null, false);
}
/** @param {unknown} data */
export function handleSummary(data) {
  return { '/output/summary.json': JSON.stringify(data, null, 2) };
}
