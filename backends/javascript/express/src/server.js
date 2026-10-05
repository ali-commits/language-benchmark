import cluster from "node:cluster";
import { fileURLToPath } from "node:url";
import { readConfig } from "./config.js";
readConfig();
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
for (let i = 0; i < 2; i++) {
  const child = cluster.fork({
    WORKER_INDEX: String(i),
    UV_THREADPOOL_SIZE: "2",
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
