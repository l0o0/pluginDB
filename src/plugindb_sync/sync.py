from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from itertools import zip_longest
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from urllib.error import HTTPError
from zipfile import BadZipFile

from .artifacts import calculate_md5, download_file, read_manifest_from_xpi, sanitize_name, sanitize_tag, write_manifest_xpi
from .config import AppPaths
from .github_client import MissingAssetError, NoReleaseError, fetch_plugins_ts, fetch_release, fetch_repo_metadata, list_releases, pick_release_for_tag, pick_xpi_asset
from .plugin_source import PluginRef, ReleaseRef, parse_plugins_ts
from .storage import create_engine, ensure_schema, find_cached_release, upsert_plugin_record


DEFAULT_PLUGINS_TS_URL = "https://raw.githubusercontent.com/zotero-chinese/zotero-plugins/main/src/plugins.ts"
DEFAULT_DEPRECATED_TS_URL = "https://raw.githubusercontent.com/zotero-chinese/zotero-plugins/main/src/deprecated.ts"


# Store removals must remain excluded even if an upstream catalog lists them again.
EXCLUDED_REPOS = frozenset({"wdcpclover/ai4paper"})


@dataclass(frozen=True)
class SyncResult:
    plugin_count: int
    success_count: int
    failure_count: int
    failures: list[str]
    pending_count: int = 0


def _load_catalog(
    paths: AppPaths,
    plugins_ts_text: str | None,
    plugins_ts_path: Path | str | None,
    plugins_url: str,
    deprecated_ts_path: Path | str | None,
    deprecated_url: str | None,
    include_deprecated: bool,
) -> list[PluginRef]:
    if plugins_ts_path is not None:
        plugins_text = Path(plugins_ts_path).read_text(encoding="utf-8")
    elif plugins_ts_text is not None:
        plugins_text = plugins_ts_text
    else:
        plugins_text = fetch_plugins_ts(plugins_url)
    plugins = parse_plugins_ts(plugins_text)
    if not plugins:
        raise ValueError("Active plugin catalog is empty or has an unsupported format")
    (paths.cache_dir / "plugins.ts").write_text(plugins_text, encoding="utf-8")

    if include_deprecated:
        if deprecated_ts_path is None and plugins_ts_path is not None:
            sibling = Path(plugins_ts_path).with_name("deprecated.ts")
            if sibling.is_file():
                deprecated_ts_path = sibling
        if (deprecated_url is None and plugins_ts_text is None
                and plugins_ts_path is None and plugins_url == DEFAULT_PLUGINS_TS_URL):
            deprecated_url = DEFAULT_DEPRECATED_TS_URL
        legacy_text = None
        if deprecated_ts_path is not None:
            legacy_text = Path(deprecated_ts_path).read_text(encoding="utf-8")
        elif deprecated_url is not None:
            legacy_text = fetch_plugins_ts(deprecated_url)
        if legacy_text is not None:
            legacy = parse_plugins_ts(legacy_text, "deprecatedPlugins")
            if not legacy:
                raise ValueError("Legacy plugin catalog is empty or has an unsupported format")
            (paths.cache_dir / "deprecated.ts").write_text(legacy_text, encoding="utf-8")
            plugins.extend(legacy)

    aliases = {alias.lower(): plugin.repo.lower() for plugin in plugins for alias in plugin.aliases}
    merged: dict[str, PluginRef] = {}
    # Prefer canonical entries over any remaining alias entries.
    for plugin in sorted(plugins, key=lambda item: item.repo.lower() in aliases):
        key = aliases.get(plugin.repo.lower(), plugin.repo.lower())
        if key not in merged:
            merged[key] = plugin
        else:
            previous = merged[key]
            merged[key] = replace(
                previous,
                releases=list(dict.fromkeys(previous.releases + plugin.releases)),
                discover_releases=previous.discover_releases or plugin.discover_releases,
                tags=list(dict.fromkeys(previous.tags + plugin.tags)),
                aliases=list(dict.fromkeys(previous.aliases + plugin.aliases)),
            )
    return [plugin for plugin in merged.values()
            if not EXCLUDED_REPOS.intersection(
                repo.lower() for repo in [plugin.repo, *plugin.aliases])]


