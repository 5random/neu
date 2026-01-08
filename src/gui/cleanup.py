import asyncio
import threading
import logging
import signal
import sys
from typing import Optional, Any
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

async def cleanup_camera_async() -> None:
    """Async cleanup for camera if needed."""
    camera = instances.get_camera()
    if camera:
        try:
            # Assuming camera.cleanup might be async or we want to ensure async context
            # If camera.cleanup is strictly sync, this wrapper is still fine.
            # But if camera has an async_cleanup method, call it here.
            # For now, we reuse the sync cleanup as per existing code, 
            # but wrapping it in async function allows future expansion.
            camera.cleanup() 
            logger.info("Async Camera cleanup completed")
        except Exception as e:
            logger.error(f"Error during async camera cleanup: {e}")

def schedule_async_cleanup() -> None:
    """Schedule async cleanup on the running event loop."""
    try:
        loop = asyncio.get_running_loop()
        
        def cleanup_and_signal() -> None:
            async def full_cleanup() -> None:
                try:
                    await cleanup_camera_async()
                finally:
                    _cleanup_completed.set()
            loop.create_task(full_cleanup())
        
        loop.call_soon_threadsafe(cleanup_and_signal)
    except RuntimeError:
        logger.warning("No running event loop found, skipping async cleanup")
        _cleanup_completed.set()

def cleanup_application() -> None:
    """Main cleanup entry point."""
    with _shutdown_lock:
        if _shutdown_requested.is_set():
            return
        _shutdown_requested.set()

    logger.info("Starting application cleanup...")
    
    try:
        cleanup_application_sync()
        schedule_async_cleanup()
        
        # Wait for async cleanup
        if _cleanup_completed.wait(timeout=2.0):
            logger.info("Application cleanup completed successfully")
        else:
            logger.warning("Async cleanup timeout - proceeding with shutdown")
            
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    finally:
        _cleanup_completed.set()

def signal_handler(signum: int, frame: Any) -> None:
    """Signal handler for graceful shutdown."""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    
    cleanup_thread = threading.Thread(target=cleanup_application, daemon=True)
    cleanup_thread.start()
    
    if _cleanup_completed.wait(timeout=3.0):
        logger.info("Cleanup completed, exiting...")
    else:
        logger.warning("Cleanup timeout, forcing exit...")
    
    try:
        app.shutdown()
    except Exception:
        sys.exit(0)

def register_signal_handlers() -> None:
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

