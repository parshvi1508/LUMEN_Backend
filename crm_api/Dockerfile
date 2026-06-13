FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic/ alembic/
COPY crm_api/ crm_api/
COPY scripts/ scripts/

RUN useradd --create-home appuser
USER appuser

# Render injects $PORT and probes healthCheckPath externally; no in-image HEALTHCHECK needed.
# Migrations run on start: alembic upgrade head is idempotent (no-op when already current).
CMD ["sh", "-c", "alembic upgrade head && uvicorn crm_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
