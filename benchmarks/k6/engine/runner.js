import { sleep } from 'k6';
import execution from 'k6/execution';
import { emit, engineConfig, sample, stageAt } from './metrics.js';
// Lifecycle replaces this literal with a relative path to the prepared entry,
// preserving the workload's original directory and all declared dependencies.
import workload from './workload.js';

const config = engineConfig(JSON.parse(__ENV.BENCH_CONFIG));
const stages = config.stages;
if (!Array.isArray(stages) || !stages.length) throw new Error('stages required');
let previous = 0;
const ids = new Set();
for (const stage of stages) {
  if (
    typeof stage.id !== 'string' ||
    ids.has(stage.id) ||
    !Number.isFinite(stage.duration) ||
    stage.duration <= 0 ||
    stage.start_seconds !== previous ||
    Math.abs(stage.end_seconds - previous - stage.duration) > 1e-8 ||
    !Number.isInteger(stage.from_users) ||
    stage.from_users < 0 ||
    !Number.isInteger(stage.to_users) ||
    stage.to_users < 0
  )
    throw new Error('invalid resolved stages');
  if (ids.size && stage.from_users !== stages[ids.size - 1].to_users)
    throw new Error('discontinuous users');
  ids.add(stage.id);
  previous = stage.end_seconds;
}
const total = previous;
const grace = config.gracefulRampDown === undefined ? 5 : config.gracefulRampDown;
const drain = config.drain === undefined ? 30 : config.drain;
const interval = config.telemetryInterval === undefined ? 0.1 : config.telemetryInterval;
if (![grace, drain, interval].every(Number.isFinite) || grace < 0 || drain < 0 || interval <= 0)
  throw new Error('invalid timing');
/** @param {number} seconds */
const duration = (seconds) => {
  if (Math.abs(seconds * 1000 - Math.round(seconds * 1000)) > 1e-7)
    throw new Error('durations require exact millisecond precision');
  return `${Math.round(seconds * 1000)}ms`;
};
export const options = {
  teardownTimeout: duration(total + 30),
  scenarios: {
    load: {
      executor: 'ramping-vus',
      exec: 'journey',
      startVUs: stages[0].from_users,
      stages: stages.map((stage) => ({
        duration: duration(stage.duration),
        target: stage.to_users,
      })),
      gracefulRampDown: duration(grace),
      gracefulStop: duration(drain),
    },
  },
};

// The single scenario owns all VU IDs. Native k6 `vus` gauge points (JSON
// output, once per second) provide independent occupancy, including idle.
function timestamp() {
  return (Date.now() - execution.scenario.startTime) / 1000;
}
let metadataSent = false;
function metadata() {
  if (execution.vu.idInTest === 1 && !metadataSent) {
    emit({
      type: 'metadata',
      version: 1,
      load_start_epoch_ms: execution.scenario.startTime,
      stages,
      graceful_ramp_down_seconds: grace,
      drain_seconds: drain,
      telemetry_interval_seconds: 1,
      occupancy_excludes_monitor_vus: 0,
      occupancy_provider: 'k6_native_vus',
      observation_tail_seconds: 1,
    });
    metadataSent = true;
  }
}

export function journey() {
  metadata();
  const started = timestamp();
  const stage = stageAt(stages, started);
  // A gracefully draining VU may finish an existing journey, but never starts
  // new application work outside the scheduled load timeline.
  if (!stage || (stage.from_users === 0 && stage.to_users === 0)) {
    sleep(interval);
    return;
  }
  const journeyId = `${execution.vu.idInTest}:${execution.vu.iterationInScenario}`;
  const identity = {
    journey_id: journeyId,
    vu: execution.vu.idInTest,
    iteration: execution.vu.iterationInScenario,
  };
  emit(Object.assign({ type: 'journey_start', timestamp: started, stage: stage.id }, identity));
  let operationCount = 0;
  /** @type {import("./contracts").WorkloadContext} */
  const context = {
    baseUrl: config.baseUrl,
    parameters: config.parameters || {},
    stage,
    operation(name, callback, success = (result) => Boolean(result)) {
      const operationId = journeyId + ':' + operationCount++;
      const begin = timestamp();
      const startStage = stageAt(stages, begin);
      emit(
        Object.assign(
          {
            type: 'operation_start',
            operation_id: operationId,
            operation: name,
            timestamp: begin,
            start_stage: startStage ? startStage.id : 'drain',
          },
          identity,
        ),
      );
      let result;
      let passed = false;
      try {
        result = callback();
        passed = Boolean(success(result));
        return result;
      } finally {
        const end = timestamp();
        const completion = stageAt(stages, end);
        const fields = Object.assign(
          {
            operation_id: operationId,
            operation: name,
            start_seconds: begin,
            start_stage: startStage ? startStage.id : 'drain',
            completion_stage: completion ? completion.id : 'drain',
            success: passed,
          },
          identity,
        );
        emit(
          Object.assign(
            { type: 'operation_end', timestamp: end, latency_ms: (end - begin) * 1000 },
            fields,
          ),
        );
        sample('latency', end, {
          value: (end - begin) * 1000,
          unit: 'ms',
          provider_fields: fields,
        });
        sample('success', end, {
          kind: 'rate',
          numerator: passed ? 1 : 0,
          denominator: 1,
          unit: 'fraction',
          provider_fields: fields,
        });
      }
    },
    record(metric, fields) {
      sample(
        metric,
        timestamp(),
        Object.assign({}, fields, {
          provider_fields: Object.assign({}, fields.provider_fields || {}, identity),
        }),
      );
    },
  };
  try {
    workload(context);
  } catch {
    emit(Object.assign({ type: 'journey_error', timestamp: timestamp() }, identity));
  } finally {
    emit(Object.assign({ type: 'journey_end', timestamp: timestamp() }, identity));
  }
}

// k6 can finish ramping-vus once the last active VU retires, before trailing
// zero-user stages end. Keep the process alive for that declared observation
// window. The native gauge reads actual zero active VUs during teardown.
export function setup() {
  return { observationDeadline: Date.now() + (total + 1) * 1000 };
}
/** @param {{observationDeadline: number}} data */
export function teardown(data) {
  const remaining = (data.observationDeadline - Date.now()) / 1000;
  if (remaining > 0) sleep(remaining);
}
