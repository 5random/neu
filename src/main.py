import sys
import logging
from pathlib import Path
import argparse
from nicegui import ui

# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def parse_args() -> argparse.Namespace:
    """Kommandozeilenargumente parsen."""
    parser = argparse.ArgumentParser(description="CVD-Tracker")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Pfad zur Konfigurationsdatei",
    )
    return parser.parse_args()

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

def main() -> int:
    """Haupteinstiegspunkt der Anwendung"""
    args = parse_args()
    try:
        # Logging zuerst konfigurieren
        setup_logging()
        logger = logging.getLogger(__name__)
        
        logger.info("Starte CVD-Tracker Anwendung...")
        
        from src.config import load_config
        cfg = load_config(args.config)

        # GUI erstellen
        from gui.gui import create_gui
        create_gui(config_path=args.config)
        
        # NiceGUI starten
        ui.run(
            host=cfg.gui.host,
            port=cfg.gui.port,
            title='CVD-Tracker',
            favicon='https://www.tuhh.de/favicon.ico',
            reload=False
        )
        
        logger.info("Anwendung beendet")
        
    except ImportError as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Import-Fehler: {e}")
        logger.error("Installiere Abhängigkeiten: pip install -r requirements.txt")
        return 1
        
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Fehler beim Start: {e}", exc_info=True)
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())