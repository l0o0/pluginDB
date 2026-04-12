import json
import tempfile
import unittest
from pathlib import Path

from plugindb_sync.sync import run_sync
from plugindb_sync.storage import create_engine, fetch_all, fetch_one


class SyncTest(unittest.TestCase):
    def test_reads_plugins_from_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            plugins_file = root / "fixtures" / "plugins.ts"
            plugins_file.parent.mkdir(parents=True, exist_ok=True)
            plugins_file.write_text(
                """
                export const plugins = [
                  { name: 'Demo', repo: 'demo/repo', releases: [{ tagName: 'latest' }] }
                ]
                """,
                encoding="utf-8",
            )

            result = run_sync(
                root=root,
                plugins_ts_path=plugins_file,
                github_release_map={
                    "demo/repo": [
                        {
                            "tag_name": "v1.2.3",
                            "prerelease": False,
                            "published_at": "2026-04-11T00:00:00Z",
                            "assets": [
                                {
                                    "name": "demo.xpi",
                                    "browser_download_url": "https://example.com/demo.xpi",
                                }
                            ],
                        }
                    ]
                },
                downloaded_xpi_manifests={
                    "https://example.com/demo.xpi": {
                        "name": "Demo",
                        "version": "1.2.3",
                        "description": "desc",
                        "homepage_url": "https://example.com",
                        "author": "author",
                        "applications": {
                            "zotero": {
                                "id": "demo@example.com",
                                "strict_min_version": "7.0",
                                "strict_max_version": "8.*",
                            }
                        },
                    }
                },
                github_repo_map={
                    "demo/repo": {
                        "description": "Repo description",
                        "homepage": "https://repo.example.com",
                        "html_url": "https://github.com/demo/repo",
                    }
                },
            )

            self.assertEqual(result.plugin_count, 1)
            self.assertEqual(result.success_count, 1)
            cached_text = (root / "data" / "cache" / "plugins.ts").read_text(encoding="utf-8")
            self.assertIn("demo/repo", cached_text)
            engine = create_engine(f"sqlite+pysqlite:///{root / 'data' / 'db' / 'plugins.sqlite3'}")
            locale_rows = fetch_all(
                engine,
                "SELECT locale, source, value FROM plugin_locales ORDER BY source",
            )
            self.assertEqual(
                locale_rows,
                [("und", "github_repo", "Repo description"), ("und", "manifest", "desc")],
            )

    def test_runs_end_to_end_with_stubbed_clients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            result = run_sync(
                root=root,
                plugins_ts_text="""
                export const plugins = [
                  { name: 'Demo', repo: 'demo/repo', releases: [{ tagName: 'latest' }] }
                ]
                """,
                github_release_map={
                    "demo/repo": [
                        {
                            "tag_name": "v1.2.3",
                            "prerelease": False,
                            "published_at": "2026-04-11T00:00:00Z",
                            "assets": [
                                {
                                    "name": "demo.xpi",
                                    "browser_download_url": "https://example.com/demo.xpi",
                                }
                            ],
                        }
                    ]
                },
                downloaded_xpi_manifests={
                    "https://example.com/demo.xpi": {
                        "name": "Demo",
                        "version": "1.2.3",
                        "description": "desc",
                        "homepage_url": "https://example.com",
                        "author": "author",
                        "applications": {
                            "zotero": {
                                "id": "demo@example.com",
                                "strict_min_version": "7.0",
                                "strict_max_version": "8.*",
                            }
                        },
                    }
                },
                github_repo_map={
                    "demo/repo": {
                        "description": "Repo description",
                        "homepage": "https://repo.example.com",
                        "html_url": "https://github.com/demo/repo",
                    }
                },
            )

            self.assertEqual(result.plugin_count, 1)
            self.assertEqual(result.success_count, 1)
            json_files = list((root / "data" / "json").glob("*.json"))
            self.assertEqual(len(json_files), 1)
            payload = json.loads(json_files[0].read_text())
            self.assertEqual(payload["id"], "demo@example.com")
            self.assertEqual(payload["releases"]["latest"]["xpi_path"], "data/xpi/Demo/v1.2.3.xpi")
            self.assertEqual(payload["locales"][0]["field"], "description")

            engine = create_engine(f"sqlite+pysqlite:///{root / 'data' / 'db' / 'plugins.sqlite3'}")
            plugin_row = fetch_one(
                engine,
                "SELECT id, plugin_name, homepage_url FROM plugins",
            )
            release_row = fetch_one(
                engine,
                "SELECT tag, xpi_path, manifest_version FROM plugin_releases",
            )
            self.assertEqual(plugin_row, ("demo@example.com", "Demo", "https://example.com"))
            self.assertEqual(release_row, ("v1.2.3", "data/xpi/Demo/v1.2.3.xpi", "1.2.3"))


if __name__ == "__main__":
    unittest.main()
