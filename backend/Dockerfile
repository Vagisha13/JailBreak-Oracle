# Jailbreak Oracle — backend image.
#
# One image serves two entrypoints:
#   * API:      uvicorn app.main:app         (default CMD)
#   * Worker:   python -m app.worker          (docker compose service override)
#
# Runtime dependencies (psycopg2-binary, asyncpg, pgvector) ship binary wheels
# so a slim base is sufficient — no build toolchain required.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so the layer cache survives source edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source. `benchmarks` is a runtime import (kept out of the app
# package so a future pip install remains clean).
COPY alembic ./alembic
COPY alembic.ini ./
COPY app ./app
COPY benchmarks ./benchmarks

# Run as an unprivileged user for API + worker processes.
RUN useradd --create-home oracle && chown -R oracle:oracle /app
USER oracle

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]