@dataclass(frozen=True)
class ResolvedXpi:
    target_path: Path
    manifest_raw: dict[str, Any]
    md5: str


@contextmanager
def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = lock_path.open("x")
    except FileExistsError as exc:
        raise RuntimeError(f"Sync already running: {lock_path}") from exc
    try:
        fd.write(str(datetime.now(timezone.utc).isoformat()))
        fd.flush()
        yield
    finally:
        fd.close()
        lock_path.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_source_url(repo: str) -> str:
    return f"https://github.com/{repo}"


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_xpi_filename(tag: str) -> str:
    normalized = sanitize_tag(tag)
    if normalized.lower().startswith("v"):
        return normalized
    return f"v{normalized}"


def _relative_to_root(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def _log_transfer(action: str, repo: str, release_key: str, asset_url: str, target_path: Path, root: Path) -> None:
    print(
        f"action={action} "
        f"repo={repo} "
        f"release={release_key} "
        f"url={asset_url} "
        f"target={_relative_to_root(target_path, root)}"
    )


def _should_process_release(mode: str, release_ref: ReleaseRef, discover_releases: bool = False) -> bool:
    if mode == "init" or discover_releases:
        return True
    return release_ref.tag_name in {"latest", "pre", "custom"}


def _existing_cached_target(
    root: Path,
    provisional_path: Path,
    cached_release: dict[str, Any] | None,
    resolved_tag: str,
    allow_cached_path_mismatch: bool = False,
) -> Path | None:
    if cached_release and str(cached_release.get("tag") or "") != resolved_tag:
        return None
    cached_path_value = str((cached_release or {}).get("xpi_path") or "").strip()
    if cached_path_value:
        cached_path = root / cached_path_value
        if cached_path.exists() and (allow_cached_path_mismatch or cached_path == provisional_path):
            return cached_path
    if provisional_path.exists():
        return provisional_path
    return None


def _xpi_target_path(base_dir: Path, plugin: PluginRef, tag: str, asset_name: str, release_ref: ReleaseRef) -> Path:
    if plugin.discover_releases or release_ref.asset_name:
        # Repository + release + asset prevents collisions between similarly named
        # plugins and between different compatibility builds of a single release.
        owner, repo = plugin.repo.split("/", 1)
        return base_dir / sanitize_name(owner) / sanitize_name(repo) / _build_xpi_filename(tag) / sanitize_name(asset_name)
    return base_dir / sanitize_name(plugin.name) / f"{_build_xpi_filename(tag)}.xpi"


def _resolve_final_xpi_path(
    base_dir: Path,
    plugin: PluginRef,
    release_ref: Any,
    manifest: dict[str, Any],
    provisional_path: Path,
) -> Path:
    if not release_ref.custom_link:
        return provisional_path

    manifest_version = str(manifest.get("version") or "").strip()
    if not manifest_version:
        return provisional_path

    return _xpi_target_path(base_dir, plugin, manifest_version, provisional_path.name, release_ref)


def _extract_manifest_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    zotero = dict(((manifest.get("applications") or {}).get("zotero") or {}))
    return {
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "description": manifest.get("description"),
        "homepage_url": manifest.get("homepage_url"),
        "author": manifest.get("author"),
        "update_url": zotero.get("update_url"),
        "localized": manifest.get("localized") or [],
        "zotero": {
            "id": zotero.get("id"),
            "strict_min_version": zotero.get("strict_min_version"),
            "strict_max_version": zotero.get("strict_max_version"),
        },
    }


def _extract_repo_fields(repo_metadata: dict[str, Any] | None, repo: str) -> dict[str, Any]:
    payload = dict(repo_metadata or {})
    return {
        "description": payload.get("description"),
        "homepage_url": payload.get("homepage") or payload.get("html_url") or _build_source_url(repo),
        "source_url": payload.get("html_url") or _build_source_url(repo),
        "github_stars": _to_int(payload.get("stargazers_count")),
    }


def _release_key(release_ref: Any) -> str:
    target_zotero_version = str(getattr(release_ref, "target_zotero_version", "") or "").strip()
    key = release_ref.tag_name
    if target_zotero_version:
        key += f"@zotero-{target_zotero_version}"
    return key


def _materialize_test_xpi(target_path: Path, manifest: dict[str, Any]) -> None:
    write_manifest_xpi(target_path, manifest)


def _resolve_release(
    plugin: PluginRef,
    release_ref: Any,
    github_release_map: dict[str, list[dict[str, Any]]] | None,
    github_token: str | None,
) -> dict[str, Any]:
    if release_ref.custom_link:
        asset_name = Path(urlparse(release_ref.custom_link).path).name or f"{sanitize_name(plugin.name)}.xpi"
        return {
            "tag_name": release_ref.tag_name,
            "prerelease": False,
            "published_at": "",
            "assets": [
                {
                    "name": asset_name,
                    "browser_download_url": release_ref.custom_link,
                    "download_count": 0,
                }
            ],
        }
    if github_release_map is not None:
        releases = github_release_map.get(plugin.repo, [])
        return pick_release_for_tag(releases, release_ref.tag_name)
    return fetch_release(plugin.repo, release_ref.tag_name, github_token=github_token)


def _resolve_xpi(
    asset_url: str,
    target_path: Path,
    downloaded_xpi_manifests: dict[str, dict[str, Any]] | None,
    github_token: str | None,
    force: bool = False,
) -> tuple[dict[str, Any], str]:
    if downloaded_xpi_manifests is not None:
        manifest = downloaded_xpi_manifests[asset_url]
        _materialize_test_xpi(target_path, manifest)
        return manifest, calculate_md5(target_path)

    md5 = download_file(asset_url, target_path, github_token=github_token, force=force)
    manifest = read_manifest_from_xpi(target_path)
    return manifest, md5


def _resolve_repo_metadata(
    plugin: PluginRef,
    github_repo_map: dict[str, dict[str, Any]] | None,
    github_token: str | None,
) -> dict[str, Any]:
    if github_repo_map is not None:
        return dict(github_repo_map.get(plugin.repo, {}))
    try:
        return fetch_repo_metadata(plugin.repo, github_token=github_token)
    except Exception:
        return {}


def _build_locales(manifest: dict[str, Any], repo_fields: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    candidates = [
        ("und", "description", "manifest", manifest.get("description")),
        ("und", "description", "github_repo", repo_fields.get("description")),
    ]
    for locale, field, source, value in candidates:
        text_value = str(value or "").strip()
        if not text_value:
            continue
        key = (locale, field, source, text_value)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "locale": locale,
                "field": field,
                "source": source,
                "value": text_value,
            }
        )

    for localized_entry in manifest.get("localized", []):
        locale = str(localized_entry.get("locale") or "und").strip() or "und"
        for field in ("name", "description"):
            text_value = str(localized_entry.get(field) or "").strip()
            if not text_value:
                continue
            key = (locale, field, "manifest", text_value)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "locale": locale,
                    "field": field,
                    "source": "manifest",
                    "value": text_value,
                }
            )
    return items


