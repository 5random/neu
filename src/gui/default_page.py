from pathlib import Path
from nicegui import ui, app
import signal
import asyncio
import threading
import queue
from typing import Optional
import sys

from src import cam
from src.gui.default_elements import (
    create_camfeed_content,
    create_emailcard,
    create_measurement_card,
    create_motion_status_element,
    create_uvc_content,
    create_motiondetection_card,
)

from src.cam.camera import Camera
from src.measurement import create_measurement_controller_from_config, MeasurementController
from src.notify import create_email_system_from_config, EMailSystem
from src.config import load_config, set_global_config, get_global_config, save_global_config, AppConfig, get_logger
from src.update import check_update, perform_update, get_local_commit_short, restart_self


# Globales Kamerahandle, wird erst in ``main`` erzeugt
global_camera: Camera | None = None
global_measurement_controller: MeasurementController | None = None
global_email_system: EMailSystem | None = None
global_config: AppConfig | None = None

# Shutdown-Flag für thread-sichere Cleanup-Koordination
_shutdown_requested = threading.Event()
_cleanup_completed = threading.Event()

logger = get_logger("default_page")

def _compute_title() -> str:
    """Compute UI title from config.gui.title template and metadata."""
    try:
        cfg = get_global_config()
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

def init_camera(config: AppConfig) -> Camera | None:
    """Initialisiere Kamera und starte die Bilderfassung.

    Args:
        config_path: Pfad zur zu ladenden Konfiguration
    """

    logger.info("Starting camera initialization")

    try:
        cam = Camera(config, logger=get_logger('camera'))
        cam.initialize_routes()
        cam.start_frame_capture()
        logger.info("Camera initialized successfully")
        return cam
    except Exception as e:
        logger.error(f"ERROR: {e}")
        return None


