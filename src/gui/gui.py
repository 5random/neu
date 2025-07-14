from nicegui import ui, app
import sys
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parents[2]
sys.path.insert(0, str(project_root))

from elements import (
    create_camfeed_content,
    create_emailcard,
    create_measurement_card,
    create_motion_status_element,
    create_uvc_content,
    create_motiondetection_card,
)

from src.measurement import create_measurement_controller_from_config, MeasurementController
from src.alert import create_alert_system_from_config, AlertSystem

from src.cam.camera import Camera

# Globales Kamerahandle, wird erst in ``main`` erzeugt
global_camera: Camera | None = None
global_measurement_controller: MeasurementController | None = None
global_alert_system: AlertSystem | None = None


def init_camera(config_path: str = "config/config.yaml") -> Camera | None:
    """Initialisiere Kamera und starte die Bilderfassung.

    Args:
        config_path: Pfad zur zu ladenden Konfiguration
    """

    print("Initialisiere Kamera ...")
    try:
        cam = Camera(config_path)
        cam.initialize_routes()
        cam.start_frame_capture()
        print("Kamera erfolgreich initialisiert")
        return cam
    except Exception as e:
        print(f"FEHLER: {e}")
        return None


def create_gui(config_path: str = "config/config.yaml") -> None:
    """Starte die GUI und initialisiere bei Bedarf die Kamera.

    Args:
        config_path: Pfad zur zu ladenden Konfigurationsdatei
    """

    global global_camera, global_measurement_controller, global_alert_system
    global_camera = init_camera(config_path)
    if not global_camera:
        ui.notify("Kamera konnte nicht initialisiert werden.", type='negative')
        return 
    if global_alert_system is None:
        try:
            global_alert_system = create_alert_system_from_config(config_path)
        except Exception as exc:
            logger.error(f"AlertSystem-Init fehlgeschlagen: {exc}")
            global_alert_system = None
    if global_measurement_controller is None:
        try:
            global_measurement_controller = create_measurement_controller_from_config(
                config_path=config_path,
                alert_system=global_alert_system,
                camera=global_camera,
            )
        except Exception as exc:
            logger.error(f"MeasurementController-Init fehlgeschlagen: {exc}")
            global_measurement_controller = None

    if global_measurement_controller:
        controller = global_measurement_controller
        global_camera.enable_motion_detection(
            lambda frame, motion_result: controller.on_motion_detected(motion_result)
        )

    with ui.grid(columns="2fr 1fr").classes("w-full gap-4 p-4"):
        with ui.column().classes("gap-4"):
            create_camfeed_content()
            create_motion_status_element(global_camera, global_measurement_controller)
            create_measurement_card(global_measurement_controller)

        with ui.column().classes("gap-4"):
            create_uvc_content(camera=global_camera)
            create_motiondetection_card(camera=global_camera)
            create_emailcard()

