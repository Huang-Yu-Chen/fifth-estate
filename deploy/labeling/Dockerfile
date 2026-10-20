# The labeling site (src/fifth_estate/labeling/server.py) on Cloud Run. scripts/deploy_labeling.sh
# builds it from a staged folder holding only pyproject.toml, uv.lock and the labeling package, so
# no pipeline code and no data end up in the image. Dependencies: the `labeling-server` group.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-hashes --only-group labeling-server --no-emit-project -o requirements.txt \
 && uv pip install --system --no-cache -r requirements.txt \
 && rm /usr/local/bin/uv requirements.txt
COPY fifth_estate/__init__.py fifth_estate/__init__.py
COPY fifth_estate/labeling fifth_estate/labeling
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app PORT=8080
USER nobody
CMD exec uvicorn --factory fifth_estate.labeling.server:create_app --host 0.0.0.0 --port "$PORT" \
    --proxy-headers --forwarded-allow-ips '*' --no-server-header --no-access-log
