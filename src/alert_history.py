from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote

from src.config import get_global_config, get_logger

if TYPE_CHECKING:
    from src.config import AppConfig, MeasurementConfig

logger = get_logger('alert_history')

DEFAULT_HISTORY_DIR = Path('data/history')
HISTORY_FILE_NAME = 'history.json'
HISTORY_STATIC_ROUTE = '/history'
MAX_HISTORY_ENTRIES = 100
MAX_HISTORY_IMAGE_FILES = 25
MAX_HISTORY_FILE_SIZE_BYTES = 10 * 1024 * 1024
ALERT_IMAGE_PREFIX = 'alert_'
ALERT_IMAGE_EXTENSIONS = frozenset({'.jpg', '.jpeg', '.png'})

_history_file_lock = threading.Lock()
_history_revisions: dict[str, int] = {}
_history_listeners: dict[str, list[Callable[[int], None]]] = {}


def _history_revision_key(history_file: Path) -> str:
    return str(Path(history_file).resolve(strict=False))


def _bump_history_revision_unlocked(history_file: Path) -> int:
    key = _history_revision_key(history_file)
    next_revision = _history_revisions.get(key, 0) + 1
    _history_revisions[key] = next_revision
    return next_revision


def get_history_revision(*, history_file: Path | None = None) -> int:
    """Return the in-process revision counter for the given history file."""
    target_file = history_file or get_history_file()
    with _history_file_lock:
        return _history_revisions.get(_history_revision_key(target_file), 0)


def register_history_listener(listener: Callable[[int], None], *, history_file: Path | None = None) -> None:
    """Register a listener that is notified when the given history file changes."""
    target_file = history_file or get_history_file()
    key = _history_revision_key(target_file)
    with _history_file_lock:
        listeners = _history_listeners.setdefault(key, [])
        if listener not in listeners:
            listeners.append(listener)


def unregister_history_listener(listener: Callable[[int], None], *, history_file: Path | None = None) -> None:
    """Remove a previously registered history change listener."""
    target_file = history_file or get_history_file()
    key = _history_revision_key(target_file)
    with _history_file_lock:
        listeners = _history_listeners.get(key)
        if not listeners:
            return
        try:
            listeners.remove(listener)
        except ValueError:
            return
        if not listeners:
            _history_listeners.pop(key, None)


def _notify_history_listeners(history_file: Path, revision: int) -> None:
    key = _history_revision_key(history_file)
    with _history_file_lock:
        listeners = list(_history_listeners.get(key, []))
    for listener in listeners:
        try:
            listener(revision)
        except Exception:
            logger.exception('Failed to notify history listener')


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


def parse_history_timestamp(timestamp: Any) -> datetime | None:
    """Parse supported history timestamp formats into a datetime object."""
    if timestamp is None:
        return None

    ts_str = str(timestamp).strip()
    if not ts_str:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue

    return None


def append_history_entry(
    entry: dict[str, Any],
    *,
    history_file: Path | None = None,
    max_entries: int = MAX_HISTORY_ENTRIES,
    pending_image_filename: str | None = None,
    pending_image_bytes: bytes | None = None,
) -> list[dict[str, Any]]:
    """Append a history entry and persist the truncated history atomically."""
    target_file = history_file or get_history_file()
    target_file.parent.mkdir(parents=True, exist_ok=True)

    revision = 0
    entries_snapshot: list[dict[str, Any]] = []
    entry_to_store = dict(entry)
    created_image_path: Path | None = None
    with _history_file_lock:
        try:
            if pending_image_filename is not None or pending_image_bytes is not None:
                if not pending_image_filename or pending_image_bytes is None:
                    raise ValueError(
                        'pending_image_filename and pending_image_bytes must be provided together'
                    )
                stored_image_path, created_image_path = _write_pending_history_image_unlocked(
                    target_file.parent,
                    image_filename=pending_image_filename,
                    image_bytes=pending_image_bytes,
                )
                entry_to_store['image_path'] = stored_image_path

            entries = _load_history_entries_unlocked(target_file, repair=True)
            entries.append(entry_to_store)
            entries = _prepare_history_entries_for_storage_unlocked(
                entries,
                history_file=target_file,
                max_entries=max_entries,
            )
            _write_history_entries_unlocked(target_file, entries)
            _cleanup_orphaned_history_images_unlocked(target_file.parent, entries)
            revision = _bump_history_revision_unlocked(target_file)
            entries_snapshot = list(entries)
        except Exception:
            if created_image_path is not None:
                try:
                    created_image_path.unlink(missing_ok=True)
                except Exception as cleanup_exc:
                    logger.warning(
                        'Failed to remove uncommitted history image %s: %s',
                        created_image_path,
                        cleanup_exc,
                    )
            raise

    _notify_history_listeners(target_file, revision)
    return entries_snapshot


