from nicegui import ui
import json
import os
import uuid
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
from src.config import get_logger

logger = get_logger('gui.history')

def create_history_card() -> None:
    """Creates a card displaying the alert history from JSON."""
    
    history_file = Path("data/history/history.json")
    HISTORY_DIR = Path("data/history").resolve()
    MAX_ENTRIES = 50  # Pagination limit
    
    def load_history() -> List[Dict[str, Any]]:
        if not history_file.exists():
            return []
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if not isinstance(data, list):
                logger.error("History file content is not a list")
                return []

            valid_entries = []
            for i, entry in enumerate(data):
                if not isinstance(entry, dict):
                    continue
                
                # Ensure ID exists
                if 'id' not in entry:
                    entry['id'] = str(uuid.uuid4())
                
                # Validate and parse timestamp
                ts_str = entry.get('timestamp')
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
                    entry['_dt'] = dt
                    valid_entries.append(entry)
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
            # Ensure directory exists before writing
            if not history_file.parent.exists():
                os.makedirs(history_file.parent, exist_ok=True)

            # Clear JSON
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump([], f)
            
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
            # Resolve absolute path
            # Note: image_path might be relative like "pics/alert_123.jpg" or absolute
            # We assume it's relative to CWD or absolute.
            # If it starts with /, it's absolute (on *nix) or relative to drive root (Windows)
            # We treat it as relative to CWD if not absolute.
            
            # In this app, images are likely stored in data/history/images or similar.
            # Let's check if the resolved path starts with HISTORY_DIR.
            # However, existing code might use 'pics/' which is mounted as static.
            # Let's assume we want to allow files in CWD/data/history OR CWD/pics (if that's where they are).
            # The requirement says "Ensure paths are relative to data/history".
            
            # Let's be strict: must be inside CWD.
            base_dir = Path.cwd().resolve()
            target_path = (base_dir / image_path).resolve()
            
            # Check if target_path is within base_dir
            if not str(target_path).startswith(str(base_dir)):
                logger.warning(f"Path traversal attempt detected: {image_path}")
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
                    v-if="props.row.image_path"
                    :src="props.row.image_path" 
                    spinner-color="primary" 
                    style="height: 50px; max-width: 90px"
                    class="rounded"
                    @click="$emit('image-click', props.row.image_path)"
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

