import { test } from "node:test";
import assert from "node:assert/strict";
import jwt from "jsonwebtoken";
import { validateBody, integer, pageParams } from "../src/validation.js";
import { encodeCursor, decodeCursor, stringify } from "../src/cursors.js";
import { createSecurity } from "../src/security.js";

test("credentials reject extras and non-string values and lowercase valid usernames", () => {
  assert.deepEqual(
    validateBody({ username: "Alice", password: "long-password" }, "register"),
    { username: "alice", password: "long-password" },
  );
  for (const body of [
    { username: "Alice", password: "long-password", extra: 1 },
    { username: 123, password: "long-password" },
    { username: " bad", password: "long-password" },
    { username: "Alice", password: "short" },
  ])
    assert.throws(() => validateBody(body, "register"));
  assert.throws(() => validateBody({ content: " \n " }, "post"));
  assert.throws(() => validateBody({ content: "😀".repeat(281) }, "post"));
  assert.equal(
    validateBody({ content: "😀".repeat(280) }, "post").content.length,
    560,
  );
});
test("cursor round trips microsecond timestamps and bigint IDs", () => {
  const stamp = "2026-10-05T02:01:03.123456Z";
  const id = 9007199254740993n;
  assert.deepEqual(decodeCursor(encodeCursor(stamp, id)), [
    stamp.replace("Z", "+00:00"),
    id,
  ]);
  assert.equal(stringify({ id }), ' {"id":9007199254740993}'.trim());
  for (const payload of [
    ["2026-10-05", 1],
    ["2026-99-05T00:00:00+00:00", 1],
    ["2026-10-05T00:00:00+00:00", true],
    ["2026-10-05T00:00:00Z", 0],
  ])
    assert.throws(() =>
      decodeCursor(Buffer.from(JSON.stringify(payload)).toString("base64url")),
    );
  assert.throws(() => decodeCursor("!!!!!"));
  assert.equal(integer("9223372036854775807", "id"), 9223372036854775807n);
  assert.throws(() => integer("oops", "id"));
  assert.throws(() => pageParams({ limit: "101" }));
});
test("PHC hashes use the fixture policy; wrong and unknown passwords do equal work", async () => {
  const security = await createSecurity({
    jwtSecret: "x".repeat(32),
    jwtTtl: 3600,
  });
  const hash = await security.hash("long-password");
  assert.match(hash, /^\$argon2id\$v=19\$m=65536,t=3,p=1\$/);
  assert.equal(await security.verify("long-password", hash), true);
  assert.equal(await security.verify("wrong", hash), false);
  assert.equal(await security.verify("wrong"), false);
});
test("tokens preserve large IDs and reject wrong algorithm, expiry, claims and future iat", async () => {
  const cfg = { jwtSecret: "x".repeat(32), jwtTtl: 3600 };
  const security = await createSecurity(cfg);
  assert.equal(
    security.identity(security.issue(9007199254740993n)),
    9007199254740993n,
  );
  const now = Math.floor(Date.now() / 1000);
  const base = {
    sub: "1",
    iat: now,
    exp: now + 3600,
    iss: "language-benchmark",
    aud: "social-api",
  };
  for (const data of [
    { ...base, exp: now - 1 },
    { ...base, iat: now + 100 },
    { ...base, iat: 1.5 },
    { ...base, sub: 1 },
    { ...base, sub: "0" },
    { ...base, iss: "wrong" },
    { ...base, aud: "wrong" },
  ])
    assert.throws(() => security.identity(jwt.sign(data, cfg.jwtSecret)));
  assert.throws(() =>
    security.identity(jwt.sign(base, cfg.jwtSecret, { algorithm: "HS384" })),
  );
});
test("username validation cannot accept a trailing newline", () => {
  assert.throws(() =>
    validateBody(
      { username: "alice\n", password: "long-password" },
      "register",
    ),
  );
  assert.throws(() =>
    validateBody(
      { username: "alice\r", password: "long-password" },
      "register",
    ),
  );
});
test("cursor ID must be a JSON integer token rather than an integral float", () => {
  for (const token of ["1.0", "1e0", "true", '"1"'])
    assert.throws(() =>
      decodeCursor(
        Buffer.from(`["2026-01-01T00:00:00Z",${token}]`).toString("base64url"),
      ),
    );
});
