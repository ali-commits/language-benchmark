ARG RUNTIME_IMAGE=lb-python:3.10.22-standard
FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv
FROM ${RUNTIME_IMAGE} AS wheels
USER root
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends curl build-essential libffi-dev libpq-dev && rm -rf /var/lib/apt/lists/*
COPY runtimes/python/rust-lock.json /tmp/rust-lock.json
RUN python3 -c "import json,urllib.request,hashlib,tarfile,subprocess; d=json.load(open('/tmp/rust-lock.json')); urllib.request.urlretrieve(d['url'],'/tmp/rust.tar.xz'); assert hashlib.sha256(open('/tmp/rust.tar.xz','rb').read()).hexdigest()==d['sha256']; t=tarfile.open('/tmp/rust.tar.xz'); t.extractall('/tmp'); subprocess.run(['sh','/tmp/rust-'+d['version']+'-x86_64-unknown-linux-gnu/install.sh','--prefix=/opt/rust','--components=rustc,cargo,rust-std-x86_64-unknown-linux-gnu','--disable-ldconfig'],check=True)"
ENV PATH=/opt/rust/bin:$PATH
COPY backends/python/build-requirements.lock /tmp/build-requirements.lock
RUN uv venv --python /opt/python/bin/python3 /opt/venv && uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/build-requirements.lock
ARG APP=baseline
COPY backends/python/${APP}/requirements.lock /tmp/requirements.lock
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --python /opt/venv/bin/python --no-build-isolation --require-hashes -r /tmp/requirements.lock && uv pip freeze --python /opt/venv/bin/python > /opt/venv/installed-packages.txt
FROM ${RUNTIME_IMAGE}
ARG APP=baseline
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=wheels /opt/venv /opt/venv
COPY runtimes/python/rust-lock.json /app/rust-build-input.json
ENV PATH=/opt/venv/bin:$PATH
COPY backends/python/${APP}/src /app/src
COPY backends/python/entrypoint.py /app/entrypoint.py
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M app
ENV PYTHONPATH=/app/src APP_VARIANT=${APP}
USER 10001:10001
ENTRYPOINT ["python3","/app/entrypoint.py"]
