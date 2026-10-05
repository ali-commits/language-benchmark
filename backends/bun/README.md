# Bun 1.4.2 + Express 5.2.1

Uses the same sources, dependencies and production cluster launcher as [../javascript/express](../javascript/express/README.md). Build from the repository root:

```sh
docker build -f backends/bun/Dockerfile -t lb-bun-express:1.4.2 .
docker run --rm --cpus=2 --memory=3g --memory-swap=3g \
  --env-file /path/to/private.env -p 8000:8000 lb-bun-express:1.4.2
```

Both build and production base are pinned to `oven/bun:1.4.2-debian`. Linux `node:cluster` enables the same two HTTP workers, ten DB connections each, one active hash each. Independent `node:http` subprocesses do not share ports reliably in Bun; the tested cluster API handles that startup behavior. Express uses Bun's Node HTTP compatibility layer.

Run all native tests in the pinned image against a disposable PostgreSQL database initialized using `database/schema.sql`:

```sh
docker run --rm --network host \
  -e EXPRESS_TEST_DATABASE_URL=postgresql://postgres:express_test_password@127.0.0.1:55435/language_benchmark_express_test \
  -v "$PWD/backends/javascript/express/test:/app/test:ro" \
  lb-bun-express:1.4.2 bun test --timeout 15000 test
```
