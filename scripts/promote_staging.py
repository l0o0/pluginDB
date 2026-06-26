#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from plugindb_sync.env_loader import load_dotenv
from plugindb_sync.storage import create_engine, promote_tables


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote Zotero plugin staging tables into public tables")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="Project root directory")
    parser.add_argument("--database-url", default=None, help="SQLAlchemy database URL")
    parser.add_argument("--plugins-table", default="zotero_plugins")
    parser.add_argument("--plugin-releases-table", default="zotero_plugin_releases")
    parser.add_argument("--plugin-locales-table", default="zotero_plugin_locales")
    parser.add_argument("--staging-plugins-table", default="zotero_plugin_staging_plugins")
    parser.add_argument("--staging-plugin-releases-table", default="zotero_plugin_staging_releases")
    parser.add_argument("--staging-plugin-locales-table", default="zotero_plugin_staging_locales")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(args.root / ".env")
    database_url = args.database_url or os.getenv("PLUGINDB_DATABASE_URL")
    if not database_url:
        print("ERROR database URL is required", file=sys.stderr)
        return 1
    engine = create_engine(database_url)
    try:
        counts = promote_tables(
            engine,
            plugins_table_name=args.plugins_table,
            releases_table_name=args.plugin_releases_table,
            locales_table_name=args.plugin_locales_table,
            staging_plugins_table_name=args.staging_plugins_table,
            staging_releases_table_name=args.staging_plugin_releases_table,
            staging_locales_table_name=args.staging_plugin_locales_table,
        )
    finally:
        engine.dispose()
    print(
        "promoted_plugins "
        f"plugins={counts['plugins']} releases={counts['releases']} locales={counts['locales']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
