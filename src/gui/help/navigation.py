from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import re
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import yaml
except ImportError:  # graceful fallback if PyYAML is not installed
    yaml = None  # type: ignore[assignment]


def _find_project_root() -> Path:
    """Resolve the project root via marker files, then fall back to the legacy path depth."""

    module_path = Path(__file__).resolve()
    candidate_roots = list(module_path.parents)

    for marker in ("pyproject.toml", ".git"):
        for parent in candidate_roots:
            if (parent / marker).exists():
                return parent

    if len(candidate_roots) > 3:
        return candidate_roots[3]
    return candidate_roots[-1]


_HELP_YAML_PATH = _find_project_root() / "help" / "help.yaml"


def _slugify(title: str) -> str:
    """Create a URL-safe anchor id from a title."""

    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug or "section"


def _get_help_cache_key() -> tuple[str, int]:
    """Build a cache key that changes when help.yaml changes on disk."""

    if not _HELP_YAML_PATH.exists():
        return (str(_HELP_YAML_PATH), -1)

    try:
        return (str(_HELP_YAML_PATH), _HELP_YAML_PATH.stat().st_mtime_ns)
    except Exception:
        return (str(_HELP_YAML_PATH), -2)


@lru_cache(maxsize=4)
def _load_help_content_cached(_cache_key: tuple[str, int]) -> Dict[str, Any]:
    """Load help.yaml content once per file version."""

    if not _HELP_YAML_PATH.exists():
        return {"help": {"title": "Help", "sections": []}}

    try:
        text = _HELP_YAML_PATH.read_text(encoding="utf-8")
    except Exception:
        return {"help": {"title": "Help", "sections": []}}

    if yaml is None:
        return {
            "help": {
                "title": "Help",
                "sections": [
                    {
                        "title": "Raw help.yaml",
                        "content": text,
                    }
                ],
            }
        }

    try:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("Invalid YAML structure")
        return data
    except Exception:
        return {
            "help": {
                "title": "Help",
                "sections": [
                    {
                        "title": "Raw help.yaml",
                        "content": text,
                    }
                ],
            }
        }


def load_help_content() -> Dict[str, Any]:
    """Load the help.yaml content with a graceful fallback."""

    return deepcopy(_load_help_content_cached(_get_help_cache_key()))


def prepare_related_route(route: str) -> str:
    """Add a query fallback for settings anchors so the target section can open reliably."""

    if not route:
        return ""

    parts = urlsplit(route)
    if parts.path != "/settings" or not parts.fragment:
        return route

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("section", parts.fragment)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _extract_settings_anchor(route: str) -> str:
    """Extract the referenced settings anchor from a related route."""

    if not route:
        return ""

    parts = urlsplit(route)
    if parts.path != "/settings":
        return ""

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    anchor = str(query.get("section") or parts.fragment or "").strip()
    return anchor


def prepare_help_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Prepare help sections with stable, unique anchor ids and derived route metadata."""

    prepared: List[Dict[str, str]] = []
    seen_slugs: Dict[str, int] = {}

    for section in sections:
        title = str(section.get("title") or "Section")
        base_slug = _slugify(title)
        seen_slugs[base_slug] = seen_slugs.get(base_slug, 0) + 1
        anchor_id = base_slug if seen_slugs[base_slug] == 1 else f"{base_slug}-{seen_slugs[base_slug]}"
        related_route = prepare_related_route(str(section.get("link") or "").strip())

        prepared.append(
            {
                "title": title,
                "content": str(section.get("content") or ""),
                "route": related_route,
                "anchor_id": anchor_id,
                "settings_anchor": _extract_settings_anchor(related_route),
            }
        )

    return prepared


@lru_cache(maxsize=4)
def _get_help_sections_cached(_cache_key: tuple[str, int]) -> tuple[Dict[str, str], ...]:
    """Prepare help sections once per file version."""

    payload = _load_help_content_cached(_cache_key)
    help_root = payload.get("help") or {}
    raw_sections: List[Dict[str, Any]] = help_root.get("sections") or []
    return tuple(prepare_help_sections(raw_sections))


def get_help_sections() -> List[Dict[str, str]]:
    """Return prepared help sections from help.yaml."""

    return [dict(section) for section in _get_help_sections_cached(_get_help_cache_key())]


def build_help_route_for_settings_anchor(settings_anchor: str) -> str | None:
    """Resolve the help-page route for a given settings section anchor."""

    normalized_anchor = str(settings_anchor or "").strip()
    if not normalized_anchor:
        return None

    for section in _get_help_sections_cached(_get_help_cache_key()):
        if section.get("settings_anchor") != normalized_anchor:
            continue
        help_anchor = str(section.get("anchor_id") or "").strip()
        if not help_anchor:
            return "/help"
        query = urlencode({"section": help_anchor})
        return f"/help?{query}#{help_anchor}"

    return None
