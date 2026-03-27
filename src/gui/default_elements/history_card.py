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


def create_history_card(*, max_entries: int = 5) -> None:
    """Create a card displaying the alert history from JSON."""

    history_dir = get_history_dir()
    history_file = get_history_file()
    last_history_revision = get_history_revision(history_file=history_file)

    def load_history() -> List[Dict[str, Any]]:
        try:
            data = load_history_entries(history_file=history_file, entry_type='alert')
            return _build_history_rows(data, history_dir=history_dir, max_entries=max_entries)
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []

    def refresh_table(*, notify: bool = False, revision: int | None = None) -> None:
        nonlocal last_history_revision
        revision_snapshot = get_history_revision(history_file=history_file) if revision is None else revision
        rows = load_history()

        # Skip the expensive table.update() when the visible data hasn't changed.
        # This prevents Vue/Quasar from destroying and re-creating <q-img> slots
        # which would cause the alert images to flicker/reload.
        if not notify and not _rows_changed(table.rows, rows):
            last_history_revision = revision_snapshot
            return

        table.rows = rows
        table.update()
        last_history_revision = revision_snapshot
        if notify:
            if not rows:
                ui.notify("No history found", type="info")
            else:
                ui.notify(f"Loaded latest {len(rows)} entries", type="positive")

    def _handle_history_changed(revision: int) -> None:
        if revision <= last_history_revision:
            return
        refresh_table(revision=revision)

    def clear_history() -> None:
        try:
            replace_history_entries([], history_file=history_file)
            
            # TODO: Decide on image deletion policy. 
            # Currently preserving files to prevent accidental data loss.
            # Future: Move to 'deleted' folder or implement hard delete with confirmation.
            
            refresh_table()
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
                ui.button(icon='refresh', on_click=lambda: refresh_table(notify=True)).props('flat round').tooltip('Refresh')
                create_action_button('clear', label='Clear History', icon='delete', on_click=clear_history)
        ui.label(f'Showing latest {max(0, int(max_entries))} alerts').classes('text-caption text-grey-7')

        # Columns for the table
        columns: list[dict[str, Any]] = [
            {'name': 'timestamp', 'label': 'Time', 'field': 'timestamp', 'sortable': True, 'align': 'left'},
            {'name': 'session_id', 'label': 'Session', 'field': 'session_id', 'sortable': True, 'align': 'left'},
            {'name': 'image', 'label': 'Image', 'field': 'image_path', 'align': 'center'},
        ]

        # Use 'id' as unique row key
        table = ui.table(columns=columns, rows=[], row_key='id').classes('w-full')
        
        # Add slot for image preview
        table.add_slot('body-cell-image', r'''
            <q-td :key="'img-' + props.row.id" :props="props">
                <q-img 
                    v-if="props.row.image_url"
                    :key="props.row.id"
                    :src="props.row.image_url" 
                    no-spinner
                    loading="lazy"
                    style="height: 50px; max-width: 90px"
                    class="rounded"
                    @click="$emit('image-click', props.row.image_url)"
                >
                    <template v-slot:error>
                        <div class="absolute-full flex flex-center bg-negative text-white">
                            Error
                        </div>
                    </template>
                </q-img>
                <span v-else>-</span>
            </q-td>
        ''')
        
        # Handle image click to show full size
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

        def handle_image_click(e: Any) -> None:
            if not e or not e.args:
                return
            
            # Safe extraction
            path = e.args
            # If args is a list/tuple (standard event args), take first
            if isinstance(path, (list, tuple)) and len(path) > 0:
                path = path[0]
            
            if isinstance(path, str):
                open_image(path)
            else:
                logger.warning(f"Invalid image path in event: {path}")

        table.on('image-click', handle_image_click)

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

            client.on_disconnect(_cleanup_on_disconnect)

        # Initial load
        refresh_table(revision=get_history_revision(history_file=history_file))

