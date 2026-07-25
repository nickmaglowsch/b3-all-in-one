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

# --- engine: backtest bench CODE + its own venv (/opt/engine/.venv), deps from
# requirements-bench.txt (slim set — the backtest path needs no research/ui deps).
# NO b3_pipeline_rs (Rust is ingestion-only; backtests read the DB and fall back
# to the Python pivot), NO tests, NO ui — see the dockerignore.
WORKDIR /opt/engine
COPY engine/requirements-bench.txt ./
RUN uv venv .venv --python 3.12 \
    && uv pip install --python .venv -r requirements-bench.txt
COPY engine/b3_pipeline ./b3_pipeline
COPY engine/backtests ./backtests
COPY engine/scripts ./scripts

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
# taxcore + engine live at fixed in-image paths; b3run/enginerun read these
# (host runs leave them unset and use the ../taxcore + ../engine defaults).
ENV TAXCORE_DIR=/opt/taxcore \
    TAXCORE_PYTHON=/opt/taxcore/.venv/bin/python \
    ENGINE_DIR=/opt/engine \
    ENGINE_PYTHON=/opt/engine/.venv/bin/python
# Drop root. The worker parses attacker-supplied .xlsx through taxcore's
# pandas/openpyxl venv, so a parse bug in that chain would otherwise run as uid 0
# with write access to /data/uploads and /data/artifacts — both shared with the
# api container, both named volumes whose files then land root-owned on the host.
# The data dirs are created and chowned here because the volumes mount over them
# as root otherwise.
# /opt/engine/data is created here even though the dockerignore keeps engine/data
# out of the image: Docker seeds a named volume from the image path it mounts over,
# so if the directory does not exist it is created root-owned and the non-root
# worker cannot write the archives it downloads there.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/uploads /data/artifacts /opt/engine/data \
    && chown -R 10001:10001 /data /app /opt/taxcore /opt/engine
USER 10001:10001

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
# --proxy-headers + trust-all: only Caddy can reach this container (no published
# ports), and it sets X-Forwarded-Proto=https. Without this, request.url_for()
# builds the OAuth callback as http:// and Google rejects the redirect_uri.
CMD ["uvicorn", "platform_app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
