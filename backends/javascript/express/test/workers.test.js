import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
for (const workers of [2, 4]) test(
  `production launcher keeps ${workers} HTTP workers listening`,
  { skip: !process.env.EXPRESS_TEST_DATABASE_URL },
  async () => {
    const dir = await mkdtemp(join(tmpdir(), "express-workers-"));
    const port = globalThis.Bun ? 58136 : 58135;
    const child = spawn(
      process.execPath,
      [fileURLToPath(new URL("../src/server.js", import.meta.url))],
      {
        env: {
          ...process.env,
          DATABASE_URL: process.env.EXPRESS_TEST_DATABASE_URL,
          JWT_SECRET: "x".repeat(32),
          PORT: String(port),
          RUN_DIR: dir,
          RUN_ID: "local-tuning-test",
          WORKERS: String(workers),
        },
        stdio: "pipe",
      },
    );
    let output = "";
    child.stdout.on("data", (v) => (output += v));
    child.stderr.on("data", (v) => (output += v));
    try {
      for (let i = 0; i < 100; i++) {
        if (
          (await readdir(dir)).filter((x) => /^\d+\.json$/.test(x)).length === workers &&
          (await readdir(dir)).filter((x) => /^tuning-\d+\.json$/.test(x)).length === workers
        )
          break;
        await new Promise((r) => setTimeout(r, 50));
      }
      assert.equal(child.exitCode, null, output);
      assert.equal(
        (await readdir(dir)).filter((x) => /^\d+\.json$/.test(x)).length,
        workers,
        output,
      );
      const names = (await readdir(dir)).filter((x) => x.startsWith("tuning-") && x.endsWith(".json"));
      assert.equal(names.length, workers, output);
      const receipts = await Promise.all(names.map(async (name) => JSON.parse(await readFile(join(dir, name), "utf8"))));
      assert.equal(new Set(receipts.map((r) => r.pid)).size, workers);
      assert.equal(receipts.reduce((sum, r) => sum + r.connections_per_worker, 0), 20);
      for (const receipt of receipts) {
        process.kill(receipt.pid, 0);
        assert.equal(receipt.run_id, "local-tuning-test");
        assert.equal(receipt.workers_total, workers);
        assert.equal(receipt.hash_concurrency, 1);
        assert.equal(receipt.hash_scope, "worker");
        assert.equal(receipt.connections_per_worker, 20 / workers);
        assert.ok(names.includes(`tuning-${receipt.pid}.json`));
      }
      assert.equal(
        (await fetch(`http://127.0.0.1:${port}/health/ready`)).status,
        200,
      );
    } finally {
      child.kill("SIGTERM");
      await new Promise((r) =>
        child.exitCode !== null ? r() : child.once("exit", r),
      );
      await rm(dir, { recursive: true, force: true });
    }
  },
);
