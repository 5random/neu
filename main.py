import sys
import logging
from pathlib import Path
import argparse

# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

#from nicegui_toolkit import inject_layout_tool
#inject_layout_tool()

from nicegui import ui, app
from fastapi import Request
from fastapi.responses import JSONResponse
from src.config import load_config
from src.gui.gui_ import create_gui


def parse_args() -> argparse.Namespace:
    """Kommandozeilenargumente parsen."""
    parser = argparse.ArgumentParser(description="CVD-Tracker")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the configuration file",
    )
    return parser.parse_args()

def setup_exception_handlers(logger: logging.Logger) -> None:
    """Configure consistent exception handlers for the application."""
    
    @app.exception_handler(RuntimeError)
    async def handle_runtime_error(request: Request, exc: RuntimeError) -> JSONResponse:
        """Handle RuntimeError with specific logic for deque mutations."""
        if "deque mutated during iteration" in str(exc):
            logger.warning("Deque mutation detected - handled gracefully to prevent crash")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_processing_error",
                    "message": "Temporary processing issue resolved",
                    "handled": True
                }
            )
        
        logger.error(f"Runtime error: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "runtime_error", 
                "message": "A runtime error occurred",
                "details": str(exc)
            }
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        """Handle configuration and validation errors."""
        logger.error(f"Value error: {exc}")
        return JSONResponse(
            status_code=400,
            content={
                "error": "configuration_error",
                "message": "Invalid configuration or input",
                "details": str(exc)
            }
        )

    @app.exception_handler(ConnectionError)
    async def handle_connection_error(request: Request, exc: ConnectionError) -> JSONResponse:
        """Handle network and device connection errors."""
        logger.error(f"Connection error: {exc}")
        return JSONResponse(
            status_code=503,
            content={
                "error": "connection_error",
                "message": "Device or network connection failed",
                "details": str(exc)
            }
        )

def main() -> int:

    """Haupteinstiegspunkt der Anwendung"""
    args = parse_args()
    try:
        # Konfiguration laden und Logger einrichten
        cfg = load_config(args.config)
        logger = cfg.logging.setup_logger("cvd_tracker.main")

        logger.info("Starting CVD-Tracker application...")

        # Configure exception handlers
        setup_exception_handlers(logger)

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
    sys.exit(main())
