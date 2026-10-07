"""Execute both journeys to catch changes to historical pacing, mix and validation."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("bun") is None, reason="requires Bun")


def test_confirmation_replays_historical_journey():
    result = subprocess.run(
        [
            "bun",
            "-e",
            r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as dataTypes from './benchmarks/k6/lib/data.js';
const target = 'benchmarks/k6/engine/social-confirmation.js';
assert.ok(fs.existsSync(target), 'confirmation workload must exist');
function replay(path, vu, fault = '') {
  const trace = [], outcomes = [], authenticated = [];
  let now = 0, faultUsed = false;
  const cfg = {users: 1000, wait_min: 3, wait_max: 10, timeout_seconds: 5,
    ramp_seconds: 300, settle_seconds: 0, hold_seconds: 120};
  const actors = Array.from({length: 1000}, (_, i) => ({username: `actor${i+1}`, password: 'test'}));
  class Metric {
    constructor(name) {this.name = name;}
    add(value, tags) {
      if (this.name === 'journey_failure_rate') outcomes.push([tags.operation, !value]);
      if (this.name === 'users_authenticated') authenticated.push(value);
    }
  }
  const execution = {vu: {idInTest: vu}, scenario: {startTime: 0}, instance: {vusActive: 1000}};
  const http = {request(method, url, body, options) {
    trace.push(['request', method, url, body, options.timeout, options.headers]);
    const login = url.endsWith('/auth/login');
    const write = method === 'POST' && !login;
    let status = login ? 200 : write ? 201 : method === 'GET' ? 200 : 204;
    let json = login ? {access_token: 'token'} : write ? {id: 1, content: 'ok'} :
      {items: [{id: 11, liked: false}, {id: 22, liked: true}, {id: 33, liked: false}]};
    const operation = login ? 'login' : url.endsWith('/like') ? 'like' :
      url.endsWith('/comments') ? (method === 'GET' ? 'comments' : 'comment') :
      write ? 'post' : 'feed';
    const hit = !faultUsed && (fault.startsWith(operation + '-') ||
      (fault === 'empty-feed' && operation === 'feed'));
    if (hit) faultUsed = true;
    now += hit && fault.endsWith('slow') ? 5001 : hit && fault.endsWith('deadline') ? 5000 : 1;
    if (hit && fault.endsWith('401')) status = 401;
    if (hit && fault.endsWith('shape')) json = {};
    if (write && fault === 'write-shape') json = {id: 1};
    if (hit && fault === 'empty-feed') json = {items: []};
    return {status, error_code: 0, json() {
      if (hit && fault.endsWith('json')) throw new Error('malformed JSON');
      return json;
    }};
  }};
  const sandbox = {
    __VU: vu, __ENV: {BASE_URL: 'http://test'}, Date: {now: () => now},
    sleep(seconds) {trace.push(['wait', seconds]); now += seconds * 1000;},
    SharedArray: function(_, load) {return load();}, execution, exec: execution, http,
    Counter: Metric, Gauge: Metric, Rate: Metric, Trend: Metric, dataTypes,
    check() {}, open(path) {return JSON.stringify(path.includes('scenario') ? cfg : {actors});},
  };
  const context = vm.createContext(sandbox);
  let source = fs.readFileSync(path, 'utf8').replace(/^import .*;\n/gm, '')
    .replace('export default function', 'function journey').replace(/export /g, '');
  vm.runInContext(source + ';globalThis.run = journey;', context);
  const workloadContext = {
    baseUrl: 'http://test', parameters: cfg,
    stage: {id: 'hold'},
    operation(name, callback, success) {
      const response = callback(); outcomes.push([name, success(response)]); return response;
    },
    record(name, fields) {if (name === 'authenticated_account') authenticated.push(fields.value);},
  };
  for (let i = 0; i < 200; i++) context.run(workloadContext);
  return JSON.parse(JSON.stringify({trace, outcomes, authenticated}));
}
for (const vu of [1, 2, 1000]) {
  for (const fault of ['', 'login-json', 'login-shape', 'login-slow', 'login-401', 'login-deadline',
    'feed-json', 'feed-shape', 'feed-slow', 'feed-401', 'feed-deadline', 'write-shape', 'empty-feed',
    'like-401', 'comment-401', 'comment-json', 'comment-slow', 'post-json', 'post-shape',
    'comments-json', 'comments-shape']) {
    const old = replay('benchmarks/k6/user-journey.js', vu, fault);
    const actual = replay(target, vu, fault);
    assert.deepEqual(actual, old, `VU ${vu}, fault ${fault}`);
    assert.ok(actual.trace.some(row => row[0] === 'wait' && row[1] >= 3 && row[1] <= 10));
    if (!fault) {
      const requests = actual.trace.filter(row => row[0] === 'request');
      assert.ok(requests.some(row => row[1] === 'DELETE'));
      assert.ok(requests.some(row => row[2].endsWith('/posts')));
      assert.ok(requests.some(row => row[1] === 'GET' && row[2].endsWith('/comments')));
      assert.equal(JSON.parse(requests[0][3]).username, `actor${vu}`);
      assert.equal(actual.authenticated.length, 1);
    }
  }
}
""",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
