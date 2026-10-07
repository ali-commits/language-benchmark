import { sleep } from 'k6';
import { perform } from './lib/requests.js';

const data = JSON.parse(open(__ENV.LOAD_DATA || '/inputs/load-data.json'));
export const options = {
  vus: Number(__ENV.JOURNEY_VUS || 20),
  duration: __ENV.JOURNEY_DURATION || '300s',
};
export default function () {
  const a = data.actors[(__VU - 1) % data.actors.length];
  perform('feed', a, data.posts[0], null, __ENV.BASE_URL, null, false);
  sleep(Number(__ENV.THINK_SECONDS || 2));
}
