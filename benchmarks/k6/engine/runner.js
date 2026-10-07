import execution from 'k6/execution';
import { sleep } from 'k6';
// Lifecycle replaces this literal with a relative path to the prepared entry,
// preserving the workload's original directory and all declared dependencies.
import workload from './workload.js';
import { emit, sample, stageAt } from './metrics.js';

const config = JSON.parse(__ENV.BENCH_CONFIG);
const stages = config.stages;
if (!Array.isArray(stages) || !stages.length) throw new Error('stages required');
let previous = 0;
const ids = new Set();
for (const stage of stages) {
  if (typeof stage.id !== 'string' || ids.has(stage.id) || !Number.isFinite(stage.duration) || stage.duration <= 0 || stage.start_seconds !== previous || Math.abs(stage.end_seconds - previous - stage.duration) > 1e-8 || !Number.isInteger(stage.from_users) || stage.from_users < 0 || !Number.isInteger(stage.to_users) || stage.to_users < 0) throw new Error('invalid resolved stages');
  if (ids.size && stage.from_users !== stages[ids.size - 1].to_users) throw new Error('discontinuous users');
  ids.add(stage.id);
  previous = stage.end_seconds;
}
const total = previous;
const grace = config.gracefulRampDown === undefined ? 5 : config.gracefulRampDown;
const drain = config.drain === undefined ? 30 : config.drain;
const interval = config.telemetryInterval === undefined ? 0.1 : config.telemetryInterval;
if (![grace, drain, interval].every(Number.isFinite) || grace < 0 || drain < 0 || interval <= 0) throw new Error('invalid timing');
const duration = seconds => `${Math.round(seconds * 1000)}ms`;
export const options = {
  scenarios: {
    load: {executor: 'ramping-vus', exec: 'journey', startVUs: stages[0].from_users,
      stages: stages.map(stage => ({duration: duration(stage.duration), target: stage.to_users})),
      gracefulRampDown: duration(grace), gracefulStop: duration(drain)},
    telemetry: {executor: 'constant-vus', exec: 'telemetry', vus: 1,
      duration: duration(total + Math.max(drain, grace)), gracefulStop: '0s'},
  },
};

// Both scenarios start at offset zero. Scenario startTime is the same absolute
// load origin, independent of init/setup wall time or per-VU iteration delays.
function timestamp() { return (Date.now() - execution.scenario.startTime) / 1000; }
let metadataSent = false;
export function telemetry() {
  const seconds = timestamp();
  if (!metadataSent) {
    emit({type: 'metadata', version: 1, load_start_epoch_ms: execution.scenario.startTime,
      stages, graceful_ramp_down_seconds: grace, drain_seconds: drain,
      telemetry_interval_seconds: interval, occupancy_excludes_monitor_vus: 1});
    metadataSent = true;
  }
  const stage = stageAt(stages, seconds);
  const requested = stage ? stage.from_users + (stage.to_users - stage.from_users) * (seconds - stage.start_seconds) / stage.duration : 0;
  sample('active_users', seconds, {scope: 'generator', kind: 'gauge', value: Math.max(0, execution.instance.vusActive - 1),
    unit: 'count', max_gap_seconds: interval * 3, collection_interval: interval,
    provider_fields: {stage: stage ? stage.id : 'drain', requested_users: requested}});
  sleep(interval);
}

export function journey() {
  const started = timestamp();
  const stage = stageAt(stages, started);
  // A gracefully draining VU may finish an existing journey, but never starts
  // new application work outside the scheduled load timeline.
  if (!stage || (stage.from_users === 0 && stage.to_users === 0)) { sleep(interval); return; }
  const journeyId = `${execution.vu.idInTest}:${execution.vu.iterationInScenario}`;
  const identity = {journey_id: journeyId, vu: execution.vu.idInTest, iteration: execution.vu.iterationInScenario};
  emit(Object.assign({type: 'journey_start', timestamp: started, stage: stage.id}, identity));
  let operationCount = 0;
  const context = {
    baseUrl: config.baseUrl, parameters: config.parameters || {}, stage,
    operation(name, callback, success = result => Boolean(result)) {
      const operationId = journeyId + ':' + operationCount++;
      const begin = timestamp();
      const startStage = stageAt(stages, begin);
      emit(Object.assign({type: 'operation_start', operation_id: operationId, operation: name,
        timestamp: begin, start_stage: startStage ? startStage.id : 'drain'}, identity));
      let result;
      let passed = false;
      try { result = callback(); passed = Boolean(success(result)); return result; }
      finally {
        const end = timestamp();
        const completion = stageAt(stages, end);
        const fields = Object.assign({operation_id: operationId, operation: name, start_seconds: begin,
          start_stage: startStage ? startStage.id : 'drain', completion_stage: completion ? completion.id : 'drain', success: passed}, identity);
        emit(Object.assign({type: 'operation_end', timestamp: end, latency_ms: (end - begin) * 1000}, fields));
        sample('latency', end, {value: (end - begin) * 1000, unit: 'ms', provider_fields: fields});
        sample('success', end, {kind: 'rate', numerator: passed ? 1 : 0, denominator: 1, unit: 'fraction', provider_fields: fields});
      }
    },
    record(metric, fields) { sample(metric, timestamp(), Object.assign({}, fields, {provider_fields: Object.assign({}, fields.provider_fields || {}, identity)})); },
  };
  try { workload(context); }
  catch (error) { emit(Object.assign({type: 'journey_error', timestamp: timestamp()}, identity)); }
  finally { emit(Object.assign({type: 'journey_end', timestamp: timestamp()}, identity)); }
}
