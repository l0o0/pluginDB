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

from plugindb_sync.sync import DEFAULT_PLUGINS_TS_URL, run_sync


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Zotero plugin metadata into local storage")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="Project root directory")
    parser.add_argument(
        "--mode",
        choices=("init", "sync"),
        default="sync",
        help="sync discovers new releases and retains historical selectors in modern catalogs; init also includes fixed tags in legacy catalogs",
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
        "--deprecated-file", type=Path, default=None,
        help="Local deprecated.ts; defaults to a sibling of --plugins-file when present",
    )
    parser.add_argument(
        "--deprecated-url", default=None,
        help="Legacy catalog URL; the default upstream includes deprecated.ts automatically",
    )
    parser.add_argument(
        "--active-only", action="store_true",
        help="Exclude the legacy plugin catalog",
    )
    parser.add_argument(
        "--github-token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub token used for API and asset download requests; defaults to GITHUB_TOKEN",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_sync(
        root=args.root,
        mode=args.mode,
        database_url=args.database_url,
        plugins_ts_path=args.plugins_file,
        github_token=args.github_token,
        plugins_url=args.plugins_url,
        deprecated_ts_path=args.deprecated_file,
        deprecated_url=args.deprecated_url,
        include_deprecated=not args.active_only,
    )
    print(
        f"plugin_count={result.plugin_count} success_count={result.success_count} "
        f"failure_count={result.failure_count} pending_count={result.pending_count}"
    )
    for failure in result.failures:
        print(f"ERROR {failure}")
    return 0 if result.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
