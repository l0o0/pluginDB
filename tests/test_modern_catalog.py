import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from unittest.mock import patch

from plugindb_sync.github_client import pick_release_for_tag, pick_xpi_asset
from plugindb_sync.plugin_source import parse_plugins_ts
from plugindb_sync.storage import create_engine, ensure_schema, fetch_all
from plugindb_sync.sync import DEFAULT_DEPRECATED_TS_URL, DEFAULT_PLUGINS_TS_URL, run_sync


def catalog(repo="demo/plugin", releases="[]", fields="", export="plugins"):
    return (f"export const {export} = [{{repo: '{repo}', releases: {releases}, "
            f"discoverReleases: true, tags: ['reader'], {fields}}}]")


def release(tag, names=("plugin.xpi",), date="2026-10-01T00:00:00Z"):
    return {
        "tag_name": tag, "prerelease": False, "published_at": date,
        "assets": [{"name": name, "browser_download_url": f"https://example.com/{tag}/{name}"}
                   for name in names],
    }


def manifest(plugin_id="plugin@example.com", version="2.0", minimum="7.0"):
    return {"name": "Plugin", "version": version, "applications": {"zotero": {
        "id": plugin_id, "strict_min_version": minimum, "strict_max_version": "11.*",
    }}}


class ModernCatalogTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = io.StringIO()
        self.addCleanup(patch.stopall)

    def sync(self, source, releases, manifests, **kwargs):
        with redirect_stdout(self.output):
            return run_sync(
                self.root, plugins_ts_text=source, github_release_map=releases,
                github_repo_map={}, downloaded_xpi_manifests=manifests, **kwargs,
            )

    def rows(self, sql):
        engine = create_engine(f"sqlite+pysqlite:///{self.root / 'data/db/plugins.sqlite3'}")
        try:
            return fetch_all(engine, sql)
        finally:
            engine.dispose()

    def test_removed_plugin_and_alias_are_excluded(self):
        for repo, fields in [("wdcpclover/ai4paper", ""),
                             ("new/renamed", "aliases: ['WDCPClOVER/AI4Paper'],")]:
            with self.subTest(repo=repo):
                result = self.sync(catalog(repo=repo, fields=fields), {}, {})
                self.assertEqual(result.plugin_count, 0)
                self.assertEqual(result.failure_count, 0)
                self.assertEqual(self.rows("select * from plugins"), [])

    def test_percent_encoded_asset_name(self):
        self.assertEqual(pick_xpi_asset(release("v1", ("plugin@local.xpi",)),
                                       "plugin%40local.xpi")["name"], "plugin@local.xpi")

    def test_missing_historical_asset_keeps_current_release(self):
        latest = release("v2")
        old = release("v1", ("other.xpi",), date="2025-01-01T00:00:00Z")
        result = self.sync(catalog(releases="[{tagName: 'v1', assetName: 'missing.xpi'}]"),
                           {"demo/plugin": [latest, old]},
                           {latest["assets"][0]["browser_download_url"]: manifest()})
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failure_count, 0)
        self.assertIn("action=unavailable_release", self.output.getvalue())
        self.assertEqual(len(self.rows("select * from plugins")), 1)

    def test_all_missing_artifacts_remain_failure(self):
        result = self.sync(catalog(releases="[{tagName: 'v1', assetName: 'missing.xpi'}]"),
                           {"demo/plugin": []}, {})
        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.failure_count, 1)

    def test_parses_modern_fields_without_comment_or_dev_entries(self):
        source = catalog(
            releases="[{tagName: 'v1', assetName: 'plugin.zip', targetZoteroVersion: '6'}]",
            fields="aliases: ['old/plugin'], // a comment containing } ]\n",
        ) + "\nexport const pluginsDev = [{repo: 'test/only'}]"
        plugin, = parse_plugins_ts(source)
        self.assertTrue(plugin.discover_releases)
        self.assertEqual(plugin.tags, ["reader"])
        self.assertEqual(plugin.aliases, ["old/plugin"])
        self.assertEqual(plugin.releases[0].asset_name, "plugin.zip")
        legacy, = parse_plugins_ts(catalog(export="deprecatedPlugins"), "deprecatedPlugins")
        self.assertEqual(legacy.repo, "demo/plugin")
        self.assertEqual(legacy.releases, [])

    def test_discovers_stable_and_retains_history_on_repeated_sync(self):
        source = catalog(releases="[{tagName: 'v1', assetName: 'plugin-v1.xpi', targetZoteroVersion: '6'}]")
        history = release("v1", ("plugin-v1.xpi",), "2025-01-01T00:00:00Z")
        latest = release("v2", ("plugin-v2.xpi",))
        beta = {**release("v3-beta", date="2026-10-02T00:00:00Z"), "prerelease": True}
        manifests = {
            history["assets"][0]["browser_download_url"]: manifest(version="1.0", minimum="6.0"),
            latest["assets"][0]["browser_download_url"]: manifest(version="2.0"),
        }
        for _ in range(2):
            result = self.sync(source, {"demo/plugin": [history, latest, beta]}, manifests)
            self.assertEqual((result.success_count, result.failure_count), (1, 0), result.failures)
            self.assertEqual(self.rows("SELECT release_key, tag FROM plugin_releases ORDER BY release_key"),
                             [("latest", "v2"), ("v1@zotero-6", "v1")])
        self.assertEqual(json.loads(self.rows("SELECT tags FROM plugins")[0][0]), ["reader"])
        self.assertIn("action=skip", self.output.getvalue())

    def test_pinned_asset_beats_shorter_debug_asset_and_accepts_zip(self):
        source = catalog(releases="[{tagName: 'v1', assetName: 'selected.zip'}]")
        payload = release("v1", ("a.xpi", "selected.zip"))
        result = self.sync(source, {"demo/plugin": [payload]}, {
            payload["assets"][1]["browser_download_url"]: manifest(),
        })
        self.assertEqual((result.success_count, result.failure_count), (1, 0), result.failures)
        self.assertEqual(self.rows("SELECT DISTINCT asset_name FROM plugin_releases"), [("selected.zip",)])

    def test_distinct_assets_for_same_tag_do_not_share_cache(self):
        source = catalog(releases="[{tagName: 'v1', assetName: 'old.xpi', targetZoteroVersion: '6'}, "
                                  "{tagName: 'v1', assetName: 'new.xpi', targetZoteroVersion: '9'}]")
        payload = release("v1", ("old.xpi", "new.xpi"))
        result = self.sync(source, {"demo/plugin": [payload]}, {
            payload["assets"][0]["browser_download_url"]: manifest(version="1.0", minimum="6.0"),
            payload["assets"][1]["browser_download_url"]: manifest(version="1.1", minimum="9.0"),
        })
        self.assertEqual(result.failure_count, 0, result.failures)
        self.assertEqual(self.rows("SELECT asset_name, manifest_version FROM plugin_releases "
                                  "WHERE release_key != 'latest' ORDER BY asset_name"),
                         [("new.xpi", "1.1"), ("old.xpi", "1.0")])
        self.assertEqual(len(set(row[0] for row in self.rows("SELECT xpi_path FROM plugin_releases"))), 2)

    def test_same_repository_name_in_different_owners_has_isolated_cache(self):
        source = "export const plugins = [" + ",".join(
            f"{{repo: '{owner}/plugin', releases: [], discoverReleases: true}}" for owner in ["one", "two"]
        ) + "]"
        one = release("v1", ("one.xpi",))
        two = release("v1", ("two.xpi",))
        # Same asset filename and tag, different URLs and plugin identities.
        two["assets"][0]["name"] = one["assets"][0]["name"] = "plugin.xpi"
        result = self.sync(source, {"one/plugin": [one], "two/plugin": [two]}, {
            one["assets"][0]["browser_download_url"]: manifest("one@example.com"),
            two["assets"][0]["browser_download_url"]: manifest("two@example.com"),
        })
        self.assertEqual(result.success_count, 2, result.failures)
        self.assertEqual(len(self.rows("SELECT DISTINCT xpi_path FROM plugin_releases")), 2)

    def test_unreleased_source_is_pending_not_a_failure(self):
        result = self.sync(catalog(), {"demo/plugin": []}, {})
        self.assertEqual((result.plugin_count, result.success_count, result.pending_count, result.failure_count),
                         (1, 0, 1, 0))
        self.assertIn("action=pending", self.output.getvalue())

    def test_monorepo_non_plugin_latest_preserves_pinned_release(self):
        pinned = release("plugin-v1")
        non_plugin = {**release("other-v2", date="2026-10-02T00:00:00Z"), "assets": []}
        result = self.sync(catalog(releases="[{tagName: 'plugin-v1'}]"),
                           {"demo/plugin": [pinned, non_plugin]},
                           {pinned["assets"][0]["browser_download_url"]: manifest()})
        self.assertEqual(result.success_count, 1, result.failures)
        self.assertEqual(self.rows("SELECT tag FROM plugin_releases"), [("plugin-v1",)])

    def test_all_unavailable_releases_preserve_previous_database_record(self):
        latest = release("v2")
        manifests = {latest["assets"][0]["browser_download_url"]: manifest()}
        self.assertEqual(self.sync(catalog(), {"demo/plugin": [latest]}, manifests).success_count, 1)
        before = self.rows("SELECT * FROM plugins")
        result = self.sync(catalog(releases="[{tagName: 'missing'}]"), {"demo/plugin": []}, manifests)
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(self.rows("SELECT * FROM plugins"), before)

    def test_monorepo_latest_zip_without_zotero_id_uses_pinned_plugin(self):
        pinned = release("plugin-v1")
        other = release("other-v2", ("other.zip",), "2026-10-02T00:00:00Z")
        result = self.sync(catalog(releases="[{tagName: 'plugin-v1'}]"),
                           {"demo/plugin": [pinned, other]}, {
                               pinned["assets"][0]["browser_download_url"]: manifest(),
                               other["assets"][0]["browser_download_url"]: {"name": "Other software", "version": "1.0"},
                           })
        self.assertEqual(result.success_count, 1, result.failures)
        self.assertEqual(self.rows("SELECT tag FROM plugin_releases"), [("plugin-v1",)])
        self.assertIn("action=skip_discovery", self.output.getvalue())

    def test_custom_same_version_refreshes_contents_and_md5(self):
        source = catalog(releases="[{tagName: 'custom', customLink: 'https://example.com/plugin.xpi'}]")
        url = "https://example.com/plugin.xpi"
        self.assertEqual(self.sync(source, {"demo/plugin": []}, {url: manifest()}).success_count, 1)
        before = self.rows("SELECT md5 FROM plugin_releases")[0][0]
        changed = {**manifest(), "description": "Changed contents, same version"}
        self.assertEqual(self.sync(source, {"demo/plugin": []}, {url: changed}).success_count, 1)
        after = self.rows("SELECT md5 FROM plugin_releases")[0][0]
        self.assertNotEqual(before, after)
        path = self.root / self.rows("SELECT xpi_path FROM plugin_releases")[0][0]
        from plugindb_sync.artifacts import read_manifest_from_xpi
        self.assertEqual(read_manifest_from_xpi(path)["description"], changed["description"])

    def test_cached_package_retains_checksum_when_database_is_rebuilt(self):
        latest = release("v2")
        manifests = {latest["assets"][0]["browser_download_url"]: manifest()}
        self.assertEqual(self.sync(catalog(), {"demo/plugin": [latest]}, manifests).success_count, 1)
        checksum = self.rows("SELECT md5 FROM plugin_releases")[0][0]
        with closing(sqlite3.connect(self.root / 'data/db/plugins.sqlite3')) as connection:
            for table in ['plugin_locales', 'plugin_releases', 'plugins']:
                connection.execute('DELETE FROM ' + table)
            connection.commit()
        self.assertEqual(self.sync(catalog(), {"demo/plugin": [latest]}, {}).success_count, 1)
        self.assertEqual(self.rows("SELECT md5 FROM plugin_releases"), [(checksum,)])

    def test_newer_custom_build_is_preferred_over_github_latest(self):
        source = catalog(releases="[{tagName: 'custom', customLink: 'https://example.com/custom.xpi'}]")
        github = release("6.0.8")
        result = self.sync(source, {"demo/plugin": [github]}, {
            github["assets"][0]["browser_download_url"]: manifest(version="6.0.8"),
            'https://example.com/custom.xpi': manifest(version="6.0.86"),
        })
        self.assertEqual(result.success_count, 1, result.failures)
        self.assertEqual(self.rows("SELECT manifest_version, asset_url FROM plugin_releases WHERE release_key='latest'"),
                         [("6.0.86", "https://example.com/custom.xpi")])
        self.assertEqual(self.rows("SELECT manifest_version FROM plugin_releases WHERE release_key='discovered@6.0.8'"),
                         [("6.0.8",)])
        raw = self.rows("SELECT manifest_json FROM plugin_releases WHERE release_key='latest'")[0][0]
        self.assertEqual(json.loads(raw)['version'], '6.0.86')

    def test_default_source_loads_both_catalogs_and_deduplicates_aliases(self):
        active = catalog(fields="aliases: ['old/plugin'],")
        legacy = catalog(repo="old/plugin", export="deprecatedPlugins")
        fetched = patch("plugindb_sync.sync.fetch_plugins_ts", side_effect=lambda url: {
            DEFAULT_PLUGINS_TS_URL: active, DEFAULT_DEPRECATED_TS_URL: legacy,
        }[url]).start()
        result = self.sync(None, {"demo/plugin": []}, {})
        self.assertEqual(result.plugin_count, 1)
        self.assertEqual(fetched.call_count, 2)
        self.assertTrue((self.root / "data/cache/deprecated.ts").is_file())

    def test_local_source_includes_sibling_legacy_and_active_only_opt_out(self):
        source_dir = self.root / "source"
        source_dir.mkdir()
        active = source_dir / "plugins.ts"
        active.write_text(catalog())
        (source_dir / "deprecated.ts").write_text(catalog(repo="legacy/plugin", export="deprecatedPlugins"))
        for include, expected in [(True, 2), (False, 1)]:
            with self.subTest(include_deprecated=include):
                result = self.sync(None, {}, {}, plugins_ts_path=active, include_deprecated=include)
                self.assertEqual(result.plugin_count, expected)
                self.assertEqual(result.pending_count, expected)

    def test_invalid_catalog_fails_before_database_creation_and_releases_lock(self):
        with self.assertRaisesRegex(ValueError, "catalog is empty"):
            self.sync("export const other = []", {}, {})
        self.assertFalse((self.root / "data/db/plugins.sqlite3").exists())
        self.assertFalse((self.root / "data/.sync.lock").exists())

    def test_draft_release_not_selected_as_latest(self):
        draft = {**release("v2", date="2026-10-02T00:00:00Z"), "draft": True}
        self.assertEqual(pick_release_for_tag([release("v1"), draft], "latest")["tag_name"], "v1")

    def test_explicit_missing_asset_fails_instead_of_picking_wrong_plugin(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            pick_xpi_asset(release("v1"), "different.xpi")

    def test_legacy_database_migration_preserves_existing_rows(self):
        path = self.root / "old.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE plugins (id TEXT PRIMARY KEY, plugin_name TEXT, sanitized_name TEXT, "
                               "source_repo TEXT, source_url TEXT, homepage_url TEXT, author TEXT, update_url TEXT, synced_at TEXT)")
            connection.execute("INSERT INTO plugins (id, plugin_name) VALUES ('existing', 'Existing')")
            connection.commit()
        engine = create_engine(f"sqlite+pysqlite:///{path}")
        self.addCleanup(engine.dispose)
        ensure_schema(engine)
        ensure_schema(engine)
        self.assertEqual(fetch_all(engine, "SELECT id, plugin_name, tags FROM plugins"), [("existing", "Existing", "[]")])


if __name__ == "__main__":
    unittest.main()
