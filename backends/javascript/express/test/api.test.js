import { test } from "node:test";
import assert from "node:assert/strict";
import { createApp } from "../src/app.js";
import { createDatabase } from "../src/db.js";
import { createSecurity } from "../src/security.js";

test(
  "real PostgreSQL social journey commits, paginates and preserves idempotency",
  { skip: !process.env.EXPRESS_TEST_DATABASE_URL },
  async () => {
    const url = process.env.EXPRESS_TEST_DATABASE_URL;
    if (!new URL(url).pathname.endsWith("_test"))
      throw Error("dedicated _test database required");
    const db = createDatabase({ databaseUrl: url, mode: "local" });
    const security = await createSecurity({
      jwtSecret: "x".repeat(32),
      jwtTtl: 3600,
    });
    const app = createApp({ db, security, jwtTtl: 3600 });
    const server = app.listen(0, "127.0.0.1");
    await new Promise((resolve) => server.once("listening", resolve));
    const root = `http://127.0.0.1:${server.address().port}`;
    const suffix = Date.now().toString();
    async function call(path, method = "GET", body, token) {
      const response = await fetch(root + path, {
        method,
        headers: {
          ...(body ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      return [
        response.status,
        response.status === 204 ? null : await response.json(),
        response,
      ];
    }
    try {
      const [s1, a] = await call("/api/v1/auth/register", "POST", {
        username: "a" + suffix,
        password: "long-password",
      });
      assert.equal(s1, 201);
      const [s2, b] = await call("/api/v1/auth/register", "POST", {
        username: "b" + suffix,
        password: "long-password",
      });
      assert.equal(s2, 201);
      const [s3, auth] = await call("/api/v1/auth/login", "POST", {
        username: ("a" + suffix).toUpperCase(),
        password: "long-password",
      });
      assert.equal(s3, 200);
      const token = auth.access_token;
      assert.equal(
        (await call("/api/v1/users/me", "GET", undefined, token))[1].id,
        a.id,
      );
      const other = security.issue(BigInt(b.id));
      const [sp, p] = await call(
        "/api/v1/posts",
        "POST",
        { content: "Hello" },
        other,
      );
      assert.equal(sp, 201);
      assert.equal(
        (await call("/api/v1/feed", "GET", undefined, token))[1].items.length,
        0,
      );
      for (let i = 0; i < 2; i++)
        assert.equal(
          (
            await call(`/api/v1/users/${b.id}/follow`, "PUT", undefined, token)
          )[0],
          204,
        );
      assert.equal(
        (await call("/api/v1/feed", "GET", undefined, token))[1].items[0].id,
        p.id,
      );
      for (let i = 0; i < 2; i++)
        assert.equal(
          (
            await call(`/api/v1/posts/${p.id}/like`, "PUT", undefined, token)
          )[0],
          204,
        );
      const detail = (
        await call(`/api/v1/posts/${p.id}`, "GET", undefined, token)
      )[1];
      assert.equal(detail.like_count, 1);
      assert.equal(detail.liked, true);
      const [sc, c] = await call(
        `/api/v1/posts/${p.id}/comments`,
        "POST",
        { content: "Reply" },
        token,
      );
      assert.equal(sc, 201);
      assert.equal(
        (
          await call(`/api/v1/posts/${p.id}/comments`, "GET", undefined, token)
        )[1].items[0].id,
        c.id,
      );
      assert.equal(
        (
          await call(`/api/v1/users/${a.id}/follow`, "PUT", undefined, token)
        )[0],
        422,
      );
      assert.equal(
        (
          await call(
            "/api/v1/posts/9223372036854775807",
            "GET",
            undefined,
            token,
          )
        )[0],
        404,
      );
      assert.equal(
        (
          await call("/api/v1/posts", "POST", { content: "x", extra: 1 }, token)
        )[0],
        422,
      );
      assert.equal(
        (await call("/api/v1/feed?cursor=bad", "GET", undefined, token))[0],
        422,
      );
      const [tooBig] = await call(
        "/api/v1/posts",
        "POST",
        { content: "x".repeat(17000) },
        token,
      );
      assert.equal(tooBig, 413);
      assert.equal((await call("/health/ready"))[0], 200);
      assert.equal((await call("/api/v1/not-a-route"))[0], 404);
      assert.equal((await call("/api/v1/auth/login", "GET"))[0], 405);
      const metrics = (await call("/health/metrics"))[1];
      assert.ok(metrics.connection_acquires > 0);
      assert.ok(metrics.connection_acquire_ms >= 0);
    } finally {
      await new Promise((resolve) => server.close(resolve));
      await db.close();
    }
  },
);
