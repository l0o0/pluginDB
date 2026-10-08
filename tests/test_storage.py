import tempfile
import unittest
from pathlib import Path

from sqlalchemy import ARRAY, Text
from sqlalchemy.dialects import postgresql

from plugindb_sync.storage import create_engine, ensure_schema, fetch_all, fetch_one, plugins_table, promote_tables, upsert_plugin_record


class StorageTest(unittest.TestCase):
    def test_binds_plugin_tags_as_text_array_for_postgres(self) -> None:
        tags_type = plugins_table.c.tags.type.dialect_impl(postgresql.dialect())

        self.assertIsInstance(tags_type, ARRAY)
        self.assertIsInstance(tags_type.item_type, Text)

    def test_promotes_staging_tables_to_public_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "plugins.sqlite3"
            engine = create_engine(f"sqlite+pysqlite:///{db_path}")
            ensure_schema(engine)
            with engine.begin() as connection:
                for source, target in [
                    ("plugins", "public_plugins"),
                    ("plugin_releases", "public_plugin_releases"),
                    ("plugin_locales", "public_plugin_locales"),
                ]:
                    connection.exec_driver_sql(f"CREATE TABLE {target} AS SELECT * FROM {source} WHERE 0")

            old_record = {
                "id": "demo@example.com",
                "plugin_name": "Demo",
                "sanitized_name": "Demo",
                "source_repo": "demo/repo",
                "source_url": "https://github.com/demo/repo",
                "homepage_url": "https://example.com",
                "author": "author",
                "update_url": "https://example.com/update.json",
                "tags": ["old"],
                "github_stars": 1,
                "download_count": 10,
                "releases": {
                    "latest": {
                        "tag": "v1.0.0",
                        "prerelease": False,
                        "published_at": "2026-04-10T00:00:00Z",
                        "asset_name": "demo.xpi",
                        "asset_url": "https://example.com/demo-v1.xpi",
                        "xpi_path": "data/xpi/Demo/v1.0.0.xpi",
                        "md5": "old",
                        "download_count": 10,
                        "manifest_version": "1.0.0",
                        "manifest_min_zotero_version": "7.0",
                        "manifest_max_zotero_version": "8.*",
                        "manifest_json": {"version": "1.0.0"},
                    }
                },
                "locales": [{"locale": "und", "field": "description", "source": "manifest", "value": "old"}],
                "synced_at": "2026-04-10T01:00:00Z",
            }
            new_record = {
                **old_record,
                "tags": ["new"],
                "github_stars": 2,
                "download_count": 20,
                "releases": {
                    "latest": {
                        **old_record["releases"]["latest"],
                        "tag": "v2.0.0",
                        "asset_url": "https://example.com/demo-v2.xpi",
                        "xpi_path": "data/xpi/Demo/v2.0.0.xpi",
                        "md5": "new",
                        "download_count": 20,
                        "manifest_version": "2.0.0",
                        "manifest_json": {"version": "2.0.0"},
                    }
                },
                "locales": [{"locale": "und", "field": "description", "source": "manifest", "value": "new"}],
                "synced_at": "2026-04-11T01:00:00Z",
            }

            upsert_plugin_record(engine, old_record)
            promote_tables(
                engine,
                plugins_table_name="public_plugins",
                releases_table_name="public_plugin_releases",
                locales_table_name="public_plugin_locales",
                staging_plugins_table_name="plugins",
                staging_releases_table_name="plugin_releases",
                staging_locales_table_name="plugin_locales",
            )
            upsert_plugin_record(engine, new_record)
            promote_tables(
                engine,
                plugins_table_name="public_plugins",
                releases_table_name="public_plugin_releases",
                locales_table_name="public_plugin_locales",
                staging_plugins_table_name="plugins",
                staging_releases_table_name="plugin_releases",
                staging_locales_table_name="plugin_locales",
            )

            self.assertEqual(
                fetch_one(engine, "SELECT github_stars, download_count, tags FROM public_plugins"),
                (2, 20, '["new"]'),
            )
            self.assertEqual(
                fetch_one(engine, "SELECT tag, manifest_version, asset_url FROM public_plugin_releases"),
                ("v2.0.0", "2.0.0", "https://example.com/demo-v2.xpi"),
            )
            self.assertEqual(fetch_one(engine, "SELECT value FROM public_plugin_locales"), ("new",))

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
                "tags": ["style", "notes"],
                "github_stars": 42,
                "download_count": 1234,
                "releases": {
                    "latest": {
                        "tag": "v1.2.3",
                        "prerelease": False,
                        "published_at": "2026-04-11T00:00:00Z",
                        "asset_name": "demo.xpi",
                        "asset_url": "https://example.com/demo.xpi",
                        "xpi_path": "data/xpi/Demo/v1.2.3.xpi",
                        "md5": "abc",
                        "download_count": 567,
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
                "SELECT id, plugin_name, source_repo, homepage_url, tags, github_stars, download_count FROM plugins",
            )
            release_row = fetch_one(
                engine,
                "SELECT plugin_id, release_key, tag, manifest_version, md5, download_count FROM plugin_releases",
            )
            locale_rows = fetch_all(
                engine,
                "SELECT plugin_id, locale, field, source, value FROM plugin_locales ORDER BY locale, source",
            )

            self.assertEqual(
                plugin_row,
                ("demo@example.com", "Demo", "demo/repo", "https://example.com", '["style", "notes"]', 42, 1234),
            )
            self.assertEqual(
                release_row,
                ("demo@example.com", "latest", "v1.2.3", "1.2.3", "abc", 567),
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
