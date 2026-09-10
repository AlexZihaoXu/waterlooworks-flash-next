FROM ghcr.io/astral-sh/uv:0.9.22 AS uv
FROM node:22.22.3-bookworm-slim AS node
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./backend/
COPY backend/src ./backend/src
RUN uv sync --locked --no-dev --no-editable --project backend --python /usr/bin/python3.12

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble AS prod
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY scripts/*.mjs ./scripts/
COPY benchmarks/optimized/v7-salary.txt benchmarks/optimized/v7-keywords.txt ./benchmarks/optimized/
COPY backend/src ./backend/src
COPY deployment/backend-entrypoint.sh /usr/local/bin/backend-entrypoint
ENV PATH=/opt/venv/bin:$PATH UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    WW_PROJECT_ROOT=/app WW_DATABASE=/app/data/waterlooworks.sqlite3 \
    WW_BROWSER_PROFILE=/app/browser-profile WW_BROWSER_HEADLESS=1 \
    HOME=/tmp/home XDG_CACHE_HOME=/tmp/cache XDG_CONFIG_HOME=/tmp/config
# A dedicated fixed UID avoids relying on the base image pwuser UID. First use
# copies these private ownership/modes; existing volumes require offline provisioning.
RUN groupadd --gid 10001 ww \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp/home --shell /usr/sbin/nologin ww \
    && install -d -m 0700 -o 10001 -g 10001 /app/data /app/browser-profile \
    && chmod 0555 /usr/local/bin/backend-entrypoint \
    && /opt/venv/bin/python -c "import sys, importlib.metadata; assert sys.version_info[:2] == (3, 12); assert importlib.metadata.version('playwright') == '1.62.0'" \
    && node --version
USER 10001:10001
EXPOSE 8787
ENTRYPOINT ["/usr/local/bin/backend-entrypoint"]
