import unittest

from plugindb_sync.github_client import parse_expanded_assets_html, pick_release_for_tag


class PickReleaseForTagTest(unittest.TestCase):
    def test_picks_latest_and_pre_release(self) -> None:
        releases = [
            {"tag_name": "v1.0.0-beta.1", "prerelease": True, "published_at": "2026-01-01T00:00:00Z"},
            {"tag_name": "v1.0.0", "prerelease": False, "published_at": "2026-01-02T00:00:00Z"},
            {"tag_name": "v1.1.0-beta.1", "prerelease": True, "published_at": "2026-01-03T00:00:00Z"},
        ]

        self.assertEqual(pick_release_for_tag(releases, "latest")["tag_name"], "v1.0.0")
        self.assertEqual(pick_release_for_tag(releases, "pre")["tag_name"], "v1.1.0-beta.1")

    def test_picks_exact_tag(self) -> None:
        releases = [
            {"tag_name": "v1.0.0", "prerelease": False, "published_at": "2026-01-02T00:00:00Z"},
            {"tag_name": "v1.1.0-beta.1", "prerelease": True, "published_at": "2026-01-03T00:00:00Z"},
        ]

        self.assertEqual(pick_release_for_tag(releases, "v1.0.0")["tag_name"], "v1.0.0")

    def test_parses_expanded_assets_html_for_xpi_assets(self) -> None:
        html = """
        <a href="/l0o0/jasminum/releases/download/v1.1.37/jasminum_1.1.37.xpi">jasminum_1.1.37.xpi</a>
        <a href="/l0o0/jasminum/archive/refs/tags/v1.1.37.zip">Source code</a>
        """

        assets = parse_expanded_assets_html("l0o0/jasminum", html)

        self.assertEqual(
            assets,
            [
                {
                    "name": "jasminum_1.1.37.xpi",
                    "browser_download_url": "https://github.com/l0o0/jasminum/releases/download/v1.1.37/jasminum_1.1.37.xpi",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
