from __future__ import annotations

from html.parser import HTMLParser
import json
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT = 30
GITHUB_BASE_URL = "https://github.com"


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


def _request_text(url: str) -> tuple[str, str]:
    request = Request(url, headers={"User-Agent": "plugindb-sync/0.1"})
    with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        return response.read().decode("utf-8"), response.geturl()


class _ExpandedAssetsParser(HTMLParser):
    def __init__(self, repo: str) -> None:
        super().__init__()
        self.repo = repo
        self.assets: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        prefix = f"/{self.repo}/releases/download/"
        if not href.startswith(prefix) or not href.lower().endswith(".xpi"):
            return
        name = href.rstrip("/").rsplit("/", 1)[-1]
        self.assets.append(
            {
                "name": name,
                "browser_download_url": f"{GITHUB_BASE_URL}{href}",
            }
        )


def parse_expanded_assets_html(repo: str, html: str) -> list[dict[str, Any]]:
    parser = _ExpandedAssetsParser(repo)
    parser.feed(html)
    return parser.assets


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
        try:
            data = _request_json(
                f"https://api.github.com/repos/{repo}/releases/latest",
                github_token=github_token,
            )
            if not isinstance(data, dict):
                raise ValueError(f"Expected release object for {repo}")
            return data
        except Exception:
            return fetch_latest_release_from_github_web(repo)
    if tag_name == "pre":
        return pick_release_for_tag(list_releases(repo, github_token=github_token), "pre")
    data = _request_json(
        f"https://api.github.com/repos/{repo}/releases/tags/{quote(tag_name, safe='')}",
        github_token=github_token,
    )
    if not isinstance(data, dict):
        raise ValueError(f"Expected tag release object for {repo}:{tag_name}")
    return data


def fetch_latest_release_from_github_web(repo: str) -> dict[str, Any]:
    _, final_url = _request_text(f"{GITHUB_BASE_URL}/{repo}/releases/latest")
    tag_name = urlparse(final_url).path.rstrip("/").rsplit("/", 1)[-1]
    if not tag_name or tag_name == "latest":
        raise ValueError(f"Cannot resolve latest release tag for {repo}")

    html, _ = _request_text(f"{GITHUB_BASE_URL}/{repo}/releases/expanded_assets/{quote(tag_name, safe='')}")
    assets = parse_expanded_assets_html(repo, html)
    if not assets:
        raise ValueError(f"No .xpi asset found for latest release {repo}:{tag_name}")
    return {
        "tag_name": tag_name,
        "prerelease": False,
        "published_at": "",
        "assets": assets,
    }


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
