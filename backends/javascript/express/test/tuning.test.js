import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readConfig } from '../src/config.js';
const env = { DATABASE_URL: 'postgresql://local/local', JWT_SECRET: 'x'.repeat(32) };
test('worker configuration preserves the twenty connection budget', () => {
  for (const [value, workers] of [[undefined, 2], ['2', 2], ['4', 4]]) {
    const cfg = readConfig({ ...env, ...(value === undefined ? {} : { WORKERS: value }) });
    assert.equal(cfg.workers, workers);
    assert.equal(cfg.connectionsPerWorker * cfg.workers, 20);
  }
});
test('unsupported and malformed worker values fail startup', () => {
  for (const WORKERS of ['', '0', '1', '3', '5', '4.0', 'oops'])
    assert.throws(() => readConfig({ ...env, WORKERS }), /WORKERS/);
});