def _compare_manifest_versions(left: str, right: str) -> int:
    # Match upstream's Mozilla-style comparison: numeric parts sort numerically,
    # and a stable numeric component sorts after a prerelease label.
    for a, b in zip_longest(re.split(r"[.-]", left.lstrip("vV")),
                          re.split(r"[.-]", right.lstrip("vV")), fillvalue="0"):
        if "*" in (a, b):
            continue
        x = (1, int(a)) if a.isdigit() else (0, a.casefold())
        y = (1, int(b)) if b.isdigit() else (0, b.casefold())
        if x != y:
            return 1 if x > y else -1
    return 0


def _build_plugin_record(
    plugin: PluginRef,
    release_payloads: dict[str, dict[str, Any]],
    repo_fields: dict[str, Any],
    synced_at: str,
) -> dict[str, Any]:
    preferred_release = release_payloads.get("latest") or release_payloads.get("pre")
    if not preferred_release:
        preferred_release = next(iter(release_payloads.values()))
    if plugin.discover_releases:
        stable = [item for item in release_payloads.values()
                  if not item["prerelease"] and item["manifest_version"]]
        if stable:
            best = stable[0]
            for item in stable[1:]:
                if _compare_manifest_versions(item["manifest_version"], best["manifest_version"]) > 0:
                    best = item
            preferred_release = best
            discovered = release_payloads.get("latest")
            if discovered is not None and discovered is not best:
                # Soil prefers the `latest` key. A custom/pinned build may be
                # newer than GitHub's latest release; retain both downloads.
                release_payloads = dict(release_payloads)
                if not any(key != "latest" and item["asset_url"] == discovered["asset_url"]
                           for key, item in release_payloads.items()):
                    release_payloads[f"discovered@{discovered['tag']}"] = discovered
                release_payloads["latest"] = best

    manifest_payload = dict(preferred_release["manifest"])
    plugin_id = manifest_payload["zotero"]["id"]
    if not plugin_id:
        raise ValueError(f"Missing Zotero plugin id for {plugin.repo}")

    json_releases = {}
    for key, item in release_payloads.items():
        json_releases[key] = {
            "tag": item["tag"],
            "target_zotero_version": item.get("target_zotero_version"),
            "prerelease": item["prerelease"],
            "published_at": item["published_at"],
            "asset_name": item["asset_name"],
            "asset_url": item["asset_url"],
            "xpi_path": item["xpi_path"],
            "md5": item["md5"],
            "download_count": item["download_count"],
            "manifest_version": item["manifest_version"],
            "manifest_min_zotero_version": item["manifest_min_zotero_version"],
            "manifest_max_zotero_version": item["manifest_max_zotero_version"],
            "manifest_json": item["manifest_json"],
        }

    unique_download_counts = {
        item["asset_url"]: _to_int(item.get("download_count"))
        for item in release_payloads.values()
    }

    return {
        "id": plugin_id,
        "plugin_name": manifest_payload.get("name") or plugin.name,
        "sanitized_name": sanitize_name(plugin.name),
        "source_repo": plugin.repo,
        "source_url": repo_fields["source_url"],
        "homepage_url": manifest_payload.get("homepage_url") or repo_fields["homepage_url"],
        "author": manifest_payload.get("author"),
        "update_url": manifest_payload.get("update_url"),
        "tags": plugin.tags,
        "github_stars": repo_fields.get("github_stars") or 0,
        "download_count": sum(unique_download_counts.values()),
        "releases": json_releases,
        "locales": _build_locales(manifest_payload, repo_fields),
        "synced_at": synced_at,
    }


