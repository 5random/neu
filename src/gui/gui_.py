from typing import Any

_settings_page: Any = None
try:
    from src.gui.settings_page import settings_page as _settings_page  # noqa: F401
except Exception:
    pass

from nicegui import ui, app
import sys

from src.config import load_config, set_global_config, get_logger
from src.gui import init, cleanup

# Register help and default page routes via import side effect
from .help.help import help_page  # noqa: F401
from src.gui.default_page import index_page as default_page  # noqa: F401

logger = get_logger("gui")

from .layout import build_header, build_footer, install_overlay_styles

# Register signal handlers for graceful shutdown
cleanup.register_signal_handlers()

def create_gui(config_path: str = "config/config.yaml") -> None:
    """Initialisierung vor ui.run(): Konfiguration laden und App initialisieren.

    Die eigentlichen Seiten sind per @ui.page deklariert.
    """
    try:
        # Centralized initialization
        init.init_application(config_path)
        
        # Optional: statische Pfade einmalig mounten
        try:
            app.add_static_files('/pics', 'pics')
        except Exception:
            pass
        try:
            app.add_static_files('/logs', 'logs')
        except Exception:
            pass
            
        logger.info('GUI initialized; config loaded')
    except Exception as e:
        logger.error(f"Failed to initialize GUI: {e}")
        # Nicht crashen, ui.run darf weiterlaufen

@ui.page('/shutdown')
def shutdown_page() -> None:
    install_overlay_styles()
    with ui.column().classes('absolute-center items-center gap-6'):
        ui.icon('power_settings_new').classes('text-6xl text-negative')
        ui.label('Server shutdown').classes('text-h4 font-medium')
        ui.label('You can close this window now.')

@ui.page('/updating')
def updating_page() -> None:
    from src.update import check_update, perform_update, restart_self
    import queue
    import asyncio
    
    logger.info('Opening updating page...')
    install_overlay_styles()
    with ui.column().classes('absolute-center items-center gap-4'):
        ui.icon('system_update').classes('text-6xl text-primary')
        ui.label('Update wird installiert...').classes('text-h5 font-medium')
        status = ui.label('').classes('text-body2')
        log = ui.log(max_lines=500).classes('w-[800px] h-[360px] bg-black text-green-400 rounded')

        # Thread-safe progress queue for background thread messages
        q: queue.Queue[str] = queue.Queue()

        def drain_progress() -> None:
            try:
                while True:
                    msg = q.get_nowait()
                    log.push(msg)
                    logger.info(msg)
            except queue.Empty:
                pass

        async def run_update() -> None:
            try:
                # 1) Status prüfen
                logger.info('Checking update status...')
                stat = await asyncio.to_thread(check_update)
                status.text = f"Lokaler Commit {stat.get('local')} → Remote {stat.get('remote') or ''} (behind={stat.get('behind', 0)})"
                logger.info(f"Update status: behind={stat.get('behind', 0)}, local={stat.get('local')}, remote={stat.get('remote')}")

                # 2) Update im Hintergrund durchführen
                logger.info('Starting update...')
                ok = await asyncio.to_thread(perform_update, q.put)

                if ok:
                    logger.info('Update completed successfully; restarting...')
                    ui.notify('Update abgeschlossen. Neustart...', type='positive', position='bottom-right')
                    # 3) Sauberes Cleanup + Self-Restart (cleanup on event loop thread)
                    cleanup.cleanup_application()
                    await asyncio.sleep(0.3)
                    await asyncio.to_thread(restart_self)
                else:
                    logger.warning('Update failed or not available.')
                    ui.notify('Update fehlgeschlagen oder nicht verfügbar.', type='warning', position='bottom-right')
                    ui.button('Zurück', on_click=lambda: ui.navigate.to('/')).props('flat').classes('q-mt-md')
            except Exception as e:
                logger.exception('Update process failed')
                ui.notify(f'Update failed: {e}', type='negative', position='bottom-right')
                ui.button('Zurück', on_click=lambda: ui.navigate.to('/')).props('flat').classes('q-mt-md')

        # Drain progress queue on UI thread
        ui.timer(0.1, drain_progress)
        ui.timer(0.05, run_update, once=True)

