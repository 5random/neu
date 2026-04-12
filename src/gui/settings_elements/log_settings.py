from __future__ import annotations
from collections import deque
from pathlib import Path
import tempfile
import zipfile
import shutil
import os
import logging
import queue
import re
from typing import Optional, Any

from nicegui import ui, app

from src.config import get_logger
from src.gui.settings_elements.ui_helpers import create_action_button, create_heading_row, create_section_heading
from src.gui.util import register_client_disconnect_handler

logger = get_logger('gui.logs')
LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}
LOG_LEVEL_PATTERN = re.compile(r'\s-\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s-\s')


class UILogHandler(logging.Handler):
    """Logging handler that pushes formatted log records into a thread-safe queue.

    The queue is drained into a NiceGUI ui.log() widget by a UI timer on the client.
    """

    def __init__(self, msg_queue: queue.Queue[tuple[int, str]], level: int = logging.DEBUG, fmt: Optional[logging.Formatter] = None):
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
            self.queue.put_nowait((record.levelno, msg))
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


def _extract_level_from_line(line: str) -> int:
    """Extract the logging level from a formatted log line."""
    match = LOG_LEVEL_PATTERN.search(line)
    if not match:
        return logging.INFO
    return LOG_LEVELS.get(match.group(1), logging.INFO)


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
    with ui.column().classes('w-full gap-4 items-stretch'):
        create_section_heading(
            'Logs',
            icon='receipt_long',
            caption='Package and download recent application logs for troubleshooting.',
            anchor_id='logs',
            title_classes='text-subtitle1 font-semibold',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )
        with ui.row().classes('w-full items-center gap-2'):
            ui.button('Download Logs (ZIP)', icon='download', on_click=download_logs_as_zip).props('color=primary')

        ui.separator()
        create_heading_row(
            'Live Log (from cvd_tracker)',
            icon='terminal',
            title_classes='text-subtitle1 font-semibold',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-lg shrink-0',
        )

        # Controls row
        with ui.row().classes('w-full items-center gap-3 flex-wrap'):
            paused_switch = ui.switch('Pause').props('color=primary')
            auto_scroll_switch = ui.switch('Auto-scroll', value=True).props('color=positive')
            clear_btn = create_action_button('clear')
            level_select = ui.select(
                list(LOG_LEVELS.keys()),
                value='INFO',
                with_input=False,
            ).props('label=Level dense outlined').classes('min-w-[180px]')

        # The live log widget
        with ui.element('div').classes('w-full min-w-0'):
            live_log = ui.log(max_lines=2000).classes('w-full h-96').style('min-width: 0; width: 100%;')

        # Initialize: tail existing file
        logs_dir = Path('logs')
        ensure_logs_static_mapping(logs_dir)
        current_log_file = logs_dir / 'cvd_tracker.log'
        initial_lines = _read_tail(current_log_file, max_lines=200)
        all_entries: deque[tuple[int, str]] = deque(maxlen=5000)
        for line in initial_lines:
            all_entries.append((_extract_level_from_line(line), line))

        # Queue and handler per client
        msg_queue: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=5000)

        # Set up a formatter consistent with file log lines
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%d.%m.%Y %H:%M:%S'
        )
        ui_handler = UILogHandler(msg_queue, level=logging.DEBUG, fmt=formatter)

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
        def current_level() -> int:
            return LOG_LEVELS.get(str(level_select.value), logging.INFO)

        def scroll_to_latest() -> None:
            if not bool(getattr(auto_scroll_switch, 'value', True)):
                return
            ui.run_javascript(f'''
            (() => {{
                const log = getElement({live_log.id});
                if (!log || !log.$refs || !log.$refs.qRef) return;
                log.shouldScroll = true;
                log.$nextTick(() => {{
                    try {{
                        log.$refs.qRef.setScrollPosition('vertical', Number.MAX_SAFE_INTEGER, 0);
                    }} catch (e) {{ /* ignore */ }}
                }});
            }})();
            ''')

        def render_log_view() -> None:
            live_log.clear()
            filtered = [msg for level, msg in all_entries if level >= current_level()]
            for msg in filtered[-2000:]:
                live_log.push(msg)
            scroll_to_latest()

        def on_clear() -> None:
            all_entries.clear()
            live_log.clear()
            while not msg_queue.empty():
                try:
                    msg_queue.get_nowait()
                except Exception:
                    break

        def on_level_change(e: Any) -> None:
            render_log_view()

        clear_btn.on('click', on_clear)
        level_select.on('update:model-value', on_level_change)
        auto_scroll_switch.on('update:model-value', lambda e: scroll_to_latest() if bool(getattr(e, 'value', False)) else None)
        render_log_view()

        # Drain queue into the UI in the client context
        def drain_queue() -> None:
            if paused_switch.value:
                return
            drained = 0
            threshold = current_level()
            # Limit per tick to avoid UI flooding
            while drained < 200 and not msg_queue.empty():
                try:
                    level, msg = msg_queue.get_nowait()
                except Exception:
                    break
                all_entries.append((level, msg))
                if level >= threshold:
                    live_log.push(msg)
                drained += 1
            if drained:
                scroll_to_latest()

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

        register_client_disconnect_handler(client, _cleanup_on_disconnect, logger=logger)
