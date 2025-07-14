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

from src.cam.camera import Camera

# Globales Kamerahandle, wird erst in ``main`` erzeugt
global_camera: Camera | None = None
global_measurement_controller: MeasurementController | None = None


def init_camera() -> Camera | None:
    """Initialisiere Kamera und starte die Bilderfassung."""

    print("Initialisiere Kamera ...")
    try:
        cam = Camera()
        cam.initialize_routes()
        cam.start_frame_capture()
        print("Kamera erfolgreich initialisiert")
        return cam
    except Exception as e:
        print(f"FEHLER: {e}")
        return None


def main() -> None:
    """Starte die GUI und initialisiere bei Bedarf die Kamera."""

    global global_camera, global_measurement_controller
    if global_camera is None:
        global_camera = init_camera()
    if global_measurement_controller is None:
        try:
            global_measurement_controller = create_measurement_controller_from_config(
                camera=global_camera
            )
        except Exception as exc:
            logger.error(f"MeasurementController-Init fehlgeschlagen: {exc}")
            global_measurement_controller = None

    with ui.grid(columns="2fr 1fr").classes("w-full gap-4 p-4"):
        with ui.column().classes("gap-4"):
            create_camfeed_content()
            create_motion_status_element(global_camera, global_measurement_controller)
            create_measurement_card(global_measurement_controller)

        with ui.column().classes("gap-4"):
            create_uvc_content(camera=global_camera)
            create_motiondetection_card(camera=global_camera)
            create_emailcard()

if __name__ in {'__main__', '__mp_main__'}:
    try:
        main()
        ui.run(title='CVD-Tracker', reload=False)
    finally:
        # Cleanup
        if global_camera:
            global_camera.stop_frame_capture()
