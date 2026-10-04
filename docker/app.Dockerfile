ARG RUNTIME_IMAGE=lb-python:3.10.22-standard
FROM ${RUNTIME_IMAGE}
ARG APP=baseline
WORKDIR /app
COPY apps/${APP}/requirements.lock /app/requirements.lock
RUN python3 -m pip install --no-cache-dir --require-hashes -r /app/requirements.lock
COPY apps/${APP}/src /app/src
COPY docker/entrypoint.py /app/entrypoint.py
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M app
ENV PYTHONPATH=/app/src APP_VARIANT=${APP}
USER 10001:10001
ENTRYPOINT ["python3","/app/entrypoint.py"]
