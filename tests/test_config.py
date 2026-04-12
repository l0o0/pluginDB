from pathlib import Path
import unittest

from plugindb_sync.config import AppPaths


class AppPathsTest(unittest.TestCase):
    def test_builds_expected_default_directories(self) -> None:
        root = Path("/tmp/pluginDB")
        paths = AppPaths.from_root(root)

        self.assertEqual(paths.root, root)
        self.assertEqual(paths.docs_dir, root / "docs")
        self.assertEqual(paths.json_dir, root / "data" / "json")
        self.assertEqual(paths.xpi_dir, root / "data" / "xpi")
        self.assertEqual(paths.db_path, root / "data" / "db" / "plugins.sqlite3")


if __name__ == "__main__":
    unittest.main()
