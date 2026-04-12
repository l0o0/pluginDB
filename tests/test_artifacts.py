import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from plugindb_sync.artifacts import calculate_md5, read_manifest_from_xpi, sanitize_name, sanitize_tag


class ArtifactsTest(unittest.TestCase):
    def test_reads_manifest_and_md5_from_xpi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            xpi_path = Path(tmp_dir) / "demo.xpi"
            manifest = {
                "name": "Demo",
                "version": "1.2.3",
                "applications": {
                    "zotero": {
                        "id": "demo@example.com",
                        "strict_min_version": "7.0",
                        "strict_max_version": "8.*",
                    }
                },
            }
            with zipfile.ZipFile(xpi_path, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))

            self.assertEqual(read_manifest_from_xpi(xpi_path)["version"], "1.2.3")
            self.assertEqual(calculate_md5(xpi_path), hashlib.md5(xpi_path.read_bytes()).hexdigest())

    def test_sanitizes_name_and_tag(self) -> None:
        self.assertEqual(sanitize_name("Better Notes / Test"), "Better_Notes_Test")
        self.assertEqual(sanitize_tag("release/v1.2.3 beta"), "release_v1.2.3_beta")
        self.assertEqual(sanitize_tag("v1.2.3"), "v1.2.3")


if __name__ == "__main__":
    unittest.main()
