ARG RUNTIME_IMAGE=lb-python:3.10.22-standard
FROM ${RUNTIME_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 openssh-client && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY requirements-dev.lock .
RUN python3 -m pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY tools tools
COPY database database
COPY benchmarks benchmarks
COPY docker docker
ENV PYTHONPATH=/workspace/tools
ENTRYPOINT ["python3","-m","bench.cli"]
