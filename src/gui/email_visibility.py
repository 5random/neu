from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.config import EmailConfig


def _iterable_str_list(value: object) -> list[str]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return [item for item in value if isinstance(item, str)]
    return []


def _is_reserved_group_name(name: str) -> bool:
    checker = getattr(EmailConfig, 'is_reserved_group_name', None)
    if callable(checker):
        try:
            return bool(checker(name))
        except Exception:
            pass
    return name == getattr(EmailConfig, 'SYSTEM_STATIC_GROUP', '__static__')


def _normalize_visible_groups(raw_groups: Any) -> dict[str, list[str]]:
    if not isinstance(raw_groups, Mapping):
        return {}

    visible_groups: dict[str, list[str]] = {}
    for raw_name, raw_members in raw_groups.items():
        group_name = str(raw_name or '').strip()
        if not group_name or _is_reserved_group_name(group_name):
            continue
        visible_groups[group_name] = _iterable_str_list(raw_members)
    return visible_groups


def get_visible_groups(email_cfg: Any) -> dict[str, list[str]]:
    getter = getattr(email_cfg, 'get_visible_groups', None)
    if callable(getter):
        try:
            return _normalize_visible_groups(getter())
        except Exception:
            pass
    return _normalize_visible_groups(getattr(email_cfg, 'groups', {}) or {})


def get_visible_group_names(email_cfg: Any) -> list[str]:
    return list(get_visible_groups(email_cfg).keys())


def get_visible_active_groups(email_cfg: Any) -> list[str]:
    getter = getattr(email_cfg, 'get_visible_active_groups', None)
    if callable(getter):
        try:
            raw_active_groups = _iterable_str_list(getter())
        except Exception:
            raw_active_groups = []
    else:
        raw_active_groups = _iterable_str_list(getattr(email_cfg, 'active_groups', []))

    visible_names = set(get_visible_group_names(email_cfg))
    ordered_active_groups: list[str] = []
    seen: set[str] = set()
    for group_name in raw_active_groups:
        if group_name in seen or group_name not in visible_names:
            continue
        ordered_active_groups.append(group_name)
        seen.add(group_name)
    return ordered_active_groups
