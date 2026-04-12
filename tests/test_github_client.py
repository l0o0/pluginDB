import unittest

from plugindb_sync.github_client import pick_release_for_tag


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


if __name__ == "__main__":
    unittest.main()
