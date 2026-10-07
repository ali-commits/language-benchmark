import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import http from 'k6/http';
import * as dataTypes from './lib/data.js';

const actors = new SharedArray('actors', () =>
  dataTypes.actors(JSON.parse(open(__ENV.LOAD_DATA || '/inputs/load-data.json'))),
);
export const options = {
  scenarios: {
    warm: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.WARMUP_RATE || 25),
      timeUnit: '1s',
      duration: (__ENV.WARMUP_SECONDS || 120) + 's',
      preAllocatedVUs: 64,
      maxVUs: 64,
    },
  },
  thresholds: { http_req_failed: ['rate<0.001'] },
};
export default function () {
  const a = actors[exec.scenario.iterationInTest % actors.length];
  http.get(__ENV.BASE_URL + '/api/v1/feed', {
    headers: { Authorization: 'Bearer ' + a.token },
    tags: { name: 'feed' },
  });
}
