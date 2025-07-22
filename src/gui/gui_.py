from pathlib import Path
from nicegui import ui, app
import signal
import asyncio

from src import cam
from src.gui.elements import (
    create_camfeed_content,
    create_emailcard,
    create_measurement_card,
    create_motion_status_element,
    create_uvc_content,
    create_motiondetection_card,
)

from src.measurement import create_measurement_controller_from_config, MeasurementController
from src.alert import create_alert_system_from_config, AlertSystem
from src.config import logger, load_config

from src.cam.camera import Camera

# Globales Kamerahandle, wird erst in ``main`` erzeugt
global_camera: Camera | None = None
global_measurement_controller: MeasurementController | None = None
global_alert_system: AlertSystem | None = None

dark = ui.dark_mode(value=False)


def init_camera(config_path: str = "config/config.yaml") -> Camera | None:
    """Initialisiere Kamera und starte die Bilderfassung.

    Args:
        config_path: Pfad zur zu ladenden Konfiguration
    """

    logger.info("Initializing camera ...")

    try:
        config = load_config(config_path)
    except Exception as e:
        logger.error(f"Configuration loading failed: {e}")
        ui.notify("Configuration could not be loaded.", type='negative', position='bottom-right')
        return None
    
    try:
        cam = Camera(config_path)
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

    global global_camera, global_measurement_controller, global_alert_system
    try:
        config = load_config(config_path)
        if config:
            logger.info('Configuration loaded successfully')
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        ui.notify("Failed to load configuration.", type='negative')
        return
    
    if global_camera is None:
        logger.info('Initializing Camera...')
        global_camera = init_camera(config_path)
        if global_camera is None:
            logger.error('Failed to initialize camera')
            ui.notify('Camera initialization failed, starting GUI without camera', type= 'warning', position='bottom-right')

    if global_alert_system is None:
        try:
            logger.info('Initializing alert system...')
            global_alert_system = create_alert_system_from_config(config_path)
            logger.info('Alert system initialized successfully')
        except Exception as exc:
            logger.error(f"AlertSystem-Init failed: {exc}")
            ui.notify('Alert system initialization failed', type='warning', position='bottom-right')
            global_alert_system = None

    if global_measurement_controller is None:
        try:
            global_measurement_controller = create_measurement_controller_from_config(
                config_path=config_path,
                alert_system=global_alert_system,
                camera=global_camera,
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
    with ui.header().classes('items-center justify-between shadow px-4 py-2 bg-[#1C3144] text-white'):
        # --- Linke Seite -------------------------------------------
        with ui.row().classes('items-center gap-3'):
            # Favicon per URL
            ui.image('https://www.tuhh.de/favicon.ico').classes('w-8 h-8')
            ui.label('CVD-TRACKER').classes(
                'text-xl font-semibold tracking-wider text-gray-100')

        # --- Rechte Seite ------------------------------------------
        def toggle_dark():
                dark.toggle()
                new_icon = 'light_mode' if dark.value else 'dark_mode'
                btn.props(f'icon={new_icon}')

        with ui.row().classes('items-center gap-4'):
            btn= (ui.button(
                icon='light_mode' if dark.value else 'dark_mode',
                on_click=toggle_dark,
            ).props('flat round dense').classes('text-xl'))
            #ui.button( icon='download', on_click=lambda: ui.download.from_url('/logs/cvd_tracker.log')).props('flat round dense').classes('text-xl')


    with ui.grid(columns="2fr 1fr").classes("w-full gap-4 p-4"):
        with ui.column().classes("gap-4"):
            create_camfeed_content()
            with ui.grid(columns="1fr 2fr").classes("gap-4 w-full").style("grid-template-columns: repeat(auto-fit,minmax(260px,1fr)); align-items: stretch;"):
                with ui.column().classes("h-full"):
                    create_motion_status_element(global_camera, global_measurement_controller)
                with ui.column().classes("h-full"):
                    create_measurement_card(global_measurement_controller, config=config)

        with ui.column().classes("gap-4"):
            create_uvc_content(camera=global_camera)
            create_motiondetection_card(camera=global_camera)
            create_emailcard(config=config, alert_system=global_alert_system)

def cleanup_application():
    """Cleanup-Funktion für sauberes Shutdown."""
    global global_camera, global_measurement_controller, global_alert_system
    
    logger.info("Starting application cleanup...")
    
    try:
        if global_camera:
            # Async cleanup für Camera
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(global_camera.cleanup())
            else:
                asyncio.run(global_camera.cleanup())
            global_camera = None
            
        if global_measurement_controller:
            global_measurement_controller.cleanup()
            global_measurement_controller = None
            
        if global_alert_system:
            global_alert_system.cleanup()
            global_alert_system = None
            
        logger.info("Application cleanup completed")
        
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

def signal_handler(signum, frame):
    """Signal-Handler für sauberes Shutdown."""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    cleanup_application()
    exit(0)

# Signal-Handler registrieren
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler) 
    