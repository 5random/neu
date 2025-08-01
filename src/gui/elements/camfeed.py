from nicegui import ui
import time
import sys
from pathlib import Path

# Projekt-Root zum Python-Pfad hinzufügen
#project_root = Path(__file__).parents[4]  # 4 Ebenen nach oben
#sys.path.insert(0, str(project_root))

from src.cam.camera import Camera
from src.config import get_logger

logger = get_logger('gui.camfeed')

def create_camfeed_content():
    # Kamera initialisieren
    logger.info("Creating camera feed")
    with ui.card().style("align-self:stretch; justify-content:center; align-items:start;"):
        ui.label('Camera Feed').classes('text-h6 font-semibold mb-2')
        videoimage = ui.interactive_image().classes('w-full h-full rounded-lg')
        
        # Live-Video-Feed aktualisieren
        ui.timer(interval=0.2, callback=lambda: videoimage.set_source(f'/video/frame?{time.time()}'))