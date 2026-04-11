from nicegui import ui, app
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Any
from src.alert_history import (
    HISTORY_STATIC_ROUTE,
    build_history_image_url,
    get_history_dir,
    get_history_file,
    get_history_revision,
    load_history_entries,
    parse_history_timestamp,
    register_history_listener,
    replace_history_entries,
    resolve_history_image_path,
    unregister_history_listener,
)
from src.config import get_logger
from src.gui.ui_helpers import SECTION_ICONS, create_action_button, create_heading_row
from src.gui.util import register_client_disconnect_handler

logger = get_logger('gui.history')


def _history_row_id(entry: Dict[str, Any], occurrence_index: int) -> str:
    existing_id = str(entry.get('id') or '').strip()
    if existing_id:
        return existing_id

    stable_source = {
        key: value
        for key, value in entry.items()
        if key not in {'id', 'image_url', '_dt'}
    }
    payload = json.dumps(stable_source, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]
    return f'{digest}-{occurrence_index}'

def _build_history_rows(
    entries: List[Dict[str, Any]],
    *,
    history_dir: Path,
    max_entries: int,
) -> List[Dict[str, Any]]:
    valid_entries: list[dict[str, Any]] = []
    occurrence_counts: dict[str, int] = {}
    for entry in entries:
        row = dict(entry)

        dt = parse_history_timestamp(row.get('timestamp'))
        if dt is None:
            ts_str = row.get('timestamp')
            logger.warning(f"Skipping entry with invalid timestamp format: {ts_str}")
            continue

        stable_source = {
            key: value
            for key, value in row.items()
            if key not in {'id', 'image_url', '_dt'}
        }
        payload = json.dumps(stable_source, sort_keys=True, ensure_ascii=False, default=str)
        occurrence_index = occurrence_counts.get(payload, 0)
        occurrence_counts[payload] = occurrence_index + 1
        row['id'] = _history_row_id(row, occurrence_index)
        row['_dt'] = dt
        row['image_url'] = build_history_image_url(row.get('image_path'), history_dir)
        valid_entries.append(row)

    valid_entries.sort(key=lambda x: x['_dt'], reverse=True)

    for entry in valid_entries:
        del entry['_dt']

    return valid_entries[:max(0, int(max_entries))]


_HISTORY_ROW_COMPARE_KEYS = ('id', 'timestamp', 'image_url', 'image_path', 'session_id')


def _rows_changed(
    old_rows: List[Dict[str, Any]],
    new_rows: List[Dict[str, Any]],
) -> bool:
    """Return True when the visible row data has actually changed."""
    if len(old_rows) != len(new_rows):
        return True
    for old, new in zip(old_rows, new_rows):
        for key in _HISTORY_ROW_COMPARE_KEYS:
            if old.get(key) != new.get(key):
                return True
    return False


def _build_row_fingerprint(rows: List[Dict[str, Any]]) -> str:
    """Build a compact fingerprint of the displayed rows for fast comparison."""
    parts = []
    for row in rows:
        parts.append(f"{row.get('id')}|{row.get('timestamp')}|{row.get('image_url')}|{row.get('session_id')}")
    return '\n'.join(parts)


