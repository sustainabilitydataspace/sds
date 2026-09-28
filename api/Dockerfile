# syntax=docker/dockerfile:1

# sds-canonical-json-v1 requires Unicode 13.0.0 (CPython 3.10).
# Verified Docker Hub linux/amd64 manifest for python:3.10-slim; update only
# together with the reviewed Python/runtime lock and release provenance.
FROM python:3.10-slim@sha256:179cecec99c29e6f1e97caa424514599267f7dd836696dd673ea5587eefc65fa

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.lock ./
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock

# Create non-root user
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/false appuser && \
    chown -R appuser:appgroup /app

COPY --chown=appuser:appgroup src/ ./src/
COPY --chown=appuser:appgroup ontologies/ ./ontologies/
COPY --chown=appuser:appgroup alembic.ini ./alembic.ini
COPY --chown=appuser:appgroup alembic/ ./alembic/

USER appuser

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8090/healthz')"]

CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8090", "--no-proxy-headers"]
