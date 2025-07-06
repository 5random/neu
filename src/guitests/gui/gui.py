from nicegui import ui, app
from elements import (
    create_camfeed_content,
    create_emailcard,
    create_measurement_card,
    create_motion_status_element,
    create_uvc_content,
    create_motiondetection_card,
)
import sys
from pathlib import Path

# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parents[3]
sys.path.insert(0, str(project_root))

from src.cam.camera import Camera

# Kamera SOFORT beim Import initialisieren (nicht in einer Funktion!)
print("Initialisiere Kamera beim Modul-Import...")
try:
    global_camera = Camera()
    global_camera._setup_routes()
    global_camera.start_frame_capture()
    print("Kamera erfolgreich initialisiert")
except Exception as e:
    print(f"FEHLER: {e}")
    global_camera = None

def main() -> None:
    # Keine Kamera-Initialisierung hier - bereits beim Import erledigt!
    
    with ui.grid(columns='2fr 1fr').classes('w-full gap-4 p-4'):
        with ui.column().classes('gap-4'):
            create_camfeed_content()
            create_motion_status_element()
            create_measurement_card()

        with ui.column().classes('gap-4'):
            create_uvc_content(camera=global_camera)
            create_motiondetection_card()
            create_emailcard()

if __name__ in {'__main__', '__mp_main__'}:
    try:
        main()
        ui.run(title='CVD-Tracker', reload=False)
    finally:
        # Cleanup
        if global_camera:
            global_camera.stop_frame_capture()