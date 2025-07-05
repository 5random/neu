from nicegui import ui
import time
import sys
from pathlib import Path

# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parents[4]  # 4 Ebenen nach oben
sys.path.insert(0, str(project_root))

from src.cam.camera import Camera


def create_camfeed_content():
    # Kamera initialisieren
    
    with ui.card().style("align-self:stretch; justify-content:center; align-items:start;"):
        ui.label('Camera Feed').classes('text-h6 font-semibold mb-2')
        videoimage = ui.interactive_image().classes('w-auto h-full rounded-lg shadow-md')
        
        # Live-Video-Feed aktivieren (auskommentierte Zeile aktivieren)
        ui.timer(interval=0.2, callback=lambda: videoimage.set_source(f'/video/frame?{time.time()}'))