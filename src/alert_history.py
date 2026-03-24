from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from src.config import get_global_config, get_logger

if TYPE_CHECKING:
    from src.config import AppConfig, MeasurementConfig

logger = get_logger('alert_history')

DEFAULT_HISTORY_DIR = Path('data/history')
HISTORY_FILE_NAME = 'history.json'
HISTORY_STATIC_ROUTE = '/history'

_history_file_lock = threading.Lock()


def get_history_dir(config: AppConfig | MeasurementConfig | None = None) -> Path:
    """Return the configured alert history directory."""
    history_path: str | None = None

    if config is not None:
        measurement_cfg = getattr(config, 'measurement', None)
        if measurement_cfg is not None and hasattr(measurement_cfg, 'history_path'):
            history_path = str(getattr(measurement_cfg, 'history_path') or '')
        elif hasattr(config, 'history_path'):
            history_path = str(getattr(config, 'history_path') or '')

    if not history_path:
        global_config = get_global_config()
        if global_config is not None:
            history_path = str(getattr(global_config.measurement, 'history_path', '') or '')

    return Path(history_path) if history_path else DEFAULT_HISTORY_DIR


def get_history_file(config: AppConfig | MeasurementConfig | None = None) -> Path:
    """Return the history.json path for the configured alert history directory."""
    return get_history_dir(config) / HISTORY_FILE_NAME


def load_history_entries(
    *,
    history_file: Path | None = None,
    entry_type: str | None = None,
) -> list[dict[str, Any]]:
    """Load alert history entries from disk."""
    target_file = history_file or get_history_file()
    with _history_file_lock:
        entries = _load_history_entries_unlocked(target_file)

    if entry_type is None:
        return entries
    return [entry for entry in entries if entry.get('type') == entry_type]


def append_history_entry(
    entry: dict[str, Any],
    *,
    history_file: Path | None = None,
    max_entries: int = 100,
) -> list[dict[str, Any]]:
    """Append a history entry and persist the truncated history atomically."""
    target_file = history_file or get_history_file()
    target_file.parent.mkdir(parents=True, exist_ok=True)

    with _history_file_lock:
        entries = _load_history_entries_unlocked(target_file, repair=True)
        entries.append(dict(entry))
        if max_entries > 0 and len(entries) > max_entries:
            entries = entries[-max_entries:]
        _write_history_entries_unlocked(target_file, entries)
        return list(entries)


def replace_history_entries(
    entries: list[dict[str, Any]],
    *,
    history_file: Path | None = None,
) -> None:
    """Replace the history file content atomically."""
    target_file = history_file or get_history_file()
    target_file.parent.mkdir(parents=True, exist_ok=True)

    sanitized_entries = [dict(entry) for entry in entries if isinstance(entry, dict)]
    with _history_file_lock:
        _write_history_entries_unlocked(target_file, sanitized_entries)


def to_history_image_storage_path(image_file: Path, history_dir: Path | None = None) -> str:
    """Store image references as POSIX-style paths relative to the history directory."""
    base_dir = (history_dir or get_history_dir()).resolve()
    resolved_image = image_file.resolve()

    try:
        relative_path = resolved_image.relative_to(base_dir)
    except ValueError:
        relative_path = Path(image_file.name)

    return relative_path.as_posix()


def resolve_history_image_path(image_path: str | None, history_dir: Path | None = None) -> Path | None:
    """Resolve a stored image reference or history URL to a file within the history directory."""
    if not image_path:
        return None

    raw_path = str(image_path).strip()
    if not raw_path or '://' in raw_path:
        return None

    if raw_path == HISTORY_STATIC_ROUTE:
        return None

    if raw_path.startswith(f'{HISTORY_STATIC_ROUTE}/') or raw_path.startswith(f'{HISTORY_STATIC_ROUTE}\\'):
        raw_path = raw_path[len(HISTORY_STATIC_ROUTE):].lstrip('/\\')

    normalized_path = raw_path.replace('\\', '/')
    if not normalized_path or normalized_path == '.':
        return None
    base_dir = (history_dir or get_history_dir()).resolve()

    candidate = Path(normalized_path)
    candidate_paths: list[Path] = []
    if candidate.is_absolute():
        candidate_paths.append(candidate)
    else:
        candidate_paths.append((Path.cwd() / candidate))
        candidate_paths.append((base_dir / candidate))

    for candidate_path in candidate_paths:
        resolved_candidate = candidate_path.resolve()
        if _is_relative_to(resolved_candidate, base_dir):
            return resolved_candidate

    return None


def build_history_image_url(image_path: str | None, history_dir: Path | None = None) -> str:
    """Convert a stored image reference into a static URL served by NiceGUI."""
    base_dir = (history_dir or get_history_dir()).resolve()
    resolved_path = resolve_history_image_path(image_path, base_dir)
    if resolved_path is None:
        return ''

    relative_path = resolved_path.relative_to(base_dir).as_posix()
    encoded_parts = [quote(part) for part in relative_path.split('/')]
    return f"{HISTORY_STATIC_ROUTE}/{'/'.join(encoded_parts)}"


def _load_history_entries_unlocked(history_file: Path, *, repair: bool = False) -> list[dict[str, Any]]:
    if not history_file.exists():
        return []

    try:
        with history_file.open('r', encoding='utf-8') as file:
            raw_data = json.load(file)
    except json.JSONDecodeError as exc:
        logger.error('Corrupt history file %s: %s', history_file, exc)
        if repair:
            _backup_invalid_history_file(history_file)
        return []
    except Exception as exc:
        logger.error('Error loading history file %s: %s', history_file, exc)
        return []

    if not isinstance(raw_data, list):
        logger.error('History file %s content is not a list', history_file)
        if repair:
            _backup_invalid_history_file(history_file)
        return []

    return [entry for entry in raw_data if isinstance(entry, dict)]


def _write_history_entries_unlocked(history_file: Path, entries: list[dict[str, Any]]) -> None:
    temp_file = history_file.with_suffix('.json.tmp')
    with temp_file.open('w', encoding='utf-8') as file:
        json.dump(entries, file, indent=2, ensure_ascii=False)
    temp_file.replace(history_file)


def _backup_invalid_history_file(history_file: Path) -> None:
    backup_file = history_file.with_suffix('.json.bak')
    try:
        if backup_file.exists():
            backup_file.unlink()
        history_file.replace(backup_file)
    except Exception as exc:
        logger.error('Failed to back up invalid history file %s: %s', history_file, exc)


def _is_relative_to(path: Path, base_dir: Path) -> bool:
    try:
        path.relative_to(base_dir)
        return True
    except ValueError:
        return False
