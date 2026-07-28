#!/bin/sh
set -eu
mkdir -p "${APP_DATA_DIR:-/data}/adapters"
if [ -d /app/default-data/adapters ] && [ -z "$(ls -A "${APP_DATA_DIR:-/data}/adapters" 2>/dev/null)" ]; then
  cp -a /app/default-data/adapters/. "${APP_DATA_DIR:-/data}/adapters/"
fi
exec "$@"
