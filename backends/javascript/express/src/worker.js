import { mkdir, writeFile, rename } from "node:fs/promises";
import { join } from "node:path";
import { readConfig } from "./config.js";
import { createDatabase } from "./db.js";
import { createSecurity } from "./security.js";
import { createApp } from "./app.js";
const cfg = readConfig(),
  db = createDatabase(cfg);
const security = await createSecurity(cfg);
await db.query("SELECT 1");
const app = createApp({ db, security, jwtTtl: cfg.jwtTtl });
const server = app.listen({ port: cfg.port, host: cfg.bindIp });
server.on("error", (error) => {
  console.error("server startup failed", error.code);
  process.exit(1);
});
async function publish(suffix, value) {
  if (!cfg.runDir) return;
  await mkdir(cfg.runDir, { recursive: true });
  const path = join(cfg.runDir, `${process.pid}.${suffix}`),
    tmp = path + ".tmp";
  await writeFile(tmp, JSON.stringify(value));
  await rename(tmp, path);
}
server.once("listening", async () => {
  await publish("json", {
    pid: process.pid,
    run_id: cfg.runId,
    language: globalThis.Bun ? "bun" : "node",
    version: globalThis.Bun?.version || process.versions.node,
    workers: cfg.workers,
    worker_index: Number(process.env.WORKER_INDEX),
    pool_max: db.poolMax,
    dependencies: { express: "5.2.1", pg: "8.23.1", argon2: "0.45.1" },
  });
  if (cfg.runDir) {
    const path = join(cfg.runDir, `tuning-${process.pid}.json`);
    const tuning = {
      schema_version: 1, run_id: cfg.runId, pid: process.pid,
      language: globalThis.Bun ? "bun" : "node",
      workers_total: cfg.workers, connections_per_worker: db.poolMax,
      pool_budget: cfg.workers * db.poolMax, cpu_parallelism: null,
      hash_concurrency: security.hashConcurrency, hash_scope: "worker",
    };
    if (process.env.BENCH_PROFILE === "production4") {
      tuning.schema_version = 2;
      tuning.worker_mode = "cluster";
      tuning.libuv_threads = Number(process.env.UV_THREADPOOL_SIZE);
    }
    await writeFile(path + ".tmp", JSON.stringify(tuning));
    await rename(path + ".tmp", path);
  }
  console.log(`worker ${process.pid} ready on ${cfg.bindIp}:${cfg.port}`);
});
let publishing = false;
const timer = setInterval(async () => {
  if (publishing) return;
  publishing = true;
  try {
    await publish("metrics", db.metrics());
  } catch (error) {
    console.error("metrics publication failed", error.code);
  } finally {
    publishing = false;
  }
}, 2000);
timer.unref();
let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  clearInterval(timer);
  const timeout = setTimeout(() => process.exit(1), 30000);
  timeout.unref();
  await new Promise((resolve) => server.close(resolve));
  await db.close();
  process.exit(0);
}
process.on("SIGTERM", close);
process.on("SIGINT", close);
