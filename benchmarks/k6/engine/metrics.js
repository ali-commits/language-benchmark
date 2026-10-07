// Every JSON payload is an independently parseable event in --log-format raw.
export function emit(event) {
  console.log('BENCH_EVENT ' + JSON.stringify(event));
}

export function stageAt(stages, seconds) {
  return stages.find(stage => seconds >= stage.start_seconds && seconds < stage.end_seconds) || null;
}

export function sample(metric, timestamp, fields) {
  emit({type: 'sample', sample: Object.assign({metric, timestamp, scope: 'workload', kind: 'event', includes_failures: true}, fields)});
}