def create_gui(config_path: str = "config/config.yaml") -> None:
    """Starte die GUI und initialisiere bei Bedarf die Kamera.

    Args:
        config_path: Pfad zur zu ladenden Konfigurationsdatei
    """

    global global_camera, global_measurement_controller, global_email_system, global_config
    try:
        global_config = load_config(config_path)
        set_global_config(global_config, config_path)
        if global_config:
            logger.info('Configuration loaded successfully')
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        ui.notify("Failed to load configuration.", type='negative')
        return
    
    if global_camera is None:
        logger.info('Initializing Camera...')
        global_camera = init_camera(global_config)
        if global_camera is None:
            logger.error('Failed to initialize camera')
            ui.notify('Camera initialization failed, starting GUI without camera', close_button=True, type='warning', position='bottom-right')

    if global_email_system is None:
        try:
            logger.info('Initializing E-Mail-Notification system...')
            global_email_system = create_email_system_from_config(global_config, logger=logger)
            logger.info('E-Mail-Notification system initialized successfully')
        except Exception as exc:
            logger.error(f"E-Mail-Notification system initialization failed: {exc}")
            ui.notify('E-Mail-Notification system initialization failed', type='warning', position='bottom-right')
            global_email_system = None

    if global_measurement_controller is None:
        try:
            global_measurement_controller = create_measurement_controller_from_config(
                config=global_config,
                email_system=global_email_system,
                camera=global_camera,
                logger=logger,
            )
        except Exception as exc:
            logger.error(f"MeasurementController-Init failed: {exc}")
            global_measurement_controller = None

    if global_measurement_controller and global_camera:
        try:
            logger.info("Initializing measurement controller...")
            controller = global_measurement_controller
            global_camera.enable_motion_detection(
                lambda frame, motion_result: controller.on_motion_detected(motion_result)
            )
            logger.info("Measurement controller initialized successfully")            
        except Exception as e:
            logger.error(f'Failed to enable motion detection: {e}')
            ui.notify('Measurement controller initialization failed', type='warning', position='bottom-right')
            global_measurement_controller = None
    else:
        logger.warning("Motion detection not available - missing camera or measurement controller")

    logger.info('creating GUI')

    # Mount static files once
    try:
        app.add_static_files('/pics', 'pics')
    except Exception:
        pass
    try:
        app.add_static_files('/logs', 'logs')
    except Exception:
        pass

    # Persist last visited route per client
    try:
        app.storage.client['cvd.last_route'] = '/'
    except Exception:
        pass

    @ui.page('/shutdown')
    def shutdown_page() -> None:
        with ui.column().classes('absolute-center items-center gap-6'):
            ui.icon('power_settings_new').classes('text-6xl text-negative')
            ui.label('Server shutdown').classes('text-h4 font-medium')
            ui.label('You can close this window now.')
    
    @ui.page('/updating')
    def updating_page() -> None:
        logger.info('Opening updating page...')
        with ui.column().classes('absolute-center items-center gap-4'):
            ui.icon('system_update').classes('text-6xl text-primary')
            ui.label('Update wird installiert...').classes('text-h5 font-medium')
            status = ui.label('').classes('text-body2')
            log = ui.log(max_lines=500).classes('w-[800px] h-[360px] bg-black text-green-400 rounded')

            # Thread-safe progress queue for background thread messages
            q: queue.Queue[str] = queue.Queue()

            def drain_progress():
                try:
                    while True:
                        msg = q.get_nowait()
                        log.push(msg)
                        logger.info(msg)
                except queue.Empty:
                    pass

            async def run_update():
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
                        cleanup_application()
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

    # Use shared header
    try:
        from .gui_ import build_header, build_footer
    except Exception:
        build_header = None  # type: ignore
        build_footer = None  # type: ignore

    if build_header:
        build_header()

    # Neuaufbau-/Lazy-Mechanismus: schwere Bereiche verzögert erstellen
    with ui.grid(columns="2fr 1fr").classes("w-full gap-4 p-4"):
        # Linke Spalte (Camfeed + Status/Messung)
        with ui.column().classes("gap-4") as left_col:
            left_placeholder = ui.label('Loading camera view…').classes('text-grey')

        # Rechte Spalte (UVC/Motion/E-Mail)
        with ui.column().classes("gap-4") as right_col:
            right_placeholder = ui.label('Loading controls…').classes('text-grey')

        def _build_left():
            try:
                left_placeholder.visible = False
            except Exception:
                pass
            with left_col:
                # Camfeed
                create_camfeed_content()
                # Status + Messungen in responsivem Grid
                with ui.grid(columns="1fr 2fr").classes("gap-4 w-full").style("grid-template-columns: repeat(auto-fit,minmax(260px,1fr)); align-items: stretch;"):
                    with ui.column().classes("h-full"):
                        create_motion_status_element(global_camera, global_measurement_controller)
                    with ui.column().classes("h-full"):
                        create_measurement_card(global_measurement_controller, global_camera, email_system=global_email_system)

        def _build_right():
            try:
                right_placeholder.visible = False
            except Exception:
                pass
            with right_col:
                create_uvc_content(camera=global_camera)
                create_motiondetection_card(camera=global_camera)
                create_emailcard(email_system=global_email_system)

        # Nach initialem Paint verzögert aufbauen (verhindert Ruckeln, robuster bei bfcache)
        ui.timer(0.01, _build_left, once=True)
        ui.timer(0.05, _build_right, once=True)
    
    # Replace local footer with shared footer
    if build_footer:
        build_footer()
    else:
        # fallback to existing footer if import fails
        with ui.footer(fixed=False).classes('items-center justify-between shadow px-4 py-2 bg-[#1C3144] text-white'):
            with ui.row().classes('items-center justify-between px-4 py-2'):
                # Replace static footer label with binding as well
                footer_label = ui.label().props('id=cvd-footer-title').classes('text-white text-sm')
                try:
                    footer_label.bind_text_from(app.storage.client, 'cvd.gui_title')
                except Exception:
                    footer_label.text = _compute_title()
                ui.label('© 2025 TUHH KVWEB').classes('text-white text-sm')

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
