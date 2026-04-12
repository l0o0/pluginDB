from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.request import Request, urlopen
import zipfile


CHUNK_SIZE = 1024 * 1024


def sanitize_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized or "plugin"


def sanitize_tag(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized or "unknown"


def calculate_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest_from_xpi(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        target_name = "manifest.json"
        if target_name not in names:
            matches = [name for name in names if name.endswith("/manifest.json") or name.endswith("manifest.json")]
            if not matches:
                raise FileNotFoundError(f"manifest.json not found in {path}")
            target_name = matches[0]
        with archive.open(target_name) as handle:
            return json.loads(handle.read().decode("utf-8"))


def write_manifest_xpi(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def download_file(url: str, destination: Path, github_token: str | None = None, force: bool = False) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        return calculate_md5(destination)

    headers = {"User-Agent": "plugindb-sync/0.1"}
    if github_token and "github.com" in url:
        headers["Authorization"] = f"Bearer {github_token}"
    request = Request(url, headers=headers)

    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp_handle:
        tmp_path = Path(tmp_handle.name)
        try:
            with urlopen(request, timeout=60) as response:
                while chunk := response.read(CHUNK_SIZE):
                    tmp_handle.write(chunk)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    tmp_path.replace(destination)
    return calculate_md5(destination)