def replace_history_entries(
    entries: list[dict[str, Any]],
    *,
    history_file: Path | None = None,
) -> None:
    """Replace the history file content atomically."""
    target_file = history_file or get_history_file()
    target_file.parent.mkdir(parents=True, exist_ok=True)

    revision = 0
    with _history_file_lock:
        sanitized_entries = _prepare_history_entries_for_storage_unlocked(
            entries,
            history_file=target_file,
            max_entries=MAX_HISTORY_ENTRIES,
        )
        _write_history_entries_unlocked(target_file, sanitized_entries)
        _cleanup_orphaned_history_images_unlocked(target_file.parent, sanitized_entries)
        revision = _bump_history_revision_unlocked(target_file)

    _notify_history_listeners(target_file, revision)


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


def _prepare_history_entries_for_storage_unlocked(
    entries: list[dict[str, Any]],
    *,
    history_file: Path,
    max_entries: int,
) -> list[dict[str, Any]]:
    history_dir = history_file.parent
    sanitized_entries = [dict(entry) for entry in entries if isinstance(entry, dict)]
    _normalize_history_image_paths_unlocked(sanitized_entries, history_dir)

    if max_entries > 0 and len(sanitized_entries) > max_entries:
        trimmed_count = len(sanitized_entries) - max_entries
        sanitized_entries = _retain_newest_history_entries_unlocked(sanitized_entries, max_entries)
        logger.info(
            'Trimmed %s oldest history entries to enforce max entry limit (%s)',
            trimmed_count,
            max_entries,
        )

    _enforce_history_image_limit_unlocked(sanitized_entries)
    return _enforce_history_file_size_limit_unlocked(sanitized_entries, history_file=history_file)


def _normalize_history_image_paths_unlocked(entries: list[dict[str, Any]], history_dir: Path) -> None:
    for entry in entries:
        stored_image_path = entry.get('image_path')
        if not stored_image_path:
            entry['image_path'] = ''
            continue

        resolved_image_path = resolve_history_image_path(str(stored_image_path), history_dir)
        if resolved_image_path is None:
            logger.warning('Discarding invalid history image path: %r', stored_image_path)
            entry['image_path'] = ''
            continue

        entry['image_path'] = to_history_image_storage_path(resolved_image_path, history_dir)


def _history_entry_recency_key(entry: dict[str, Any], index: int) -> tuple[bool, datetime, int]:
    parsed_timestamp = parse_history_timestamp(entry.get('timestamp'))
    return (
        parsed_timestamp is not None,
        parsed_timestamp or datetime.min,
        index,
    )


def _select_newest_history_entry_indexes_unlocked(entries: list[dict[str, Any]], keep_count: int) -> set[int]:
    if keep_count <= 0:
        return set()

    return set(
        sorted(
            range(len(entries)),
            key=lambda index: _history_entry_recency_key(entries[index], index),
            reverse=True,
        )[:keep_count]
    )


def _retain_newest_history_entries_unlocked(
    entries: list[dict[str, Any]],
    keep_count: int,
) -> list[dict[str, Any]]:
    keep_indexes = _select_newest_history_entry_indexes_unlocked(entries, keep_count)
    return [
        entry
        for index, entry in enumerate(entries)
        if index in keep_indexes
    ]


def _pop_oldest_history_entry_unlocked(entries: list[dict[str, Any]]) -> bool:
    if not entries:
        return False

    oldest_index = min(
        range(len(entries)),
        key=lambda index: _history_entry_recency_key(entries[index], index),
    )
    entries.pop(oldest_index)
    return True


