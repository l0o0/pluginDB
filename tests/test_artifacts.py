import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from plugindb_sync.artifacts import calculate_md5, read_manifest_from_xpi, sanitize_name, sanitize_tag


class ArtifactsTest(unittest.TestCase):
    def test_reads_install_rdf_when_manifest_json_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            xpi_path = Path(tmp_dir) / "legacy.xpi"
            install_rdf = """<?xml version="1.0" encoding="utf-8"?>
            <RDF xmlns="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                 xmlns:em="http://www.mozilla.org/2004/em-rdf#">
              <Description about="urn:mozilla:install-manifest">
                <em:id>jasminum@linxzh.com</em:id>
                <em:name>Jasminum</em:name>
                <em:version>0.3.2</em:version>
                <em:updateURL>https://raw.githubusercontent.com/l0o0/jasminum/master/update.rdf</em:updateURL>
                <em:creator>Xingzhong Lin</em:creator>
                <em:targetApplication>
                  <Description>
                    <em:id>zotero@chnm.gmu.edu</em:id>
                    <em:minVersion>5.0.0</em:minVersion>
                    <em:maxVersion>6.*</em:maxVersion>
                  </Description>
                </em:targetApplication>
                <em:localized>
                  <Description>
                    <em:locale>en-US</em:locale>
                    <em:name>Jasminum</em:name>
                    <em:description>A simple Add-on to enhance Chinese user experience.</em:description>
                  </Description>
                </em:localized>
                <em:localized>
                  <Description>
                    <em:locale>zh-CN</em:locale>
                    <em:name>Jasminum</em:name>
                    <em:description>一个简单的 Zotero 中文插件</em:description>
                  </Description>
                </em:localized>
              </Description>
            </RDF>
            """
            with zipfile.ZipFile(xpi_path, "w") as archive:
                archive.writestr("install.rdf", install_rdf)

            payload = read_manifest_from_xpi(xpi_path)

            self.assertEqual(payload["name"], "Jasminum")
            self.assertEqual(payload["version"], "0.3.2")
            self.assertEqual(payload["author"], "Xingzhong Lin")
            self.assertEqual(payload["applications"]["zotero"]["id"], "jasminum@linxzh.com")
            self.assertEqual(payload["applications"]["zotero"]["strict_min_version"], "5.0.0")
            self.assertEqual(payload["applications"]["zotero"]["strict_max_version"], "6.*")
            self.assertEqual(
                payload["localized"][1]["description"],
                "一个简单的 Zotero 中文插件",
            )

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
