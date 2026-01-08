from __future__ import annotations
from pathlib import Path
import tempfile
import zipfile
import shutil
import os
import logging
import queue
from typing import Optional, Any

from nicegui import ui, app

from src.config import get_logger

logger = get_logger('gui.logs')


class UILogHandler(logging.Handler):
    """Logging handler that pushes formatted log records into a thread-safe queue.

    The queue is drained into a NiceGUI ui.log() widget by a UI timer on the client.
    """

    def __init__(self, msg_queue: queue.Queue[str], level: int = logging.INFO, fmt: Optional[logging.Formatter] = None):
        super().__init__(level)
        self.queue = msg_queue
        if fmt is not None:
            self.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            # Fallback to basic formatting if custom formatter fails
            msg = f"{record.levelname} - {record.name} - {record.getMessage()}"
        try:
            # If the queue is full, drop the oldest item to avoid blocking producers
            try:
                if self.queue.full():
                    _ = self.queue.get_nowait()
            except Exception:
                pass
            self.queue.put_nowait(msg)
        except Exception:
            # As a last resort, ignore to avoid breaking logging
            pass


def _read_tail(file_path: Path, max_lines: int = 200) -> list[str]:
    """Return the last up to max_lines of the given text file, or an empty list if missing."""
    try:
        if not file_path.exists():
            return []
        # Read safely; file is typically small enough. For large files, this is acceptable for tail size.
        with file_path.open('r', encoding='utf-8', errors='ignore') as f:
            lines = f.read().splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


def create_log_settings() -> None:
    """Render controls to package and download logs and show a live log viewer."""

    def ensure_logs_static_mapping(logs_dir: Path) -> None:
        # Ensure static mapping exists (idempotent)
        try:
            app.add_static_files('/logs', str(logs_dir))
        except Exception:
            pass

    def download_logs_as_zip() -> None:
        logs_dir = Path('logs')
        if not logs_dir.exists():
            ui.notify('No log directory found', type='warning', position='bottom-right')
            return

        ensure_logs_static_mapping(logs_dir)

        # Create temporary ZIP file
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_file:
            zip_path = tmp_file.name

        try:
            log_files_found = 0
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Include all rotated log files (e.g., cvd_tracker.log, cvd_tracker.log.1, etc.)
                for log_file in logs_dir.glob('cvd_tracker.log*'):
                    if log_file.is_file():
                        zipf.write(log_file, log_file.name)
                        log_files_found += 1
                        logger.debug('Added %s to ZIP archive', log_file.name)

            if log_files_found <= 0:
                ui.notify('No log files found', type='warning', position='bottom-right')
                return

            final_zip_path = logs_dir / 'cvd_tracker_logs.zip'
            # Move temporary ZIP into logs folder so the static route can serve it
            shutil.move(zip_path, final_zip_path)

            ui.download.from_url('/logs/cvd_tracker_logs.zip')
            ui.notify(f'{log_files_found} log files packaged and downloaded', type='positive', position='bottom-right')
            logger.info('Created ZIP archive with %d log files', log_files_found)
        except Exception as e:
            logger.error('Error creating log ZIP archive: %s', e)
            ui.notify('Error creating log archive', type='negative', position='bottom-right')
        finally:
            # Clean up temp file if it still exists
            try:
                if os.path.exists(zip_path):
                    os.unlink(zip_path)
            except Exception:
                pass

    # --- UI Layout ---
    with ui.column().classes('gap-3'):
        ui.label('Logs').classes('text-subtitle1 font-semibold').props('id=logs')
        ui.label('Package and download recent application logs for troubleshooting.').classes('text-body2')
        with ui.row().classes('gap-2'):
            ui.button('Download Logs (ZIP)', icon='download', on_click=download_logs_as_zip).props('color=primary')

        ui.separator()
        ui.label('Live Log (from cvd_tracker)').classes('text-subtitle1 font-semibold')

        # Controls row
        with ui.row().classes('items-center gap-3'):
            paused_switch = ui.switch('Pause').props('color=primary')
            clear_btn = ui.button('Clear', icon='clear')
            # Level filter: affects our handler's level (cannot raise events logged below logger level)
            level_select = ui.select(
                {"DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL"},
                value="INFO",
                with_input=False,
            ).props('label=Level dense outlined style="min-width: 160px;"')

        # The live log widget
        live_log = ui.log(max_lines=2000).classes('w-full h-72')

        # Initialize: tail existing file
        logs_dir = Path('logs')
        ensure_logs_static_mapping(logs_dir)
        current_log_file = logs_dir / 'cvd_tracker.log'
        initial_lines = _read_tail(current_log_file, max_lines=200)
        for line in initial_lines:
            live_log.push(line)

        # Queue and handler per client
        msg_queue: queue.Queue[str] = queue.Queue(maxsize=5000)

        # Set up a formatter consistent with file log lines
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%d.%m.%Y %H:%M:%S'
        )
        ui_handler = UILogHandler(msg_queue, level=logging.INFO, fmt=formatter)

        # Attach to multiple relevant loggers to capture broader output
        target_loggers: list[logging.Logger] = [
            logging.getLogger(),  # root
            logging.getLogger('cvd_tracker'),
            logging.getLogger('camera'),
        ]
        for lg in target_loggers:
            try:
                lg.addHandler(ui_handler)
            except Exception:
                pass

        # Keep reference on client to remove on disconnect
        client = ui.context.client
        # Remove any previous handlers this client added to avoid duplicates on re-render
        try:
            prev = getattr(client, 'cvd_ui_log_handlers', [])
            for lg, h in prev:
                try:
                    lg.removeHandler(h)
                except Exception:
                    pass
        except Exception:
            pass
        client.cvd_ui_log_handlers = [(lg, ui_handler) for lg in target_loggers]  # type: ignore[attr-defined]

        # Wire controls
        def on_clear() -> None:
            live_log.clear()

        def on_level_change(e: Any) -> None:
            level_name = str(e.value)
            level = getattr(logging, level_name, logging.INFO)
            ui_handler.setLevel(level)

        clear_btn.on('click', on_clear)
        level_select.on('update:model-value', on_level_change)

        # Drain queue into the UI in the client context
        def drain_queue() -> None:
            if paused_switch.value:
                return
            drained = 0
            # Limit per tick to avoid UI flooding
            while drained < 200 and not msg_queue.empty():
                try:
                    msg = msg_queue.get_nowait()
                except Exception:
                    break
                live_log.push(msg)
                drained += 1

        # Ensure only one drain timer per client; cancel a previous one if present
        try:
            prev_timer = getattr(client, 'cvd_logs_timer', None)
            if prev_timer:
                try:
                    prev_timer.cancel()
                except Exception:
                    pass
        except Exception:
            pass

        timer = ui.timer(0.25, drain_queue)
        try:
            setattr(client, 'cvd_logs_timer', timer)
        except Exception:
            pass

        # Ensure handler is removed when the client disconnects to prevent duplicates/leaks
        def _cleanup_on_disconnect() -> None:
            try:
                timer.cancel()
            except Exception:
                pass
            try:
                if getattr(client, 'cvd_logs_timer', None) is timer:
                    delattr(client, 'cvd_logs_timer')
            except Exception:
                pass
            try:
                # remove handlers we added
                for lg, h in getattr(client, 'cvd_ui_log_handlers', []):
                    try:
                        lg.removeHandler(h)
                    except Exception:
                        pass
            except Exception:
                pass

        client.on_disconnect(_cleanup_on_disconnect)
