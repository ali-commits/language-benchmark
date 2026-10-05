import express from "express";
import { HttpError, validateBody, integer, pageParams } from "./validation.js";
import { stringify } from "./cursors.js";
import { POST_SELECT, FEED, COMMENTS, item, page } from "./queries.js";
export function createApp({ db, security, jwtTtl }) {
  const app = express();
  app.disable("x-powered-by");
  app.disable("etag");
  function send(res, status, value) {
    res.status(status).type("application/json").send(stringify(value));
  }
  app.use((req, res, next) => {
    const controller = new AbortController();
    req.operationSignal = controller.signal;
    const abort = () => controller.abort();
    req.once("aborted", abort);
    res.once("close", () => {
      if (!res.writableEnded) abort();
      req.off("aborted", abort);
    });
    req.db = {
      query: (sql, params) => db.query(sql, params, controller.signal),
      transaction: (work) => db.transaction(work, controller.signal),
    };
    next();
  });
  // Express/raw enforces the same cap for content-length and chunked streams.
  app.use((req, res, next) => {
    if (["POST", "PUT", "PATCH"].includes(req.method))
      express.raw({ type: () => true, limit: 16384, inflate: false })(
        req,
        res,
        next,
      );
    else next();
  });
  const input = (req, kind) => {
    const raw = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
    if (raw.length === 0) return validateBody(null, kind);
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
    } catch {
      throw new HttpError(400, "There was an error parsing the body");
    }
    let body;
    try {
      const type = req.headers["content-type"];
      if (type && !/^application\/(?:[\w.+-]*\+)?json\b/i.test(type))
        throw Error("content type");
      body = JSON.parse(text);
    } catch {
      throw new HttpError(422, [
        { loc: ["body"], msg: "Invalid JSON", type: "json_invalid" },
      ]);
    }
    return validateBody(body, kind);
  };
  const identity = (req, res, next) => {
    try {
      const match = /^Bearer\s+(.+)$/i.exec(req.headers.authorization || "");
      if (!match) throw security.unauthorized();
      req.userId = security.identity(match[1]);
      next();
    } catch (error) {
      next(error);
    }
  };
  async function exists(client, table, id) {
    if (
      !(await client.query(`SELECT 1 FROM ${table} WHERE id=$1`, [id])).rowCount
    )
      throw new HttpError(404, "not found");
  }
  app.get("/health/live", (req, res) => send(res, 200, { status: "ok" }));
  app.get("/health/ready", async (req, res) => {
    await req.db.query("SELECT 1");
    send(res, 200, { status: "ok" });
  });
  app.get("/health/metrics", (req, res) => send(res, 200, db.metrics()));
  app.post("/api/v1/auth/register", async (req, res) => {
    const body = input(req, "register");
    const encoded = await security.hash(body.password, req.operationSignal);
    try {
      const result = await req.db.query(
        "INSERT INTO users(username,password_hash) VALUES ($1,$2) RETURNING id,username",
        [body.username, encoded],
      );
      send(res, 201, result.rows[0]);
    } catch (error) {
      if (error.code === "23505")
        throw new HttpError(409, "username already exists");
      throw error;
    }
  });
  app.post("/api/v1/auth/login", async (req, res) => {
    const body = input(req, "login");
    const result = await req.db.query(
      "SELECT id,password_hash FROM users WHERE username=$1",
      [body.username],
    );
    const user = result.rows[0];
    // query() releases the connection before the CPU-bound password operation.
    const valid = await security.verify(
      body.password,
      user?.password_hash,
      req.operationSignal,
    );
    if (!valid || !user) throw security.unauthorized();
    send(res, 200, {
      access_token: security.issue(user.id),
      token_type: "bearer",
      expires_in: jwtTtl,
    });
  });

  app.get("/api/v1/users/me", identity, async (req, res) => {
    const { rows } = await req.db.query(
      "SELECT id,username FROM users WHERE id=$1",
      [req.userId],
    );
    if (!rows[0]) throw new HttpError(401, "invalid credentials");
    send(res, 200, rows[0]);
  });
  app.post("/api/v1/posts", identity, async (req, res) => {
    const body = input(req, "post");
    const result = await req.db.transaction(async (client) => {
      const inserted = await client.query(
        "INSERT INTO posts(author_id,content) VALUES($1,$2) RETURNING id",
        [req.userId, body.content],
      );
      return (
        await client.query(POST_SELECT + " WHERE p.id=$2", [
          req.userId,
          inserted.rows[0].id,
        ])
      ).rows[0];
    });
    send(res, 201, item(result));
  });
  app.get("/api/v1/posts/:post_id", identity, async (req, res) => {
    const id = integer(req.params.post_id, "post_id");
    const { rows } = await req.db.query(POST_SELECT + " WHERE p.id=$2", [
      req.userId,
      id,
    ]);
    if (!rows[0]) throw new HttpError(404, "not found");
    send(res, 200, item(rows[0]));
  });
  app.get("/api/v1/feed", identity, async (req, res) => {
    const p = pageParams(req.query);
    const { rows } = await req.db.query(FEED, [
      req.userId,
      p.stamp,
      p.id,
      p.limit + 1,
    ]);
    send(res, 200, page(rows, p.limit));
  });
  for (const method of ["put", "delete"]) {
    app[method](
      "/api/v1/users/:target_id/follow",
      identity,
      async (req, res) => {
        const id = integer(req.params.target_id, "target_id");
        if (id === req.userId)
          throw new HttpError(422, "cannot follow yourself");
        await req.db.transaction(async (client) => {
          await exists(client, "users", id);
          await client.query(
            method === "put"
              ? "INSERT INTO follows(follower_id,followed_id) VALUES($1,$2) ON CONFLICT DO NOTHING"
              : "DELETE FROM follows WHERE follower_id=$1 AND followed_id=$2",
            [req.userId, id],
          );
        });
        res.status(204).end();
      },
    );
    app[method]("/api/v1/posts/:post_id/like", identity, async (req, res) => {
      const id = integer(req.params.post_id, "post_id");
      await req.db.transaction(async (client) => {
        await exists(client, "posts", id);
        await client.query(
          method === "put"
            ? "INSERT INTO likes(post_id,user_id) VALUES($1,$2) ON CONFLICT DO NOTHING"
            : "DELETE FROM likes WHERE post_id=$1 AND user_id=$2",
          [id, req.userId],
        );
      });
      res.status(204).end();
    });
  }
  app.post("/api/v1/posts/:post_id/comments", identity, async (req, res) => {
    const id = integer(req.params.post_id, "post_id"),
      body = input(req, "comment");
    const result = await req.db.transaction(async (client) => {
      await exists(client, "posts", id);
      return (
        await client.query(
          `WITH inserted AS(INSERT INTO comments(post_id,author_id,content) VALUES($1,$2,$3) RETURNING *) SELECT c.id,c.content,to_char(c.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at,c.author_id,u.username FROM inserted c JOIN users u ON u.id=c.author_id`,
          [id, req.userId, body.content],
        )
      ).rows[0];
    });
    send(res, 201, item(result));
  });
  app.get("/api/v1/posts/:post_id/comments", identity, async (req, res) => {
    const id = integer(req.params.post_id, "post_id"),
      p = pageParams(req.query);
    const rows = await req.db.transaction(async (client) => {
      await exists(client, "posts", id);
      return (await client.query(COMMENTS, [id, p.stamp, p.id, p.limit + 1]))
        .rows;
    });
    send(res, 200, page(rows, p.limit));
  });
  const allowedRoutes = [
    [/^\/api\/v1\/auth\/(register|login)$/, "POST"],
    [/^\/api\/v1\/users\/me$/, "GET"],
    [/^\/api\/v1\/posts$/, "POST"],
    [/^\/api\/v1\/posts\/[^/]+$/, "GET"],
    [/^\/api\/v1\/feed$/, "GET"],
    [/^\/api\/v1\/users\/[^/]+\/follow$/, "PUT, DELETE"],
    [/^\/api\/v1\/posts\/[^/]+\/like$/, "PUT, DELETE"],
    [/^\/api\/v1\/posts\/[^/]+\/comments$/, "POST, GET"],
    [/^\/health\/(live|ready|metrics)$/, "GET"],
  ];
  app.use((req, res) => {
    const known = allowedRoutes.find(([pattern]) => pattern.test(req.path));
    if (known) {
      res.set("Allow", known[1]);
      return send(res, 405, { detail: "Method Not Allowed" });
    }
    return send(res, 404, { detail: "Not Found" });
  });
  app.use((error, req, res, next) => {
    if (req.operationSignal?.aborted || res.destroyed) return;
    if (res.headersSent) return next(error);
    if (error.type === "entity.too.large")
      return send(res, 413, { detail: "request body too large" });
    if (error.type === "encoding.unsupported")
      return send(res, 422, {
        detail: [{ loc: ["body"], msg: "Invalid JSON", type: "json_invalid" }],
      });
    if (error instanceof HttpError) {
      for (const [key, value] of Object.entries(error.headers))
        res.set(key, value);
      return send(res, error.status, { detail: error.detail });
    }
    console.error("request failed", error.code || error.name);
    send(res, 500, { detail: "Internal Server Error" });
  });
  return app;
}
