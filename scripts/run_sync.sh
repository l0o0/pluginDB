#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

set -- "$PROJECT_ROOT/scripts/sync_plugins.py" --root "$PROJECT_ROOT"

if [ -n "${PLUGINDB_DATABASE_URL:-}" ]; then
    set -- "$@" --database-url "$PLUGINDB_DATABASE_URL"
fi

exec /usr/bin/env python3 "$@"
