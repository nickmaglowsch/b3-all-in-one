# Build context is the REPO ROOT (docker-compose: context: . dockerfile: platform/Dockerfile).
# Baked in: platform/ (the app) + taxcore's CODE and a SEPARATE venv for its deps.
# taxcore/b3_imports, data, reports, tests hold REAL personal data and are blocked
# in Dockerfile.dockerignore — verify with `docker run --rm IMAGE ls /opt/taxcore`.
FROM python:3.12-slim

# curl for the compose healthcheck; uv as the installer.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy

# --- taxcore: deps in their own venv (/opt/taxcore/.venv) so taxcore's pandas/openpyxl
# pins don't fight platform deps. --no-install-project: we only want the deps; b3run runs
# the scripts by copying them into a throwaway workspace on PYTHONPATH, not via the package.
WORKDIR /opt/taxcore
COPY taxcore/pyproject.toml taxcore/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# taxcore CODE (NO b3_imports/data/reports/tests — see Dockerfile.dockerignore).
COPY taxcore/scripts ./scripts
COPY taxcore/methodologies ./methodologies

# --- platform app ---
WORKDIR /app
# Deps first for layer caching.
COPY platform/pyproject.toml platform/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# App code.
COPY platform/ ./
RUN uv sync --frozen --no-dev
RUN chmod +x /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
# taxcore lives at a fixed in-image path; b3run reads these (host runs leave them unset).
ENV TAXCORE_DIR=/opt/taxcore \
    TAXCORE_PYTHON=/opt/taxcore/.venv/bin/python
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "platform_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
