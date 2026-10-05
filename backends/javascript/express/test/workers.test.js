import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
test(
  "production launcher keeps both HTTP workers listening",
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
          (await readdir(dir)).filter((x) => x.endsWith(".json")).length === 2
        )
          break;
        await new Promise((r) => setTimeout(r, 50));
      }
      assert.equal(child.exitCode, null, output);
      assert.equal(
        (await readdir(dir)).filter((x) => x.endsWith(".json")).length,
        2,
        output,
      );
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
