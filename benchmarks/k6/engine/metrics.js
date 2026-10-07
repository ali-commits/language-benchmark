// Every JSON payload is an independently parseable event in --log-format raw.
/** @param {Record<string, unknown>} event */
export function emit(event) {
  console.log('BENCH_EVENT ' + JSON.stringify(event));
}

/** @param {import("./contracts").Stage[]} stages @param {number} seconds */
export function stageAt(stages, seconds) {
  return (
    stages.find((stage) => seconds >= stage.start_seconds && seconds < stage.end_seconds) || null
  );
}

/** @param {string} metric @param {number} timestamp @param {import("./contracts").MetricFields} fields */
export function sample(metric, timestamp, fields) {
  emit({
    type: 'sample',
    sample: Object.assign(
      { metric, timestamp, scope: 'workload', kind: 'event', includes_failures: true },
      fields,
    ),
  });
}

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** @param {unknown} value @returns {value is import('./contracts').JsonValue} */
function isJson(value) {
  return (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'boolean' ||
    (typeof value === 'number' && Number.isFinite(value)) ||
    (Array.isArray(value)
      ? value.every(isJson)
      : isRecord(value) && Object.values(value).every(isJson))
  );
}

/** @param {unknown} value @returns {value is import('./contracts').Stage} */
function isStage(value) {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    ['duration', 'start_seconds', 'end_seconds', 'from_users', 'to_users'].every(
      (key) => typeof value[key] === 'number' && Number.isFinite(value[key]),
    ) &&
    (value.evaluate === undefined || typeof value.evaluate === 'boolean')
  );
}

/** @param {unknown} value @returns {import('./contracts').EngineConfig} */
export function engineConfig(value) {
  if (
    !isRecord(value) ||
    typeof value.baseUrl !== 'string' ||
    !Array.isArray(value.stages) ||
    !value.stages.every(isStage)
  )
    throw new Error('invalid resolved configuration');
  const parameters = value.parameters ?? {};
  if (!isRecord(parameters) || !isJson(parameters)) throw new Error('invalid parameters');
  const { gracefulRampDown, drain, telemetryInterval } = value;
  if (
    (gracefulRampDown !== undefined && typeof gracefulRampDown !== 'number') ||
    (drain !== undefined && typeof drain !== 'number') ||
    (telemetryInterval !== undefined && typeof telemetryInterval !== 'number')
  )
    throw new Error('invalid timing');
  return {
    baseUrl: value.baseUrl,
    stages: value.stages,
    parameters,
    gracefulRampDown,
    drain,
    telemetryInterval,
  };
}
