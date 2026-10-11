import cluster from "node:cluster";
import { fileURLToPath } from "node:url";
import { readConfig } from "./config.js";
const cfg = readConfig();
// Bun cluster workers listen directly with Linux SO_REUSEPORT. In pinned Bun
// 1.4.2, node:http ignores an explicit reusePort option outside this path.
cluster.setupPrimary({
  exec: fileURLToPath(new URL("./worker.js", import.meta.url)),
});
const children = new Set();
let stopping = false;
function stop(signal = "SIGTERM", code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children) child.process.kill(signal);
}
for (let i = 0; i < cfg.workers; i++) {
  const child = cluster.fork({
    WORKER_INDEX: String(i),
    UV_THREADPOOL_SIZE: String(cfg.uvThreadpoolSize),
  });
  children.add(child);
  child.on("error", () => stop("SIGTERM", 1));
  child.on("exit", (code) => {
    children.delete(child);
    if (!stopping) stop("SIGTERM", code || 1);
    if (!children.size) process.exit(process.exitCode || 0);
  });
}
process.on("SIGTERM", () => stop());
process.on("SIGINT", () => stop("SIGINT"));