def _enforce_history_image_limit_unlocked(entries: list[dict[str, Any]]) -> None:
    image_entry_indexes = [
        index
        for index, entry in enumerate(entries)
        if str(entry.get('image_path') or '').strip()
    ]
    if MAX_HISTORY_IMAGE_FILES < 0 or len(image_entry_indexes) <= MAX_HISTORY_IMAGE_FILES:
        return

    keep_indexes = (
        set(
            sorted(
                image_entry_indexes,
                key=lambda index: _history_entry_recency_key(entries[index], index),
                reverse=True,
            )[:MAX_HISTORY_IMAGE_FILES]
        )
        if MAX_HISTORY_IMAGE_FILES > 0 else set()
    )
    cleared_count = 0
    for index in image_entry_indexes:
        if index in keep_indexes:
            continue
        if entries[index].get('image_path'):
            entries[index]['image_path'] = ''
            cleared_count += 1

    if cleared_count > 0:
        logger.info(
            'Cleared %s older history image reference(s) to enforce image limit (%s)',
            cleared_count,
            MAX_HISTORY_IMAGE_FILES,
        )


def _enforce_history_file_size_limit_unlocked(
    entries: list[dict[str, Any]],
    *,
    history_file: Path,
) -> list[dict[str, Any]]:
    if MAX_HISTORY_FILE_SIZE_BYTES <= 0:
        return entries

    limited_entries = list(entries)
    removed_count = 0
    while len(limited_entries) > 1 and _serialized_history_entries_size_bytes(limited_entries) > MAX_HISTORY_FILE_SIZE_BYTES:
        if not _pop_oldest_history_entry_unlocked(limited_entries):
            break
        removed_count += 1

    if removed_count > 0:
        logger.info(
            'Trimmed %s oldest history entries to enforce file size limit (%s bytes)',
            removed_count,
            MAX_HISTORY_FILE_SIZE_BYTES,
        )

    if (
        limited_entries
        and _serialized_history_entries_size_bytes(limited_entries) > MAX_HISTORY_FILE_SIZE_BYTES
    ):
        logger.warning(
            'History file %s exceeds %s bytes even with a single entry; keeping newest entry',
            history_file,
            MAX_HISTORY_FILE_SIZE_BYTES,
        )

    return limited_entries


def _cleanup_orphaned_history_images_unlocked(history_dir: Path, entries: list[dict[str, Any]]) -> None:
    referenced_images: set[Path] = set()
    for entry in entries:
        resolved_image_path = resolve_history_image_path(entry.get('image_path'), history_dir)
        if resolved_image_path is not None:
            referenced_images.add(resolved_image_path.resolve(strict=False))

    removed_count = 0
    for image_file in _iter_history_image_files(history_dir):
        resolved_image = image_file.resolve(strict=False)
        if resolved_image in referenced_images:
            continue
        try:
            image_file.unlink(missing_ok=True)
            removed_count += 1
        except Exception as exc:
            logger.warning('Failed to remove orphaned history image %s: %s', image_file, exc)

    if removed_count > 0:
        logger.info('Removed %s orphaned history image file(s)', removed_count)


def _iter_history_image_files(history_dir: Path):
    if not history_dir.exists():
        return

    for candidate in history_dir.rglob('*'):
        if (
            candidate.is_file()
            and candidate.name.startswith(ALERT_IMAGE_PREFIX)
            and candidate.suffix.lower() in ALERT_IMAGE_EXTENSIONS
        ):
            yield candidate


def _serialize_history_entries(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, indent=2, ensure_ascii=False)


def _serialized_history_entries_size_bytes(entries: list[dict[str, Any]]) -> int:
    return len(_serialize_history_entries(entries).encode('utf-8'))


def _write_pending_history_image_unlocked(
    history_dir: Path,
    *,
    image_filename: str,
    image_bytes: bytes,
) -> tuple[str, Path]:
    raw_filename = str(image_filename or '').strip()
    if not raw_filename:
        raise ValueError('pending history image filename must not be empty')

    resolved_history_dir = history_dir.resolve(strict=False)
    resolved_image_path = (history_dir / raw_filename).resolve(strict=False)
    if not _is_relative_to(resolved_image_path, resolved_history_dir):
        raise ValueError(
            f"refusing to write history image outside history directory: {resolved_image_path}"
        )

    image_temp_path = resolved_image_path.with_suffix(f'{resolved_image_path.suffix}.tmp')
    resolved_image_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        image_temp_path.write_bytes(image_bytes)
        image_temp_path.replace(resolved_image_path)
    except Exception:
        try:
            image_temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    return to_history_image_storage_path(resolved_image_path, history_dir), resolved_image_path


def _write_history_entries_unlocked(history_file: Path, entries: list[dict[str, Any]]) -> None:
    temp_file = history_file.with_suffix('.json.tmp')
    serialized_entries = _serialize_history_entries(entries).encode('utf-8')
    with temp_file.open('wb') as file:
        file.write(serialized_entries)
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
