import http from 'k6/http';
export const options = {
  scenarios: {
    calibration: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || 10000),
      timeUnit: '1s',
      duration: '60s',
      preAllocatedVUs: 1000,
      maxVUs: 1000,
    },
  },
  thresholds: { dropped_iterations: ['count==0'], http_req_failed: ['rate<0.001'] },
};
export default function () {
  http.get(__ENV.CALIBRATION_URL, { tags: { name: 'calibration' } });
}
