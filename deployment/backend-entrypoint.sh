#!/bin/sh
set -eu
umask 077
mkdir -p /tmp/home /tmp/cache /tmp/config
exec /opt/venv/bin/python -m uvicorn waterlooworks_flash_next.api:create_app --factory --host 0.0.0.0 --port 8787 --workers 1 --no-proxy-headers --no-access-log --timeout-graceful-shutdown 10
