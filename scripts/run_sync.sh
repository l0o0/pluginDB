#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

exec /usr/bin/env python3 "$PROJECT_ROOT/scripts/sync_plugins.py" --root "$PROJECT_ROOT"
