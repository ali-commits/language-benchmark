ARG RUNTIME_IMAGE=lb-python:3.10.22-standard
FROM ${RUNTIME_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 openssh-client && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /usr/local/bin/uv
WORKDIR /workspace
COPY requirements-dev.lock .
RUN uv venv --python /opt/python/bin/python3 /opt/venv && uv pip sync --python /opt/venv/bin/python --require-hashes requirements-dev.lock
ENV PATH=/opt/venv/bin:$PATH
COPY tools tools
COPY database database
COPY benchmarks benchmarks
COPY docker docker
ENV PYTHONPATH=/workspace/tools
ENTRYPOINT ["python3","-m","bench.cli"]
