export function readConfig(env = process.env) {
  const databaseUrl = (env.DATABASE_URL || "").replace(
    "postgresql+psycopg://",
    "postgresql://",
  );
  const url = new URL(databaseUrl);
  if (url.protocol !== "postgresql:" && url.protocol !== "postgres:")
    throw Error("PostgreSQL required");
  if (!env.JWT_SECRET || Buffer.byteLength(env.JWT_SECRET) < 32)
    throw Error("JWT_SECRET requires at least 32 bytes");
  const jwtTtl = Number(env.JWT_TTL_SECONDS || 3600);
  if (!Number.isInteger(jwtTtl) || jwtTtl < 600 || jwtTtl > 86400)
    throw Error("JWT_TTL_SECONDS must be 600..86400");
  const port = Number(env.PORT || 8000);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw Error("invalid PORT");
  const mode = env.MODE || "local";
  if (
    mode === "aws" &&
    (url.searchParams.get("sslmode") !== "verify-full" ||
      !url.searchParams.get("sslrootcert"))
  )
    throw Error("AWS requires sslmode=verify-full and sslrootcert");
  const workerValue = env.WORKERS ?? "2";
  if (!["2", "4"].includes(workerValue)) throw Error("WORKERS must be 2 or 4");
  const workers = Number(workerValue);
  return {
    workers,
    connectionsPerWorker: 20 / workers,
    databaseUrl,
    jwtSecret: env.JWT_SECRET,
    jwtTtl,
    mode,
    port,
    bindIp: env.BIND_IP || "0.0.0.0",
    runId: env.RUN_ID || "",
    runDir: env.RUN_DIR || "",
  };
}
