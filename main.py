import sys
import logging
from pathlib import Path
import argparse

# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from nicegui import ui, app
#10from nicegui_toolkit import inject_layout_tool
from src.config import load_config
from src.gui.gui_ import create_gui

#inject_layout_tool()

def parse_args() -> argparse.Namespace:
    """Kommandozeilenargumente parsen."""
    parser = argparse.ArgumentParser(description="CVD-Tracker")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the configuration file",
    )
    return parser.parse_args()

def main() -> int:

    """Haupteinstiegspunkt der Anwendung"""
    args = parse_args()
    try:
        # Konfiguration laden und Logger einrichten
        cfg = load_config(args.config)
        logger = cfg.logging.setup_logger("cvd_tracker.main")

        logger.info("Starting CVD-Tracker application...")

        @app.exception_handler(RuntimeError)
        async def handle_runtime_error(request, exception):
            """Spezifischer Fehler-Handler für RuntimeError."""
            if "deque mutated during iteration" in str(exception):
                logger.warning("Deque mutation detected - ignoring to prevent crash")
                return
            logger.error(f"Runtime error: {exception}")
            raise exception

        @app.exception_handler(Exception)
        async def handle_exception(request, exception):
            """Globaler Fehler-Handler für NiceGUI."""
            logger.error(f"Unhandled exception: {exception}")
            return {'error': 'Internal server error'}

        create_gui(config_path=args.config)
        
        # NiceGUI starten
        ui.run(
            host='0.0.0.0',
            port=8080,
            title='CVD-TRACKER',
            favicon='https://www.tuhh.de/favicon.ico',
            reload=False,
            reconnect_timeout=80.0,
        )

        logger.info("Application started")

    except ImportError as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Import error: {e}")
        logger.error("Install required dependencies: pip install -r requirements.txt")
        return 1
        
    except Exception as e:
        logger = logging.getLogger("cvd_tracker.main")
        logger.error(f"Error occurred at startup: {e}", exc_info=True)
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
