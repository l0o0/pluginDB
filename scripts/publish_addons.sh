#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

SOURCE_DIR="${SOURCE_DIR:-$PROJECT_ROOT/data/xpi}"
TARGET_DIR="${1:-${ADDONS_PUBLISH_DIR:-/var/www/downloads/addons}}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "ERROR rsync is required to publish addons" >&2
  exit 1
fi

if [ ! -d "$SOURCE_DIR" ]; then
  echo "ERROR source directory does not exist: $SOURCE_DIR" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
rsync -a --delete "$SOURCE_DIR"/ "$TARGET_DIR"/
echo "published_addons source=$SOURCE_DIR target=$TARGET_DIR"
