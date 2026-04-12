from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ReleaseRef:
    tag_name: str


@dataclass(frozen=True)
class PluginRef:
    name: str
    repo: str
    releases: list[ReleaseRef]


_FIELD_PATTERN = r"{field}\s*:\s*(['\"])(.*?)\1"


def _extract_bracketed(text: str, start_index: int, open_char: str, close_char: str) -> tuple[str, int]:
    if text[start_index] != open_char:
        raise ValueError(f"Expected {open_char!r} at index {start_index}")
    depth = 0
    in_string = False
    quote_char = ""
    escaped = False
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote_char:
                in_string = False
            continue
        if char in ("'", '"'):
            in_string = True
            quote_char = char
            continue
        if char == open_char:
            depth += 1
            continue
        if char == close_char:
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1], index + 1
    raise ValueError(f"Unbalanced {open_char!r}{close_char!r} pair")


def _extract_object_literals(text: str) -> list[str]:
    objects: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "{":
            block, index = _extract_bracketed(text, index, "{", "}")
            objects.append(block)
            continue
        index += 1
    return objects


def _match_field(text: str, field: str) -> str | None:
    match = re.search(_FIELD_PATTERN.format(field=re.escape(field)), text, re.DOTALL)
    return match.group(2).strip() if match else None


def _parse_release_refs(plugin_block: str) -> list[ReleaseRef]:
    releases_match = re.search(r"releases\s*:\s*\[", plugin_block)
    if not releases_match:
        return [ReleaseRef(tag_name="latest")]
    releases_text, _ = _extract_bracketed(plugin_block, releases_match.end() - 1, "[", "]")
    refs: list[ReleaseRef] = []
    for release_block in _extract_object_literals(releases_text):
        tag_name = _match_field(release_block, "tagName")
        if tag_name:
            refs.append(ReleaseRef(tag_name=tag_name))
    return refs or [ReleaseRef(tag_name="latest")]


def parse_plugins_ts(text: str) -> list[PluginRef]:
    export_match = re.search(r"export\s+const\s+plugins\b[^=]*=\s*\[", text, re.DOTALL)
    if not export_match:
        return []
    array_start = export_match.end() - 1
    array_text, _ = _extract_bracketed(text, array_start, "[", "]")

    plugins: list[PluginRef] = []
    for plugin_block in _extract_object_literals(array_text):
        repo = _match_field(plugin_block, "repo")
        if not repo:
            continue
        name = _match_field(plugin_block, "name") or repo.split("/")[-1]
        plugins.append(
            PluginRef(
                name=name,
                repo=repo,
                releases=_parse_release_refs(plugin_block),
            )
        )
    return plugins
