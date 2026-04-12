from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT = 30


def _request_json(url: str, github_token: str | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "plugindb-sync/0.1",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_plugins_ts(url: str) -> str:
    request = Request(url, headers={"User-Agent": "plugindb-sync/0.1"})
    with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        return response.read().decode("utf-8")


def list_releases(repo: str, github_token: str | None = None) -> list[dict[str, Any]]:
    data = _request_json(f"https://api.github.com/repos/{repo}/releases", github_token=github_token)
    if not isinstance(data, list):
        raise ValueError(f"Expected release list for {repo}")
    return data


def fetch_repo_metadata(repo: str, github_token: str | None = None) -> dict[str, Any]:
    data = _request_json(f"https://api.github.com/repos/{repo}", github_token=github_token)
    if not isinstance(data, dict):
        raise ValueError(f"Expected repo object for {repo}")
    return data


def fetch_release(repo: str, tag_name: str, github_token: str | None = None) -> dict[str, Any]:
    if tag_name == "latest":
        data = _request_json(
            f"https://api.github.com/repos/{repo}/releases/latest",
            github_token=github_token,
        )
        if not isinstance(data, dict):
            raise ValueError(f"Expected release object for {repo}")
        return data
    if tag_name == "pre":
        return pick_release_for_tag(list_releases(repo, github_token=github_token), "pre")
    data = _request_json(
        f"https://api.github.com/repos/{repo}/releases/tags/{quote(tag_name, safe='')}",
        github_token=github_token,
    )
    if not isinstance(data, dict):
        raise ValueError(f"Expected tag release object for {repo}:{tag_name}")
    return data


def pick_release_for_tag(releases: list[dict[str, Any]], tag_name: str) -> dict[str, Any]:
    if tag_name == "latest":
        candidates = [item for item in releases if not bool(item.get("prerelease"))]
    elif tag_name == "pre":
        candidates = [item for item in releases if bool(item.get("prerelease"))]
    else:
        candidates = [item for item in releases if item.get("tag_name") == tag_name]

    if not candidates:
        raise ValueError(f"No release found for {tag_name}")

    candidates.sort(key=lambda item: str(item.get("published_at") or item.get("created_at") or ""), reverse=True)
    return candidates[0]


def pick_xpi_asset(release: dict[str, Any]) -> dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Release assets missing")

    candidates = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        lowered = name.lower()
        if not lowered.endswith(".xpi"):
            continue
        penalty = 0
        for marker in ("source", "debug", "symbols"):
            if marker in lowered:
                penalty += 1
        candidates.append((penalty, len(name), asset))

    if not candidates:
        raise ValueError(f"No .xpi asset found for release {release.get('tag_name')}")

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]
