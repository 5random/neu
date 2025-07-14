import sys
import logging
from pathlib import Path
from nicegui import ui

# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def setup_logging():
    """Konfiguriert das Logging-System"""
    # Logs-Verzeichnis erstellen falls nicht vorhanden
    logs_dir = Path('logs')
    logs_dir.mkdir(exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(logs_dir / 'app.log', encoding='utf-8')
        ]
    )

def main():
    """Haupteinstiegspunkt der Anwendung"""
    try:
        # Logging zuerst konfigurieren
        setup_logging()
        logger = logging.getLogger(__name__)
        
        logger.info("Starte CVD-Tracker Anwendung...")
        
        # GUI erstellen
        from gui.gui import create_gui
        create_gui()
        
        # NiceGUI starten
        ui.run(
            host='0.0.0.0', 
            port=8080, 
            title='CVD-Tracker',
            favicon='https://www.tuhh.de/favicon.ico',
            reload=False
        )
        
        logger.info("Anwendung beendet")
        
    except ImportError as e:
        print(f"Import-Fehler: {e}")
        print("Installiere Abhängigkeiten: pip install -r requirements.txt")
        return 1
        
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Fehler beim Start: {e}", exc_info=True)
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())