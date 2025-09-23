from pathlib import Path
from nicegui import ui, app
import signal
import asyncio
import threading
import sys
from typing import Optional

# Entferne den frühen Side-Effect-Import der settings_page (verursacht Import-Rennen)
# try:
#     from . import settings_page as _settings_page  # noqa: F401
# except Exception:
#     _settings_page = None

from src.gui.default_elements import (
    create_camfeed_content,
    create_measurement_card,
    create_motion_status_element,
)

# Register settings page route via side-effect import (if available)
try:
    from . import settings_page as _settings_page  # noqa: F401
except Exception:
    _settings_page = None

from src.cam.camera import Camera
from src.measurement import create_measurement_controller_from_config, MeasurementController
from src.notify import create_email_system_from_config, EMailSystem
from src.config import load_config, set_global_config, get_global_config, save_global_config, AppConfig, get_logger
from src.update import check_update, perform_update, get_local_commit_short, restart_self
from .help.help import help_page  # noqa: F401  # register route via import side effect

# Remove early side-effect import (we'll import at the end)
# try:
#     from . import settings_page as _settings_page  # noqa: F401
# except Exception:
#     _settings_page = None

# Globales Kamerahandle, wird erst in ``main`` erzeugt
global_camera: Camera | None = None
global_measurement_controller: MeasurementController | None = None
global_email_system: EMailSystem | None = None
global_config: AppConfig | None = None

# Shutdown-Flag für thread-sichere Cleanup-Koordination
_shutdown_requested = threading.Event()
_cleanup_completed = threading.Event()

logger = get_logger("gui")

def _compute_title() -> str:
    """Compute UI title from config.gui.title template and metadata.

    Fallbacks to a generic title if config or fields are missing.
    """
    try:
        cfg = get_global_config() or global_config
        if cfg and getattr(cfg, 'gui', None):
            tpl = getattr(cfg.gui, 'title', '') or 'CVD-TRACKER'
            meta = getattr(cfg, 'metadata', None)
            params = {
                'cvd_id': getattr(meta, 'cvd_id', ''),
                'cvd_name': getattr(meta, 'cvd_name', ''),
            }
            try:
                return str(tpl).format(**params)
            except Exception:
                return str(tpl)
    except Exception:
        pass
    return 'CVD-TRACKER'

def build_header() -> None:
    with ui.header().classes('items-center justify-between shadow px-4 py-2 bg-[#1C3144] text-white'):
        # Per-client dark mode binding with immediate initialization from client storage
        dark = ui.dark_mode()
        try:
            _stored_dark = app.storage.client.get('cvd.dark_mode')
            if _stored_dark is not None:
                dark.value = bool(_stored_dark)
        except Exception:
            pass
        # Ensure per-client title value is initialized for bindings below
        try:
            if not app.storage.client.get('cvd.gui_title'):
                app.storage.client['cvd.gui_title'] = _compute_title()
        except Exception:
            pass
        # --- Linke Seite -------------------------------------------
        with ui.row().classes('items-center gap-3'):
            shutdown_dialog = ui.dialog().classes('items-center justify-center')
            with shutdown_dialog:
                with ui.card().classes('items-center justify-center'):
                    ui.label('Shutdown the server?').classes('text-h6')
                    
                    async def do_shutdown() -> None:
                        ui.navigate.to('/shutdown', new_tab=False)
                        await asyncio.sleep(2)
                        app.shutdown()

                    with ui.row().classes('gap-2 items-center justify-center'):
                        ui.button('Yes', on_click=do_shutdown).props('color=negative').tooltip('Shutdown the server and close the application')
                        ui.button('No', on_click=shutdown_dialog.close).props('color=positive').tooltip('Cancel shutdown')

            def show_shutdown_dialog() -> None:
                shutdown_dialog.open()

            ui.button(icon='img:/pics/logo_ipc_short.svg', on_click=show_shutdown_dialog).props('flat').style('max-height:72px; width:auto').tooltip('Shutdown the server and close the application')

            title_label = ui.label().props('id=cvd-header-title').classes(
                'text-xl font-semibold tracking-wider text-gray-100')
            try:
                title_label.bind_text_from(app.storage.client, 'cvd.gui_title')
            except Exception:
                title_label.text = _compute_title()

        # --- Rechte Seite ------------------------------------------
        def toggle_dark():
                dark.toggle()
                new_icon = 'light_mode' if dark.value else 'dark_mode'
                btn.props(f'icon={new_icon}')
                try:
                    app.storage.client['cvd.dark_mode'] = bool(dark.value)
                except Exception:
                    pass

        with ui.row().classes('items-center gap-4'):
            ui.button(icon='help', on_click=lambda: ui.navigate.to('/help'))\
                .props('flat round dense').classes('text-xl').tooltip('Help')
            btn= (ui.button(
                icon='light_mode' if dark.value else 'dark_mode',
                on_click=toggle_dark,
            ).props('flat round dense').classes('text-xl')).tooltip('Toggle dark mode')

            def _go_home():
                try:
                    app.storage.client['cvd.last_route'] = '/'
                except Exception:
                    pass
                ui.navigate.to('/', new_tab=False)

            def _go_settings():
                try:
                    app.storage.client['cvd.last_route'] = '/settings'
                except Exception:
                    pass
                ui.navigate.to('/settings', new_tab=False)

            ui.button(icon='home', on_click=_go_home)\
              .props('flat round dense id=cvd-header-home').classes('text-xl').tooltip('Home')

            ui.button(icon='settings', on_click=_go_settings)\
              .props('flat round dense id=cvd-header-settings').classes('text-xl').tooltip('Open settings')

