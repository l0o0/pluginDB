import unittest

from plugindb_sync.plugin_source import parse_plugins_ts


class ParsePluginsTsTest(unittest.TestCase):
    def test_parses_realistic_export_with_comments_and_type_annotation(self) -> None:
        text = """
        import type { PluginInfoBase } from './types.js'

        /**
         * Plugins list
         */
        // @keep-sorted { "keys": ["repo", "tags"] }
        export const plugins: PluginInfoBase[] = [
          {
            repo: 'MuiseDestiny/ZoteroStyle',
            releases: [
              { targetZoteroVersion: '7', tagName: 'latest' }
            ],
            tags: ['style']
          }
        ]
        """

        plugins = parse_plugins_ts(text)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].repo, "MuiseDestiny/ZoteroStyle")
        self.assertEqual([item.tag_name for item in plugins[0].releases], ["latest"])
        self.assertEqual(plugins[0].releases[0].target_zotero_version, "7")

    def test_parses_repo_and_release_definitions(self) -> None:
        text = """
        export const plugins = [
          {
            name: 'Zotero Style',
            repo: 'MuiseDestiny/ZoteroStyle',
            releases: [
              { tagName: 'latest' },
              { tagName: 'pre' }
            ]
          }
        ]
        """

        plugins = parse_plugins_ts(text)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].name, "Zotero Style")
        self.assertEqual(plugins[0].repo, "MuiseDestiny/ZoteroStyle")
        self.assertEqual([item.tag_name for item in plugins[0].releases], ["latest", "pre"])

    def test_parses_custom_link_release(self) -> None:
        text = """
        export const plugins = [
          {
            repo: 'MuiseDestiny/ZoteroStyle',
            releases: [
              {
                targetZoteroVersion: '8',
                tagName: 'custom',
                customLink: 'https://gitee.com/MuiseDestiny/plugins/raw/master/zotero-style.xpi'
              }
            ]
          }
        ]
        """

        plugins = parse_plugins_ts(text)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].releases[0].tag_name, "custom")
        self.assertEqual(
            plugins[0].releases[0].custom_link,
            "https://gitee.com/MuiseDestiny/plugins/raw/master/zotero-style.xpi",
        )
        self.assertEqual(plugins[0].releases[0].target_zotero_version, "8")

    def test_ignores_entries_without_repo(self) -> None:
        text = """
        export const plugins = [
          {
            name: 'External',
            releases: [{ tagName: 'latest' }]
          },
          {
            name: 'Kept',
            repo: 'demo/repo',
            releases: [{ tagName: 'latest' }]
          }
        ]
        """

        plugins = parse_plugins_ts(text)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].repo, "demo/repo")


if __name__ == "__main__":
    unittest.main()