def create_history_card(*, max_entries: int = 5) -> None:
    """Create a card displaying the alert history from JSON."""

    history_dir = get_history_dir()
    history_file = get_history_file()
    last_history_revision = get_history_revision(history_file=history_file)
    # Track the fingerprint of currently displayed rows to skip unnecessary rebuilds
    current_fingerprint = ''
    # Keep a reference to the currently displayed rows for external access
    current_rows: list[dict[str, Any]] = []

    def load_history() -> List[Dict[str, Any]]:
        try:
            data = load_history_entries(history_file=history_file, entry_type='alert')
            return _build_history_rows(data, history_dir=history_dir, max_entries=max_entries)
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []

    def _rebuild_rows_ui(rows: List[Dict[str, Any]]) -> None:
        """Clear and rebuild the history rows container with fresh data."""
        nonlocal current_fingerprint, current_rows
        rows_container.clear()
        current_rows = list(rows)
        current_fingerprint = _build_row_fingerprint(rows)

        if not rows:
            with rows_container:
                ui.label('No alert history entries.').classes('text-caption text-grey-6 q-pa-sm')
            return

        with rows_container:
            # Table header
            with ui.row().classes('w-full items-center gap-0 text-caption font-bold text-grey-7') \
                    .style('border-bottom: 1px solid rgba(0,0,0,0.12); padding: 8px 12px;'):
                ui.label('Time').classes('flex-1')
                ui.label('Session').classes('flex-1')
                ui.label('Image').classes('text-center').style('width: 100px;')

            # Data rows
            for row in rows:
                _build_single_row(row)

    def _build_single_row(row: Dict[str, Any]) -> None:
        """Render a single history row with its image."""
        with ui.row().classes('w-full items-center gap-0') \
                .style('border-bottom: 1px solid rgba(0,0,0,0.06); padding: 6px 12px; min-height: 60px;'):
            ui.label(str(row.get('timestamp', '-'))).classes('flex-1 text-caption')
            ui.label(str(row.get('session_id', '-'))).classes('flex-1 text-caption')

            image_url = row.get('image_url', '')
            with ui.element('div').classes('text-center').style('width: 100px;'):
                if image_url:
                    img = (
                        ui.image(image_url)
                        .style('height: 50px; max-width: 90px; object-fit: cover; cursor: pointer; border-radius: 4px;')
                        .props('no-spinner no-transition')
                    )
                    img.on('click', lambda e, url=image_url: open_image(url))
                else:
                    ui.label('-').classes('text-caption text-grey')

    def refresh_display(*, notify: bool = False, revision: int | None = None) -> None:
        nonlocal last_history_revision
        revision_snapshot = get_history_revision(history_file=history_file) if revision is None else revision
        rows = load_history()

        # Compare fingerprints to skip rebuilds when the data hasn't changed.
        new_fingerprint = _build_row_fingerprint(rows)
        if not notify and new_fingerprint == current_fingerprint:
            last_history_revision = revision_snapshot
            return

        logger.debug(
            'History display rebuild: notify=%s, revision=%s, rows=%s',
            notify, revision_snapshot, len(rows),
        )
        _rebuild_rows_ui(rows)
        last_history_revision = revision_snapshot

        if notify:
            if not rows:
                ui.notify("No history found", type="info")
            else:
                ui.notify(f"Loaded latest {len(rows)} entries", type="positive")

    def _handle_history_changed(revision: int) -> None:
        if revision <= last_history_revision:
            return
        refresh_display(revision=revision)

    def clear_history() -> None:
        try:
            replace_history_entries([], history_file=history_file)

            # TODO: Decide on image deletion policy.
            # Currently preserving files to prevent accidental data loss.
            # Future: Move to 'deleted' folder or implement hard delete with confirmation.

            refresh_display()
            ui.notify("History cleared", type="positive")
        except Exception as e:
            ui.notify(f"Error clearing history: {e}", type="negative")

    def download_history() -> None:
        try:
            if not history_file.exists():
                ui.notify("No history file found", type="warning")
                return

            history_dir.mkdir(parents=True, exist_ok=True)
            try:
                app.add_static_files(HISTORY_STATIC_ROUTE, str(history_dir))
            except Exception:
                pass

            ui.download.from_url(f'{HISTORY_STATIC_ROUTE}/{history_file.name}')
            ui.notify("history.json downloaded", type="positive")
        except Exception as e:
            logger.error(f"Error downloading history file: {e}")
            ui.notify("Error downloading history.json", type="negative")

    def validate_image_path(image_path: str) -> bool:
        """
        Validates that the image path is within the allowed history directory.
        Prevents Path Traversal attacks.
        """
        if not image_path:
            return False

        try:
            if resolve_history_image_path(image_path, history_dir) is None:
                logger.warning(f"Invalid history image path: {image_path}")
                return False
            return True
        except Exception as e:
            logger.error(f"Path validation error: {e}")
            return False

    def open_image(image_path: str) -> None:
        if not image_path:
            ui.notify("No image path provided", type="warning")
            return

        if not validate_image_path(image_path):
            ui.notify("Invalid image path", type="negative")
            return

        try:
            with ui.dialog().classes('w-full') as dialog, ui.card().classes('w-full'):
                ui.image(image_path).classes('w-full')
                ui.button('Close', on_click=dialog.close).classes('w-full')
            dialog.open()
        except Exception as e:
            logger.error(f"Error opening image dialog: {e}")
            ui.notify("Error opening image", type="negative")

    with ui.card().classes('w-full h-full'):
        with ui.row().classes('w-full items-center justify-between gap-2 flex-wrap'):
            create_heading_row(
                'Alert History',
                icon=SECTION_ICONS['history'],
                title_classes='text-h6',
                row_classes='items-center gap-2',
                icon_classes='text-primary text-xl shrink-0',
            )
            with ui.row().classes('gap-2 flex-wrap'):
                ui.button(icon='download', on_click=download_history).props('flat round').tooltip('Download history.json')
                ui.button(icon='refresh', on_click=lambda: refresh_display(notify=True)).props('flat round').tooltip('Refresh')
                create_action_button('clear', label='Delete History', icon='delete', on_click=clear_history)
        ui.label(f'Showing latest {max(0, int(max_entries))} alerts').classes('text-caption text-grey-7')

        # Container for history rows – managed manually to avoid Quasar table
        # re-rendering which causes image flicker.
        rows_container = ui.column().classes('w-full gap-0')

        def _unregister_history_updates() -> None:
            unregister_history_listener(_handle_history_changed, history_file=history_file)

        try:
            client = ui.context.client
        except Exception:
            client = None

        if client is not None:
            previous_cleanup = getattr(client, 'cvd_history_card_listener_cleanup', None)
            if callable(previous_cleanup):
                previous_cleanup()

        register_history_listener(_handle_history_changed, history_file=history_file)

        if client is not None:
            setattr(client, 'cvd_history_card_listener_cleanup', _unregister_history_updates)

            def _cleanup_on_disconnect() -> None:
                _unregister_history_updates()
                try:
                    if getattr(client, 'cvd_history_card_listener_cleanup', None) is _unregister_history_updates:
                        delattr(client, 'cvd_history_card_listener_cleanup')
                except Exception:
                    pass

            register_client_disconnect_handler(client, _cleanup_on_disconnect, logger=logger)

        # Initial load
        refresh_display(revision=get_history_revision(history_file=history_file))
