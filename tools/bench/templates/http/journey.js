import { sleep } from 'k6';
import http from 'k6/http';

// Module state survives every stage transition for each VU.
let visits = 0;
/** @param {import("./contracts").WorkloadContext} context */
export default function (context) {
  const wait = context.parameters.wait || 0.02;
  if (typeof wait !== 'number') throw new Error('invalid wait parameter');
  visits += 1;
  context.operation(
    'get',
    () =>
      http.get(context.baseUrl + '/?delay=' + (context.parameters.delay || 0), {
        timeout: '2s',
        headers: { 'X-Session-Visit': String(visits) },
      }),
    (response) => response.status === 200,
  );
  sleep(wait);
}
