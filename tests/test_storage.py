import tempfile
import unittest
from pathlib import Path

from plugindb_sync.storage import create_engine, ensure_schema, fetch_all, fetch_one, upsert_plugin_record


class StorageTest(unittest.TestCase):
    def test_upserts_plugin_release_and_locales(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "plugins.sqlite3"
            engine = create_engine(f"sqlite+pysqlite:///{db_path}")
            ensure_schema(engine)

            record = {
                "id": "demo@example.com",
                "plugin_name": "Demo",
                "sanitized_name": "Demo",
                "source_repo": "demo/repo",
                "source_url": "https://github.com/demo/repo",
                "homepage_url": "https://example.com",
                "author": "author",
                "update_url": "https://example.com/update.json",
                "releases": {
                    "latest": {
                        "tag": "v1.2.3",
                        "prerelease": False,
                        "published_at": "2026-04-11T00:00:00Z",
                        "asset_name": "demo.xpi",
                        "asset_url": "https://example.com/demo.xpi",
                        "xpi_path": "data/xpi/Demo/v1.2.3.xpi",
                        "md5": "abc",
                        "manifest_version": "1.2.3",
                        "manifest_min_zotero_version": "7.0",
                        "manifest_max_zotero_version": "8.*",
                        "manifest_json": {"version": "1.2.3"},
                    },
                    "pre": None,
                },
                "locales": [
                    {
                        "locale": "und",
                        "field": "description",
                        "source": "manifest",
                        "value": "desc",
                    },
                    {
                        "locale": "zh-CN",
                        "field": "description",
                        "source": "github_repo",
                        "value": "中文描述",
                    },
                ],
                "synced_at": "2026-04-11T01:00:00Z",
            }

            upsert_plugin_record(engine, record)
            plugin_row = fetch_one(
                engine,
                "SELECT id, plugin_name, source_repo, homepage_url FROM plugins",
            )
            release_row = fetch_one(
                engine,
                "SELECT plugin_id, release_key, tag, manifest_version, md5 FROM plugin_releases",
            )
            locale_rows = fetch_all(
                engine,
                "SELECT plugin_id, locale, field, source, value FROM plugin_locales ORDER BY locale, source",
            )

            self.assertEqual(
                plugin_row,
                ("demo@example.com", "Demo", "demo/repo", "https://example.com"),
            )
            self.assertEqual(
                release_row,
                ("demo@example.com", "latest", "v1.2.3", "1.2.3", "abc"),
            )
            self.assertEqual(
                locale_rows,
                [
                    ("demo@example.com", "und", "description", "manifest", "desc"),
                    ("demo@example.com", "zh-CN", "description", "github_repo", "中文描述"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
