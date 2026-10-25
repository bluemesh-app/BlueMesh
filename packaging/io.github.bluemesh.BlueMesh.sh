#!/bin/sh
set -eu
export BLUEMESHD="${BLUEMESHD:-/app/bin/bluemeshd}"
exec python3 /app/lib/bluemesh/bluemesh.py "$@"
