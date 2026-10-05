import argon2 from "argon2";
import jwt from "jsonwebtoken";
import { randomBytes } from "node:crypto";
import { HttpError } from "./validation.js";
import { HashQueue } from "./hash_queue.js";
const policy = {
  type: argon2.argon2id,
  memoryCost: 65536,
  timeCost: 3,
  parallelism: 1,
  hashLength: 32,
};
export async function createSecurity(cfg) {
  const queue = new HashQueue();
  const hash = (password, signal) =>
    queue.run(async () => {
      const encoded = await argon2.hash(password, {
        ...policy,
        salt: randomBytes(16),
      });
      return encoded.replace("m=65536,p=1,t=3", "m=65536,t=3,p=1");
    }, signal);
  const dummy = await hash("dummy-only-not-a-valid-user-password");
  const unauthorized = () =>
    new HttpError(401, "invalid credentials", { "WWW-Authenticate": "Bearer" });
  return {
    hash,
    verify: (password, encoded = dummy, signal) =>
      queue.run(async () => {
        try {
          return await argon2.verify(encoded, password);
        } catch {
          return false;
        }
      }, signal),
    issue: (id) => {
      const now = Math.floor(Date.now() / 1000);
      return jwt.sign(
        {
          sub: String(id),
          iat: now,
          exp: now + cfg.jwtTtl,
          iss: "language-benchmark",
          aud: "social-api",
        },
        cfg.jwtSecret,
        { algorithm: "HS256" },
      );
    },
    identity: (token) => {
      try {
        const claims = jwt.verify(token, cfg.jwtSecret, {
          algorithms: ["HS256"],
          issuer: "language-benchmark",
          audience: "social-api",
        });
        const now = Date.now() / 1000;
        if (
          typeof claims.sub !== "string" ||
          !/^\+?\d+$/.test(claims.sub) ||
          !Number.isInteger(claims.iat) ||
          !Number.isInteger(claims.exp) ||
          claims.iat > now + 30 ||
          claims.exp <= now
        )
          throw Error("claims");
        const id = BigInt(claims.sub);
        if (id <= 0n || id >= 2n ** 63n) throw Error("subject");
        return id;
      } catch {
        throw unauthorized();
      }
    },
    unauthorized,
  };
}
