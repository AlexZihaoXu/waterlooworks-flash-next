FROM ghcr.io/astral-sh/uv:0.9.22@sha256:2320e6c239737dc73cccce393a8bb89eba2383d17018ee91a59773df802c20e6 AS uv
FROM node:22.22.3-bookworm-slim@sha256:e21fc383b50d5347dc7a9f1cae45b8f4e2f0d39f7ade28e4eef7d2934522b752 AS node
# Pi inference sidecar: exact-pinned lockfile, no install scripts, compiled at
# build time; only production dependencies reach the runtime image.
FROM node AS sidecar
WORKDIR /sidecar
COPY inference-sidecar/package.json inference-sidecar/package-lock.json inference-sidecar/tsconfig.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY inference-sidecar/src ./src
COPY inference-sidecar/test ./test
RUN npx tsc -p tsconfig.json \
    && npm prune --omit=dev --ignore-scripts --no-audit --no-fund \
    && rm -rf src test dist/test tsconfig.json

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./backend/
COPY backend/src ./backend/src
RUN uv sync --locked --no-dev --no-editable --project backend --python /usr/bin/python3.12

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d AS prod
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=sidecar /sidecar /app/inference-sidecar
COPY scripts/*.mjs ./scripts/
COPY benchmarks/optimized/v7-salary.txt benchmarks/optimized/v7-keywords.txt ./benchmarks/optimized/
COPY backend/src ./backend/src
COPY docs/agent-api.md ./docs/agent-api.md
COPY deployment/backend-entrypoint.sh /usr/local/bin/backend-entrypoint
ENV PATH=/opt/venv/bin:$PATH UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    WW_PROJECT_ROOT=/app WW_DATABASE=/app/data/waterlooworks.sqlite3 \
    WW_BROWSER_PROFILE=/app/browser-profile WW_BROWSER_HEADLESS=1 \
    WW_SIDECAR_DIR=/app/inference-sidecar WW_NODE_BINARY=/usr/local/bin/node \
    HOME=/tmp/home XDG_CACHE_HOME=/tmp/cache XDG_CONFIG_HOME=/tmp/config
# A dedicated fixed UID avoids relying on the base image pwuser UID. First use
# copies these private ownership/modes; existing volumes require offline provisioning.
RUN groupadd --gid 10001 ww \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp/home --shell /usr/sbin/nologin ww \
    && install -d -m 0700 -o 10001 -g 10001 /app/data /app/browser-profile \
    && chmod 0555 /usr/local/bin/backend-entrypoint \
    && /opt/venv/bin/python -c "import sys, importlib.metadata; assert sys.version_info[:2] == (3, 12); assert importlib.metadata.version('playwright') == '1.62.0'" \
    && node --version \
    && test -f /app/inference-sidecar/dist/src/main.js
USER 10001:10001
EXPOSE 8787
ENTRYPOINT ["/usr/local/bin/backend-entrypoint"]
