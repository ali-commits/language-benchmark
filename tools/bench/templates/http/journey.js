import { sleep } from 'k6';
import http from 'k6/http';

// Module state survives every stage transition for each VU.
let visits = 0;
export default function (context) {
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
  sleep(context.parameters.wait || 0.02);
}
