#!/bin/sh
set -eu
mkdir -p "${APP_DATA_DIR:-/data}/adapters"
exec "$@"
