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

DEFAULT_PLUGINS_TS_URL = "https://raw.githubusercontent.com/zotero-chinese/zotero-plugins/main/src/plugins.ts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Zotero plugin metadata into local storage")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="Project root directory")
    parser.add_argument(
        "--mode",
        choices=("init", "sync"),
        default="sync",
        help="init downloads all declared releases; sync only checks dynamic releases like latest/pre/custom",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy database URL. Defaults to a SQLite file under the root data directory",
    )
    parser.add_argument(
        "--plugins-file",
        type=Path,
        default=None,
        help="Read plugins.ts from a local file instead of downloading it",
    )
    parser.add_argument(
        "--plugins-url",
        default=DEFAULT_PLUGINS_TS_URL,
        help="Raw plugins.ts source URL",
    )
    parser.add_argument(
        "--github-token",
        default=None,
        help="Optional GitHub token used for API and asset download requests",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(args.root / ".env")

    from plugindb_sync.sync import run_sync

    result = run_sync(
        root=args.root,
        mode=args.mode,
        database_url=args.database_url or os.getenv("PLUGINDB_DATABASE_URL"),
        plugins_ts_path=args.plugins_file,
        github_token=args.github_token,
        plugins_url=args.plugins_url,
    )
    print(
        f"plugin_count={result.plugin_count} success_count={result.success_count} "
        f"failure_count={result.failure_count}"
    )
    for failure in result.failures:
        print(f"ERROR {failure}")
    return 0 if result.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
