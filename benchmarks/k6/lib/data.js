/** @param {unknown} value @returns {Record<string, unknown>} */
export function record(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('expected JSON object');
  }
  return Object.fromEntries(Object.entries(value));
}

/** @param {unknown} value @returns {number} */
export function number(value) {
  if (typeof value !== 'number' || !Number.isFinite(value))
    throw new Error('expected finite number');
  return value;
}

/** @param {unknown} value @returns {string} */
export function string(value) {
  if (typeof value !== 'string') throw new Error('expected string');
  return value;
}

/** @param {unknown} value @returns {unknown[]} */
export function array(value) {
  if (!Array.isArray(value)) throw new Error('expected array');
  return value;
}

/** @typedef {{username: string, password: string, token?: string, cursor?: string | null}} Actor */
/** @param {unknown} value @returns {Actor[]} */
export function actors(value) {
  return array(record(value).actors).map((item) => {
    const row = record(item);
    return {
      username: string(row.username),
      password: string(row.password),
      token: row.token === undefined ? undefined : string(row.token),
      cursor: row.cursor === undefined || row.cursor === null ? row.cursor : string(row.cursor),
    };
  });
}

/** @param {unknown} value @returns {number[]} */
export function posts(value) {
  return array(record(value).posts).map(number);
}

/** @param {unknown} value @returns {[number, number][]} */
export function pairs(value) {
  return array(record(value).like_pairs).map((item) => {
    const row = array(item);
    if (row.length !== 2) throw new Error('expected actor/post pair');
    return [number(row[0]), number(row[1])];
  });
}

/** @param {unknown} value @returns {Record<string, string>} */
function tags(value) {
  return Object.fromEntries(
    Object.entries(record(value ?? {})).map(([key, item]) => [key, string(item)]),
  );
}

/** @param {unknown} value */
export function journeyConfig(value) {
  const row = record(value);
  return {
    users: number(row.users),
    wait_min: number(row.wait_min),
    wait_max: number(row.wait_max),
    ramp_seconds: number(row.ramp_seconds),
    settle_seconds: number(row.settle_seconds),
    hold_seconds: number(row.hold_seconds),
    timeout_seconds: number(row.timeout_seconds),
    success_fraction: row.success_fraction === undefined ? undefined : number(row.success_fraction),
    tags: tags(row.tags),
  };
}

/** @param {unknown} value */
export function capacityConfig(value) {
  const row = record(value);
  return {
    rate: number(row.rate),
    vus: number(row.vus),
    duration: number(row.duration),
    expires_at: number(row.expires_at),
    operation: string(row.operation),
    seed: row.seed === undefined ? undefined : number(row.seed),
    tags: tags(row.tags),
  };
}