def build_footer() -> None:
    with ui.footer(fixed=False).classes('items-center justify-between shadow px-4 py-2 bg-[#1C3144] text-white'):
        with ui.row().classes('items-center justify-between px-4 py-2'):
            footer_label = ui.label().props('id=cvd-footer-title').classes('text-white text-sm')
            try:
                footer_label.bind_text_from(app.storage.client, 'cvd.gui_title')
            except Exception:
                footer_label.text = _compute_title()
            ui.label('© 2025 TUHH KVWEB').classes('text-white text-sm')

def _init_camera(config: AppConfig) -> Camera | None:
    """Create and start the Camera instance from config."""
    try:
        cam_obj = Camera(config, logger=get_logger('camera'))
        cam_obj.initialize_routes()
        cam_obj.start_frame_capture()
        logger.info("Camera initialized successfully")
        return cam_obj
    except Exception as e:
        logger.error(f"Camera init failed: {e}")
        return None

async def cleanup_camera_async():
    """Asynchrone Kamera-Cleanup-Routine."""
    global global_camera
    try:
        if global_camera:
            await global_camera.cleanup()
            global_camera = None
            logger.info("Camera cleanup completed")
    except Exception as e:
        logger.error(f"Error during camera cleanup: {e}")

def cleanup_application_sync():
    """Thread-sichere synchrone Cleanup-Funktion."""
    global global_measurement_controller, global_email_system
    
    logger.info("Starting synchronous application cleanup...")
    
    try:
        # Synchrone Komponenten sofort bereinigen
        if global_measurement_controller:
            global_measurement_controller.cleanup()
            global_measurement_controller = None
            logger.info("Measurement controller cleanup completed")

        if global_email_system:
            global_email_system.cleanup()
            global_email_system = None
            logger.info("Email system cleanup completed")

        logger.info("Synchronous cleanup completed")
        
    except Exception as e:
        logger.error(f"Error during synchronous cleanup: {e}")

def schedule_async_cleanup():
    """Plant asynchrone Cleanup-Routine thread-sicher ein."""
    try:
        # Versuche den aktuellen Event Loop zu finden
        loop = asyncio.get_running_loop()
        
        # Erstelle eine threadsafe Callback-Funktion
        def cleanup_and_signal():
            async def full_cleanup():
                try:
                    await cleanup_camera_async()
                finally:
                    _cleanup_completed.set()
            
            # Erstelle Task für asynchrone Cleanup
            loop.create_task(full_cleanup())
        
        # Plane die Cleanup-Funktion thread-sicher ein
        loop.call_soon_threadsafe(cleanup_and_signal)
        
    except RuntimeError:
        # Kein aktiver Event Loop - Fallback auf synchrone Cleanup
        logger.warning("No running event loop found, skipping async camera cleanup")
        _cleanup_completed.set()

