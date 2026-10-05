# Node 26.10.0 + Express 5.2.1

Uses the shared implementation in [../javascript/express](../javascript/express/README.md). Build from the repository root:

```sh
docker build -f backends/node/Dockerfile -t lb-node-express:26.10.0 .
docker run --rm --cpus=2 --memory=3g --memory-swap=3g \
  --env-file /path/to/private.env -p 8000:8000 lb-node-express:26.10.0
```

Production base: `node:26.10.0-bookworm-slim`; dependency installation base: `oven/bun:1.4.2-debian`. Two cluster workers, ten connections each, one hash each. Node is the latest stable current release. Apply the same fixed CPU/memory/connection budget to all comparisons.

The full native suite can run in the built image, with a disposable PostgreSQL instance initialized using `database/schema.sql`:

```sh
docker run --rm --network host \
  -e EXPRESS_TEST_DATABASE_URL=postgresql://postgres:express_test_password@127.0.0.1:55435/language_benchmark_express_test \
  -v "$PWD/backends/javascript/express/test:/app/test:ro" \
  lb-node-express:26.10.0 node --test 'test/*.test.js'
```
