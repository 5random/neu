import threading
import logging
import signal
import sys
import time
from typing import Any
from nicegui import app

from src.gui import instances

logger = logging.getLogger('gui.cleanup')

_shutdown_requested = threading.Event()
_shutdown_lock = threading.Lock()
_cleanup_completed = threading.Event()
_cleanup_owner_thread_id: int | None = None
_CAMERA_CLEANUP_MAX_ATTEMPTS = 3
_CAMERA_CLEANUP_RETRY_DELAY_SECONDS = 0.1


def _cleanup_camera_with_retries(camera: object) -> bool:
    """Retry camera cleanup briefly when the first pass stays partial."""
    cleanup = getattr(camera, "cleanup", None)
    if not callable(cleanup):
        return True

    supports_clean_state = hasattr(camera, "cleaned")
    max_attempts = max(1, _CAMERA_CLEANUP_MAX_ATTEMPTS)
    for attempt in range(1, max_attempts + 1):
        cleanup()
        if not supports_clean_state or bool(getattr(camera, "cleaned", False)):
            return True
        if attempt < max_attempts:
            logger.warning(
                "Camera cleanup stayed partial after attempt %s/%s; retrying shortly",
                attempt,
                max_attempts,
            )
            time.sleep(_CAMERA_CLEANUP_RETRY_DELAY_SECONDS)

    return not supports_clean_state or bool(getattr(camera, "cleaned", False))

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
            if _cleanup_camera_with_retries(camera):
                logger.info("Camera cleanup completed")
            else:
                logger.warning("Camera cleanup completed partially after retries")
        except Exception as e:
            logger.error(f"Error during camera cleanup: {e}")

    logger.info("Synchronous cleanup completed")


def cleanup_application(*, wait_if_already_running: bool = False) -> bool:
    """Main cleanup entry point.

    Returns ``False`` only when a re-entrant call on the active cleanup thread
    skips waiting to avoid deadlocking the outer cleanup invocation.
    """
    global _cleanup_owner_thread_id

    should_run_cleanup = False
    should_wait_for_cleanup = False
    current_thread_id = threading.get_ident()
    with _shutdown_lock:
        if _shutdown_requested.is_set():
            if not wait_if_already_running:
                return True
            if (
                _cleanup_owner_thread_id == current_thread_id
                and not _cleanup_completed.is_set()
            ):
                logger.warning(
                    "Cleanup re-entered on the active cleanup thread; "
                    "skipping nested wait to avoid deadlock"
                )
                return False
            should_wait_for_cleanup = True
        else:
            _shutdown_requested.set()
            _cleanup_completed.clear()
            _cleanup_owner_thread_id = current_thread_id
            should_run_cleanup = True

    if not should_run_cleanup:
        if should_wait_for_cleanup:
            _cleanup_completed.wait()
        return True

    logger.info("Starting application cleanup...")

    try:
        cleanup_application_sync()
        logger.info("Application cleanup completed successfully")

    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    finally:
        with _shutdown_lock:
            _cleanup_owner_thread_id = None
            _cleanup_completed.set()

    return True

def signal_handler(signum: int, frame: Any) -> None:
    """Signal handler for graceful shutdown."""
    logger.info(f"Received signal {signum}, initiating shutdown...")

    try:
        if cleanup_application(wait_if_already_running=True):
            # False means re-entrant call on cleanup thread; outer cleanup will complete.
            app.shutdown()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        sys.exit(0)

def register_signal_handlers() -> None:
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

