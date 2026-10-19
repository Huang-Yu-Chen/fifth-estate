# The public API only (src/fifth_estate/api): FastAPI + uvicorn serving read-only SQLite snapshots.
# Built and deployed to Cloud Run fifth-estate-api by .github/workflows/api.yml (first time: scripts/setup_backend.sh).
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group api

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY src/fifth_estate/__init__.py src/fifth_estate/__init__.py
COPY src/fifth_estate/api src/fifth_estate/api
RUN /app/.venv/bin/python -m compileall -q src
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER nobody
# Cloud Run sets PORT; access logs are off because Cloud Run logs every request itself.
CMD ["sh", "-c", "exec uvicorn fifth_estate.api.app:app --host 0.0.0.0 --port ${PORT:-8080} --log-level warning --no-access-log"]
