import sys
import logging
from pathlib import Path
from nicegui import ui

from src.config import load_config

# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def main():
    """Haupteinstiegspunkt der Anwendung"""
    try:
        # Konfiguration laden und Logger einrichten
        cfg = load_config()
        logger = cfg.logging.setup_logger("cvd_tracker.main")

        logger.info("Starte CVD-Tracker Anwendung...")

        # GUI erstellen
        from gui.gui import create_gui
        create_gui()
        
        # NiceGUI starten
        ui.run(
            host=cfg.gui.host,
            port=cfg.gui.port,
            title=cfg.gui.title,
            favicon='https://www.tuhh.de/favicon.ico',
            reload=False
        )
        
        logger.info("Anwendung beendet")
        
    except ImportError as e:
        print(f"Import-Fehler: {e}")
        print("Installiere Abhängigkeiten: pip install -r requirements.txt")
        return 1
        
    except Exception as e:
        logger = logging.getLogger("cvd_tracker.main")
        logger.error(f"Fehler beim Start: {e}", exc_info=True)
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())