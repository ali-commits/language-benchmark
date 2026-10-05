import { test } from "node:test";
import assert from "node:assert/strict";
import { HashQueue } from "../src/hash_queue.js";
import { decodeCursor } from "../src/cursors.js";
import { createApp } from "../src/app.js";
import { createSecurity } from "../src/security.js";
import { createDatabase } from "../src/db.js";
import { pageParams } from "../src/validation.js";
function gate() {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}
const tick = () => new Promise((r) => setTimeout(r, 0));

test("aborted pending hash never runs and frees its bounded queue slot", async () => {
  const q = new HashQueue(1),
    hold = gate(),
    abort = new AbortController();
  let started = 0;
  const active = q.run(() => hold.promise);
  await tick();
  const cancelled = q.run(() => {
    started++;
    return true;
  }, abort.signal);
  const rejected = assert.rejects(
    Promise.race([
      cancelled,
      new Promise((_, reject) =>
        setTimeout(() => reject(Error("cancelled hash remained queued")), 100),
      ),
    ]),
    { name: "AbortError" },
  );
  abort.abort();
  try {
    await rejected;
  } finally {
    hold.resolve("active");
  }
  const next = q.run(() => ++started);
  hold.resolve("active");
  assert.equal(await active, "active");
  assert.equal(await next, 1);
  assert.equal(started, 1);
});
test("overflow is bounded and already-aborted work does not run", async () => {
  const q = new HashQueue(1),
    hold = gate();
  const active = q.run(() => hold.promise);
  await tick();
  const queued = q.run(() => 2);
  const overflow = q.run(() => 3);
  const rejected = assert.rejects(overflow, (error) => error.status === 503);
  const abort = new AbortController();
  abort.abort();
  const cancelled = assert.rejects(
    q.run(() => 4, abort.signal),
    { name: "AbortError" },
  );
  hold.resolve(1);
  await rejected;
  await cancelled;
  assert.equal(await active, 1);
  assert.equal(await queued, 2);
});
test("cursor calendar and clock validation rejects coercible invalid dates", () => {
  for (const stamp of [
    "0000-01-01T00:00:00Z",
    "2026-02-30T00:00:00Z",
    "2026-01-01T24:00:00Z",
    "2026-01-01T00:00:60Z",
    "2026-01-01T00:00:00+24:00",
  ]) {
    assert.throws(
      () =>
        decodeCursor(
          Buffer.from(JSON.stringify([stamp, 1])).toString("base64url"),
        ),
      stamp,
    );
  }
  assert.equal(
    decodeCursor(
      Buffer.from('["0004-02-29T00:00:00.123456Z",1]').toString("base64url"),
    )[0],
    "0004-02-29T00:00:00.123456Z",
  );
});
test("duplicate limits use last value and empty limits are invalid", () => {
  assert.equal(pageParams({ limit: ["1", "2"] }).limit, 2);
  assert.throws(
    () => pageParams({ limit: "" }),
    (error) => error.detail[0].type === "int_parsing",
  );
});
test("invalid UTF8 is 400 and empty body is required-body 422", async () => {
  const app = createApp({ db: null, security: null, jwtTtl: 3600 });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((r) => server.once("listening", r));
  const url = `http://127.0.0.1:${server.address().port}/api/v1/auth/login`;
  try {
    const invalid = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: Buffer.from([0xff]),
    });
    assert.equal(invalid.status, 400);
    assert.deepEqual(await invalid.json(), {
      detail: "There was an error parsing the body",
    });
    const empty = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "",
    });
    assert.equal(empty.status, 422);
    assert.deepEqual(await empty.json(), {
      detail: [
        { type: "missing", loc: ["body"], msg: "Field required", input: null },
      ],
    });
  } finally {
    await new Promise((r) => server.close(r));
  }
});
test(
  "database statement deadline and application identity are enforced",
  { skip: !process.env.EXPRESS_TEST_DATABASE_URL, timeout: 15000 },
  async () => {
    const db = createDatabase({
      databaseUrl:
        process.env.EXPRESS_TEST_DATABASE_URL +
        "?application_name=wrong&options=-c%20statement_timeout%3D0&statement_timeout=0",
      mode: "local",
    });
    try {
      const { rows } = await db.query(
        "SELECT current_setting('application_name') AS app,current_setting('statement_timeout') AS timeout",
      );
      assert.equal(rows[0].app, "lb-backend");
      assert.equal(rows[0].timeout, "10s");
      const start = Date.now();
      await assert.rejects(
        db.query("SELECT pg_sleep(11)"),
        (error) => error.code === "57014",
      );
      assert.ok(Date.now() - start < 12000);
      assert.equal((await db.query("SELECT 1 AS n")).rows[0].n, 1);
    } finally {
      await db.close();
    }
  },
);
test(
  "aborted transactions roll back and discard connection before reuse",
  { skip: !process.env.EXPRESS_TEST_DATABASE_URL },
  async () => {
    const db = createDatabase({
      databaseUrl: process.env.EXPRESS_TEST_DATABASE_URL,
      mode: "local",
    });
    const abort = new AbortController();
    let pid;
    try {
      await assert.rejects(
        db.transaction(async (client) => {
          pid = (await client.query("SELECT pg_backend_pid() AS pid")).rows[0]
            .pid;
          await client.query(
            "CREATE TEMP TABLE cancelled_hash_test(x integer)",
          );
          await client.query("INSERT INTO cancelled_hash_test VALUES(1)");
          setTimeout(() => abort.abort(), 20);
          await client.query("SELECT pg_sleep(0.2)");
        }, abort.signal),
        { name: "AbortError" },
      );
      const { rows } = await db.query(
        "SELECT pg_backend_pid() AS pid,to_regclass('pg_temp.cancelled_hash_test') AS temp",
      );
      assert.notEqual(rows[0].pid, pid);
      assert.equal(rows[0].temp, null);
    } finally {
      await db.close();
    }
  },
);
test(
  "HTTP response close aborts password work before account insertion",
  { skip: !process.env.EXPRESS_TEST_DATABASE_URL },
  async () => {
    const db = createDatabase({
      databaseUrl: process.env.EXPRESS_TEST_DATABASE_URL,
      mode: "local",
    });
    const security = await createSecurity({
      jwtSecret: "x".repeat(32),
      jwtTtl: 3600,
    });
    const started = gate(),
      originalHash = security.hash;
    let signal;
    security.hash = (password, s) => {
      signal = s;
      started.resolve();
      return originalHash(password, s);
    };
    const app = createApp({ db, security, jwtTtl: 3600 });
    const server = app.listen(0, "127.0.0.1");
    await new Promise((r) => server.once("listening", r));
    const username = "cancelled_" + Date.now(),
      abort = new AbortController();
    try {
      const pending = fetch(
        `http://127.0.0.1:${server.address().port}/api/v1/auth/register`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password: "long-password" }),
          signal: abort.signal,
        },
      );
      const rejected = assert.rejects(pending, { name: "AbortError" });
      await started.promise;
      abort.abort();
      await rejected;
      await new Promise((r) => setTimeout(r, 300));
      assert.equal(signal?.aborted, true);
      assert.equal(
        (await db.query("SELECT id FROM users WHERE username=$1", [username]))
          .rows.length,
        0,
      );
    } finally {
      await new Promise((r) => server.close(r));
      await db.close();
    }
  },
);
