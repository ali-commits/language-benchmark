import pg from "pg";
import { readFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { checkAbort, abortError } from "./cancellation.js";
pg.types.setTypeParser(20, (value) => BigInt(value));
pg.types.setTypeParser(1184, (value) => value);
export function createDatabase(cfg) {
  const url = new URL(cfg.databaseUrl),
    sslmode = url.searchParams.get("sslmode");
  let ssl;
  if (sslmode && sslmode !== "disable") {
    ssl = {
      rejectUnauthorized: ["verify-full", "verify-ca"].includes(sslmode),
    };
    if (url.searchParams.has("sslrootcert"))
      ssl.ca = readFileSync(url.searchParams.get("sslrootcert"), "utf8");
  }
  // Connection-string values must not override required benchmark policies.
  for (const key of [
    "sslmode",
    "sslrootcert",
    "sslcert",
    "sslkey",
    "options",
    "application_name",
    "statement_timeout",
  ])
    url.searchParams.delete(key);
  const pool = new pg.Pool({
    connectionString: url.toString(),
    ssl,
    max: 10,
    connectionTimeoutMillis: 5000,
    idleTimeoutMillis: 30000,
    application_name: "lb-backend",
    statement_timeout: 10000,
    options: "-c timezone=UTC -c statement_timeout=10000",
  });
  pool.on("error", (error) =>
    console.error("idle PostgreSQL connection error", error.code || "unknown"),
  );
  let count = 0,
    totalMs = 0;
  async function connection(work, signal) {
    checkAbort(signal);
    const start = performance.now(),
      client = await pool.connect();
    count++;
    totalMs += performance.now() - start;
    let discard = false;
    try {
      checkAbort(signal);
      return await work(client, () => {
        discard = true;
      });
    } finally {
      client.release(discard || Boolean(signal?.aborted));
    }
  }
  async function execute(client, signal, ...args) {
    checkAbort(signal);
    try {
      const result = await client.query(...args);
      checkAbort(signal);
      return result;
    } catch (error) {
      if (signal?.aborted) throw abortError();
      throw error;
    }
  }
  return {
    query: (sql, params = [], signal) =>
      connection((client) => execute(client, signal, sql, params), signal),
    transaction: (work, signal) =>
      connection(async (client, discard) => {
        try {
          await execute(client, signal, "BEGIN");
          const result = await work({
            query: (...args) => execute(client, signal, ...args),
          });
          await execute(client, signal, "COMMIT");
          return result;
        } catch (error) {
          // Rollback uses the underlying lease, independent of request cancellation.
          try {
            await client.query("ROLLBACK");
          } catch {
            discard();
          }
          throw error;
        }
      }, signal),
    metrics: () => ({
      pid: process.pid,
      time: Date.now() / 1000,
      connection_acquires: count,
      connection_acquire_ms: totalMs,
      pool_total: pool.totalCount,
      pool_idle: pool.idleCount,
      pool_waiting: pool.waitingCount,
    }),
    close: () => pool.end(),
  };
}
