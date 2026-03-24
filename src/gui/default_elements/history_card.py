from nicegui import ui
import uuid
from typing import List, Dict, Any
from datetime import datetime
from src.alert_history import (
    build_history_image_url,
    get_history_dir,
    get_history_file,
    load_history_entries,
    replace_history_entries,
    resolve_history_image_path,
)
from src.config import get_logger

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
                
                # Validate and parse timestamp
                ts_str = row.get('timestamp')
                if not ts_str:
                    continue
                
                try:
                    # Try parsing ISO 8601 formats
                    # Attempt 1: "YYYY-MM-DD HH:MM:SS" (legacy/current)
                    try:
                        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        # Attempt 2: "YYYY-MM-DDTHH:MM:SS" (strict ISO)
                        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                    
                    # Store parsed object for sorting, keep string for display
                    row['_dt'] = dt
                    row['image_url'] = build_history_image_url(row.get('image_path'), history_dir)
                    valid_entries.append(row)
                except ValueError:
                    logger.warning(f"Skipping entry with invalid timestamp format: {ts_str}")
                    continue

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

    def refresh_table() -> None:
        rows = load_history()
        table.rows = rows
        table.update()
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
        with ui.row().classes('w-full items-center justify-between'):
            ui.label('Alert History').classes('text-h6')
            with ui.row().classes('gap-2'):
                ui.button(icon='refresh', on_click=refresh_table).props('flat round').tooltip('Refresh')
                ui.button(icon='delete', color='negative', on_click=clear_history).props('flat round').tooltip('Clear History')

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

        # Initial load
        refresh_table()