def run_sync(
    root: Path | str,
    mode: str = "sync",
    plugins_ts_text: str | None = None,
    plugins_ts_path: Path | str | None = None,
    github_release_map: dict[str, list[dict[str, Any]]] | None = None,
    github_repo_map: dict[str, dict[str, Any]] | None = None,
    downloaded_xpi_manifests: dict[str, dict[str, Any]] | None = None,
    github_token: str | None = None,
    plugins_url: str = DEFAULT_PLUGINS_TS_URL,
    database_url: str | None = None,
    deprecated_ts_path: Path | str | None = None,
    deprecated_url: str | None = None,
    include_deprecated: bool = True,
) -> SyncResult:
    project_root = Path(root)
    paths = AppPaths.from_root(project_root)
    paths.ensure_directories()
    synced_at = _utc_now()
    failures: list[str] = []
    success_count = 0
    pending_count = 0

    with _file_lock(paths.lock_path):
        plugins = _load_catalog(paths, plugins_ts_text, plugins_ts_path, plugins_url,
                                deprecated_ts_path, deprecated_url, include_deprecated)
        engine = create_engine(database_url or paths.default_database_url())
        try:
            ensure_schema(engine)
            processed_xpi_urls: dict[str, ResolvedXpi] = {}
            for plugin in plugins:
                try:
                    repo_fields = _extract_repo_fields(
                        _resolve_repo_metadata(plugin, github_repo_map, github_token),
                        plugin.repo,
                    )
                    release_payloads: dict[str, dict[str, Any]] = {}
                    release_refs = [ref for ref in plugin.releases
                                    if _should_process_release(mode, ref, plugin.discover_releases)]
                    discovered_ref = None
                    discovered_release = None
                    resolved_releases: dict[tuple[str, str | None], dict[str, Any]] = {}
                    if plugin.discover_releases:
                        releases = (github_release_map.get(plugin.repo, []) if github_release_map is not None
                                    else list_releases(plugin.repo, github_token))
                        try:
                            discovered_release = pick_release_for_tag(releases, "latest")
                        except NoReleaseError:
                            pass
                        if discovered_release is not None:
                            hint = next((ref.asset_name for ref in plugin.releases if ref.asset_name), None)
                            try:
                                discovered_asset = pick_xpi_asset(discovered_release, hint, allow_fallback=True)
                            except ValueError:
                                if not release_refs:
                                    raise
                                # A monorepo's newest release may not be a Zotero plugin.
                                discovered_release = None
                            else:
                                discovered_ref = ReleaseRef("latest", asset_name=discovered_asset["name"])
                                release_refs = [discovered_ref] + [ref for ref in release_refs if _release_key(ref) != "latest"]
                                resolved_releases[("latest", None)] = discovered_release
                                resolved_releases[(str(discovered_release["tag_name"]), None)] = discovered_release
                        if not release_refs:
                            pending_count += 1
                            print(f"action=pending repo={plugin.repo} reason=no_stable_release")
                            continue
                    for release_ref in release_refs:
                        try:
                            release_key = _release_key(release_ref)
                            selector = (release_ref.tag_name, release_ref.custom_link)
                            if selector not in resolved_releases:
                                resolved_releases[selector] = _resolve_release(plugin, release_ref, github_release_map, github_token)
                            release = resolved_releases[selector]
                            asset = pick_xpi_asset(release, release_ref.asset_name)
                            if release_key in release_payloads and release_payloads[release_key]["asset_url"] != asset["browser_download_url"]:
                                release_key += f"#asset={asset['name']}"
                            provisional_target_path = _xpi_target_path(
                                paths.xpi_dir, plugin, str(release['tag_name']), str(asset['name']), release_ref,
                            )
                            cached_release = find_cached_release(engine, plugin.repo, release_key)
                            if cached_release is None:
                                for alias in plugin.aliases:
                                    cached_release = find_cached_release(engine, alias, release_key)
                                    if cached_release is not None:
                                        break
                            asset_url = str(asset["browser_download_url"])
                            existing_target_path = None if release_ref.custom_link else _existing_cached_target(
                                project_root,
                                provisional_target_path,
                                cached_release,
                                str(release["tag_name"]),
                                False,
                            )
                            is_duplicate_url = asset_url in processed_xpi_urls
                            try:
                                if is_duplicate_url:
                                    resolved_xpi = processed_xpi_urls[asset_url]
                                    target_path = resolved_xpi.target_path
                                    manifest_raw = resolved_xpi.manifest_raw
                                    md5 = resolved_xpi.md5
                                    _log_transfer(
                                        "skip_duplicate",
                                        plugin.repo,
                                        release_key,
                                        asset_url,
                                        target_path,
                                        project_root,
                                    )
                                elif existing_target_path is not None:
                                    _log_transfer(
                                        "skip",
                                        plugin.repo,
                                        release_key,
                                        asset_url,
                                        existing_target_path,
                                        project_root,
                                    )
                                    target_path = existing_target_path
                                    manifest_raw = read_manifest_from_xpi(target_path)
                                    md5 = str((cached_release or {}).get("md5") or calculate_md5(target_path))
                                else:
                                    manifest_raw, md5 = _resolve_xpi(
                                        asset_url,
                                        provisional_target_path,
                                        downloaded_xpi_manifests,
                                        github_token,
                                        force=bool(release_ref.custom_link),
                                    )
                                    target_path = provisional_target_path
                                manifest = _extract_manifest_fields(manifest_raw)
                                if not manifest["zotero"]["id"]:
                                    raise ValueError(f"Missing Zotero plugin id for {plugin.repo}")
                            except (ValueError, FileNotFoundError, BadZipFile) as exc:
                                if release_ref is discovered_ref and len(release_refs) > 1:
                                    print(f"action=skip_discovery repo={plugin.repo} reason={exc}")
                                    continue
                                raise
                            final_target_path = _resolve_final_xpi_path(
                                paths.xpi_dir,
                                plugin,
                                release_ref,
                                manifest_raw,
                                provisional_target_path,
                            )
                            if not is_duplicate_url and existing_target_path is None and final_target_path != target_path:
                                final_target_path.parent.mkdir(parents=True, exist_ok=True)
                                if target_path.exists():
                                    target_path.replace(final_target_path)
                                target_path = final_target_path
                            if not is_duplicate_url and existing_target_path is None:
                                _log_transfer(
                                    "download",
                                    plugin.repo,
                                    release_key,
                                    asset_url,
                                    target_path,
                                    project_root,
                                )
                            processed_xpi_urls[asset_url] = ResolvedXpi(
                                target_path=target_path,
                                manifest_raw=manifest_raw,
                                md5=md5,
                            )
                            release_payloads[release_key] = {
                                "tag": str(release["tag_name"]),
                                "target_zotero_version": release_ref.target_zotero_version,
                                "prerelease": bool(release.get("prerelease")),
                                "published_at": str(release.get("published_at") or ""),
                                "asset_name": str(asset["name"]),
                                "asset_url": str(asset["browser_download_url"]),
                                "xpi_path": _relative_to_root(target_path, project_root),
                                "md5": md5,
                                "download_count": _to_int(asset.get("download_count")),
                                "manifest_version": str(manifest.get("version") or ""),
                                "manifest_min_zotero_version": manifest["zotero"].get("strict_min_version"),
                                "manifest_max_zotero_version": manifest["zotero"].get("strict_max_version"),
                                "manifest_json": manifest_raw,
                                "manifest_json_text": json.dumps(manifest_raw, ensure_ascii=False, sort_keys=True),
                                "manifest": manifest,
                            }
                        except (MissingAssetError, NoReleaseError, HTTPError) as exc:
                            if not plugin.discover_releases or (isinstance(exc, HTTPError) and exc.code != 404):
                                raise
                            print(f"action=unavailable_release repo={plugin.repo} release={_release_key(release_ref)} reason={exc}")

                    if not release_payloads:
                        if release_refs:
                            raise ValueError(f"No usable plugin artifacts for {plugin.repo}")
                        continue

                    record = _build_plugin_record(plugin, release_payloads, repo_fields, synced_at)
                    json_path = paths.json_dir / f"{sanitize_name(record['id'])}.json"
                    json_path.write_text(
                        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8",
                    )
                    upsert_plugin_record(engine, record)
                    success_count += 1
                except Exception as exc:
                    failures.append(f"{plugin.repo}: {exc}")
        finally:
            engine.dispose()

    return SyncResult(
        plugin_count=len(plugins),
        success_count=success_count,
        failure_count=len(failures),
        failures=failures,
        pending_count=pending_count,
    )