def cleanup_application():
    """Haupt-Cleanup-Funktion mit thread-sicherer Koordination."""
    if _shutdown_requested.is_set():
        return  # Cleanup bereits initiiert
    
    _shutdown_requested.set()
    logger.info("Starting application cleanup...")
    
    try:
        # 1. Synchrone Komponenten sofort bereinigen
        cleanup_application_sync()
        
        # 2. Asynchrone Kamera-Cleanup thread-sicher einplanen
        schedule_async_cleanup()
        
        # 3. Kurz warten auf asynchrone Cleanup (mit Timeout)
        if _cleanup_completed.wait(timeout=2.0):
            logger.info("Application cleanup completed successfully")
        else:
            logger.warning("Async cleanup timeout - proceeding with shutdown")
            
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    finally:
        _cleanup_completed.set()

def signal_handler(signum, frame):
    """Thread-sicherer Signal-Handler für sauberes Shutdown."""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    
    # Cleanup in separatem Thread ausführen um Signal-Handler nicht zu blockieren
    cleanup_thread = threading.Thread(target=cleanup_application, daemon=True)
    cleanup_thread.start()
    
    # Kurz warten auf Cleanup-Completion
    if _cleanup_completed.wait(timeout=3.0):
        logger.info("Cleanup completed, exiting...")
    else:
        logger.warning("Cleanup timeout, forcing exit...")
    
    try:
        app.shutdown()  # NiceGUI sauber beenden
    except:
        sys.exit(0)

# Signal-Handler registrieren
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def create_gui(config_path: str = "config/config.yaml") -> None:
    """Initialisierung vor ui.run(): Konfiguration laden und global setzen.

    Die eigentlichen Seiten sind per @ui.page deklariert.
    """
    global global_config
    try:
        cfg = load_config(config_path)
        global_config = cfg
        set_global_config(cfg, config_path)
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
        logger.error(f"Failed to load config: {e}")
        # Nicht crashen, ui.run darf weiterlaufen; Seiten laden Config lazy nach

@ui.page('/shutdown')
def shutdown_page() -> None:
    with ui.column().classes('absolute-center items-center gap-6'):
        ui.icon('power_settings_new').classes('text-6xl text-negative')
        ui.label('Server shutdown').classes('text-h4 font-medium')
        ui.label('You can close this window now.')

@ui.page('/updating')
def updating_page() -> None:
    with ui.column().classes('absolute-center items-center gap-4'):
        ui.icon('system_update').classes('text-6xl text-primary')
        ui.label('Update wird installiert...').classes('text-h5 font-medium')
        ui.button('Zurück', on_click=lambda: ui.navigate.to('/')).props('flat').classes('q-mt-md')

@ui.page('/')
def main_page() -> None:
    """Hauptseite der Anwendung."""
    global global_camera, global_measurement_controller, global_email_system, global_config

    # Konfiguration ggf. lazy laden (falls create_gui zuvor fehlschlug)
    if global_config is None:
        try:
            global_config = load_config()
        except Exception as e:
            logger.error(f"Config load in main_page failed: {e}")

    # Kamera initialisieren
    if global_camera is None and global_config is not None:
        global_camera = _init_camera(global_config)

    logger.info(f"Starting CVD-TRACKER {get_local_commit_short()}")

    # Email-System initialisieren
    if global_email_system is None and global_config is not None:
        try:
            global_email_system = create_email_system_from_config(global_config, logger=logger)
            logger.info("Email system initialized")
        except Exception as e:
            logger.error(f"Error initializing email system: {e}")
            global_email_system = None

    # Measurement-Controller initialisieren
    if global_measurement_controller is None and global_config is not None:
        try:
            global_measurement_controller = create_measurement_controller_from_config(
                config=global_config,
                email_system=global_email_system,
                camera=global_camera,
                logger=logger,
            )
            logger.info("Measurement controller initialized")
        except Exception as e:
            logger.error(f"Error initializing measurement controller: {e}")
            global_measurement_controller = None

    # Instanzen für settings_page bereitstellen
    try:
        from .instances import set_instances
        set_instances(global_camera, global_measurement_controller, global_email_system)
    except Exception:
        pass

    # Header
    build_header()

    with ui.grid(columns="2fr 1fr").classes("w-full gap-4 p-4 items-stretch"):
        with ui.column().classes("gap-4"):
            create_camfeed_content()
        with ui.column().classes("gap-4"):
            create_motion_status_element(global_camera, global_measurement_controller)
            create_measurement_card(global_measurement_controller, global_camera, email_system=global_email_system)

    # Footer
    build_footer()
