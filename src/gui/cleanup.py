import threading
import logging
import signal
import sys
from typing import Any
from nicegui import app

from src.gui import instances

logger = logging.getLogger('gui.cleanup')

_shutdown_requested = threading.Event()
_shutdown_lock = threading.Lock()
_cleanup_completed = threading.Event()

def cleanup_application_sync() -> None:
    """Synchronous cleanup of all components."""
    camera, measurement, email = instances.get_instances()
    
    logger.info("Starting synchronous application cleanup...")
    
    # Measurement
    if measurement:
        try:
            measurement.cleanup()
            logger.info("Measurement controller cleanup completed")
        except Exception as e:
            logger.error(f"Error during measurement cleanup: {e}")

    # Email
    if email:
        try:
            email.cleanup()
            logger.info("Email system cleanup completed")
        except Exception as e:
            logger.error(f"Error during email cleanup: {e}")

    # Camera (if sync cleanup is available/sufficient)
    if camera:
        try:
            camera.cleanup()
            logger.info("Camera cleanup completed")
        except Exception as e:
            logger.error(f"Error during camera cleanup: {e}")

    logger.info("Synchronous cleanup completed")

def cleanup_application(*, wait_if_already_running: bool = False) -> None:
    """Main cleanup entry point."""
    should_run_cleanup = False
    with _shutdown_lock:
        if _shutdown_requested.is_set():
            if not wait_if_already_running:
                return
        else:
            _shutdown_requested.set()
            _cleanup_completed.clear()
            should_run_cleanup = True

    if not should_run_cleanup:
        _cleanup_completed.wait()
        return

    logger.info("Starting application cleanup...")
    
    try:
        cleanup_application_sync()
        logger.info("Application cleanup completed successfully")
             
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    finally:
        _cleanup_completed.set()

def signal_handler(signum: int, frame: Any) -> None:
    """Signal handler for graceful shutdown."""
    logger.info(f"Received signal {signum}, initiating shutdown...")

    try:
        cleanup_application(wait_if_already_running=True)
        app.shutdown()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        sys.exit(0)

def register_signal_handlers() -> None:
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

