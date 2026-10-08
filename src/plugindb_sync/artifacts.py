from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile


CHUNK_SIZE = 1024 * 1024
EM_NS = "http://www.mozilla.org/2004/em-rdf#"
RDF_NAMESPACES = {"em": EM_NS, "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}
RDF_NS = RDF_NAMESPACES["rdf"]
MSG_PATTERN = re.compile(r"^__MSG_([A-Za-z0-9_@.-]+)__$")



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


def _read_archive_text(archive: zipfile.ZipFile, preferred_name: str) -> str | None:
    names = archive.namelist()
    target_name = preferred_name
    if target_name not in names:
        matches = [name for name in names if name.endswith(f"/{preferred_name}") or name.endswith(preferred_name)]
        if not matches:
            return None
        target_name = matches[0]
    with archive.open(target_name) as handle:
        return handle.read().decode("utf-8")


def _message_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = MSG_PATTERN.match(value.strip())
    return match.group(1) if match else None


def _read_locale_messages(archive: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    locales: dict[str, dict[str, str]] = {}
    for name in sorted(archive.namelist()):
        parts = name.split("/")
        if len(parts) < 3 or parts[-3] != "_locales" or parts[-1] != "messages.json":
            continue
        locale = parts[-2]
        try:
            payload = json.loads(archive.read(name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        messages: dict[str, str] = {}
        for key, item in payload.items():
            if not isinstance(key, str) or not isinstance(item, dict):
                continue
            message = item.get("message")
            if isinstance(message, str) and message.strip():
                messages[key] = message.strip()
        if messages:
            locales[locale] = messages
    return locales


def _resolve_message(value: Any, messages: dict[str, str]) -> Any:
    key = _message_key(value)
    if key is None:
        return value
    return messages.get(key, value)


def _preferred_locale_messages(
    locale_messages: dict[str, dict[str, str]],
    default_locale: Any,
) -> dict[str, str]:
    preferred_locales = []
    if isinstance(default_locale, str) and default_locale.strip():
        preferred_locales.append(default_locale.strip())
    preferred_locales.extend(["en_US", "en", "zh_CN", "zh"])
    for locale in preferred_locales:
        messages = locale_messages.get(locale)
        if messages:
            return messages
    return next(iter(locale_messages.values()), {})


def _resolve_manifest_locales(manifest: dict[str, Any], archive: zipfile.ZipFile) -> dict[str, Any]:
    locale_messages = _read_locale_messages(archive)
    if not locale_messages:
        return manifest

    resolved = dict(manifest)
    default_messages = _preferred_locale_messages(locale_messages, manifest.get("default_locale"))
    for field in ("name", "description"):
        resolved[field] = _resolve_message(resolved.get(field), default_messages)

    localized: list[dict[str, Any]] = []
    for locale, messages in sorted(locale_messages.items()):
        entry = {"locale": locale}
        for field in ("name", "description"):
            value = _resolve_message(manifest.get(field), messages)
            if isinstance(value, str) and value.strip() and _message_key(value) is None:
                entry[field] = value.strip()
        if len(entry) > 1:
            localized.append(entry)
    if localized:
        existing = [item for item in resolved.get("localized", []) if isinstance(item, dict)]
        resolved["localized"] = existing + localized
    return resolved


def _find_child_text(element: ET.Element, tag: str) -> str | None:
    child = element.find(f"em:{tag}", RDF_NAMESPACES)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _find_em_value(element: ET.Element, tag: str) -> str | None:
    child_value = _find_child_text(element, tag)
    if child_value:
        return child_value

    attr_names = (f"{{{EM_NS}}}{tag}", f"em:{tag}", tag)
    for attr_name in attr_names:
        attr_value = element.attrib.get(attr_name)
        if attr_value is not None and attr_value.strip():
            return attr_value.strip()
    return None


def _find_nested_description(element: ET.Element) -> ET.Element | None:
    for child in element:
        if _local_name(child.tag) != "Description":
            continue
        return child
    return None


def _find_description_by_about(root: ET.Element, about: str) -> ET.Element | None:
    for element in root.iter():
        if _local_name(element.tag) != "Description":
            continue
        about_value = (
            element.attrib.get(f"{{{RDF_NS}}}about")
            or element.attrib.get("about")
            or element.attrib.get("RDF:about")
        )
        if about_value == about:
            return element
    return None


def _resolve_target_description(root: ET.Element, target: ET.Element) -> ET.Element | None:
    description = _find_nested_description(target)
    if description is not None:
        return description

    resource = (
        target.attrib.get(f"{{{RDF_NS}}}resource")
        or target.attrib.get("resource")
        or target.attrib.get("RDF:resource")
    )
    if resource:
        return _find_description_by_about(root, resource)
    return None


def _parse_install_rdf(text: str) -> dict[str, Any]:
    root = ET.fromstring(text)
    manifest = _find_description_by_about(root, "urn:mozilla:install-manifest")
    if manifest is None:
        raise ValueError("install.rdf does not contain install-manifest description")

    addon_id = _find_em_value(manifest, "id")
    applications: dict[str, Any] = {}

    for target in manifest.findall("em:targetApplication", RDF_NAMESPACES):
        description = _resolve_target_description(root, target)
        if description is None:
            continue
        app_id = _find_em_value(description, "id")
        if app_id == "zotero@chnm.gmu.edu":
            applications["zotero"] = {
                "id": addon_id,
                "strict_min_version": _find_em_value(description, "minVersion"),
                "strict_max_version": _find_em_value(description, "maxVersion"),
                "update_url": _find_em_value(manifest, "updateURL"),
            }

    localized: list[dict[str, Any]] = []
    for localized_node in manifest.findall("em:localized", RDF_NAMESPACES):
        description = _find_nested_description(localized_node)
        if description is None:
            continue
        entry = {
            "locale": _find_em_value(description, "locale"),
            "name": _find_em_value(description, "name"),
            "description": _find_em_value(description, "description"),
        }
        if any(entry.values()):
            localized.append(entry)

    payload = {
        "name": _find_em_value(manifest, "name"),
        "version": _find_em_value(manifest, "version"),
        "author": _find_em_value(manifest, "creator"),
        "homepage_url": _find_em_value(manifest, "homepageURL"),
        "update_url": _find_em_value(manifest, "updateURL"),
        "description": next((item["description"] for item in localized if item.get("locale") == "en-US" and item.get("description")), None)
        or next((item["description"] for item in localized if item.get("description")), None)
        or _find_em_value(manifest, "description"),
        "applications": applications,
    }
    if localized:
        payload["localized"] = localized
    return payload


def read_manifest_from_xpi(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        manifest_text = _read_archive_text(archive, "manifest.json")
        if manifest_text is not None:
            manifest = json.loads(manifest_text)
            if isinstance(manifest, dict):
                return _resolve_manifest_locales(manifest, archive)
            return manifest

        install_rdf = _read_archive_text(archive, "install.rdf")
        if install_rdf is not None:
            return _parse_install_rdf(install_rdf)

        raise FileNotFoundError(f"manifest.json or install.rdf not found in {path}")


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
