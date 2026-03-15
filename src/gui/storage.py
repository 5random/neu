from __future__ import annotations

from typing import Any
import uuid

from nicegui import app

APP_RUNTIME_ID = uuid.uuid4().hex


def get_ui_storage() -> Any:
    """Return the persistent storage used for UI preferences."""
    return app.storage.user


def get_ui_pref(key: str, default: Any = None) -> Any:
    """Safely read a persisted UI preference."""
    try:
        return get_ui_storage().get(key, default)
    except Exception:
        return default


def set_ui_pref(key: str, value: Any) -> bool:
    """Safely persist a UI preference."""
    try:
        get_ui_storage()[key] = value
        return True
    except Exception:
        return False


def delete_ui_pref(key: str) -> bool:
    """Safely remove a persisted UI preference."""
    try:
        storage = get_ui_storage()
        if key in storage:
            del storage[key]
        return True
    except Exception:
        return False


def get_runtime_ui_pref(key: str, default: Any = None) -> Any:
    """Read a UI preference that should only live for the current app runtime."""
    value = get_ui_pref(key)
    if not isinstance(value, dict):
        return default
    if value.get('runtime_id') != APP_RUNTIME_ID:
        return default
    return value.get('value', default)


def set_runtime_ui_pref(key: str, value: Any) -> bool:
    """Persist a UI preference that resets automatically after an app restart."""
    return set_ui_pref(key, {
        'runtime_id': APP_RUNTIME_ID,
        'value': value,
    })
