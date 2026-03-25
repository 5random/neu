from nicegui import ui, app
import uuid
from typing import List, Dict, Any
from src.alert_history import (
    HISTORY_STATIC_ROUTE,
    build_history_image_url,
    get_history_dir,
    get_history_file,
    load_history_entries,
    parse_history_timestamp,
    replace_history_entries,
    resolve_history_image_path,
)
from src.config import get_logger
from src.gui.settings_elements.ui_helpers import SECTION_ICONS, create_action_button, create_heading_row

logger = get_logger('gui.history')

def create_history_card() -> None:
    """Creates a card displaying the alert history from JSON."""
    
    history_dir = get_history_dir()
    history_file = get_history_file()
    MAX_ENTRIES = 50  # Pagination limit
    
    def load_history() -> List[Dict[str, Any]]:
        try:
            data = load_history_entries(history_file=history_file, entry_type='alert')
            valid_entries = []
            for entry in data:
                row = dict(entry)
                
                # Ensure ID exists
                if 'id' not in row:
                    row['id'] = str(uuid.uuid4())
                
                dt = parse_history_timestamp(row.get('timestamp'))
                if dt is None:
                    ts_str = row.get('timestamp')
                    logger.warning(f"Skipping entry with invalid timestamp format: {ts_str}")
                    continue

                # Store parsed object for sorting, keep string for display
                row['_dt'] = dt
                row['image_url'] = build_history_image_url(row.get('image_path'), history_dir)
                valid_entries.append(row)

            # Sort by datetime descending
            valid_entries.sort(key=lambda x: x['_dt'], reverse=True)
            
            # Remove temporary sort key
            for entry in valid_entries:
                del entry['_dt']
            
            # Pagination: Return only top MAX_ENTRIES
            return valid_entries[:MAX_ENTRIES]

        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []

    def refresh_table(*, notify: bool = False) -> None:
        rows = load_history()
        table.rows = rows
        table.update()
        if notify:
            if not rows:
                ui.notify("No history found", type="info")
            else:
                ui.notify(f"Loaded latest {len(rows)} entries", type="positive")

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
            <q-td key="image" :props="props">
                <q-img 
                    v-if="props.row.image_url"
                    :src="props.row.image_url" 
                    spinner-color="primary" 
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

        # Keep the table in sync with the chart without notification spam.
        ui.timer(5.0, refresh_table)

        # Initial load
        refresh_table()

