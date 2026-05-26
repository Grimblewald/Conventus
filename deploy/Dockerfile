# Minimal Dockerfile. Single-stage; small enough that two-stage savings
# don't matter much. Run:
#
#   docker build -t society-site .
#   docker run --env-file .env -p 5005:5005 -v $(pwd)/instance:/app/instance \
#              -v $(pwd)/uploads:/app/uploads society-site
#
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# uv (fast resolver/installer) — pinned for reproducibility
RUN pip install --no-cache-dir uv==0.4.18

WORKDIR /app

# Install deps separately for layer caching
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# App code
COPY . .

# Pre-create runtime dirs so a fresh container survives without bind mounts
RUN mkdir -p instance uploads

EXPOSE 5005

ENV GUNICORN_WORKERS=3 \
    GUNICORN_THREADS=32 \
    PORT=5005

CMD ["bash", "scripts/launch.sh"]
