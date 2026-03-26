from typing import Optional
import logging

from src.config import AppConfig, load_config, set_global_config, get_logger
from src.cam.camera import Camera
from src.measurement import create_measurement_controller_from_config, MeasurementController
from src.notify import create_email_system_from_config, EMailSystem
from src.gui import instances

logger = logging.getLogger('cvd_tracker.gui.init')

def init_application(config_path: str = "config/config.yaml") -> None:
    """Initialize the application components and store them in instances registry."""
    
    # 1. Load Config
    try:
        config = load_config(config_path)
        set_global_config(config, config_path)
        logger.info('Configuration loaded successfully')
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        # We might want to re-raise or continue with partial init, 
        # but for now let's assume config is critical.
        # However, existing code allows GUI to start without config?
        # Let's try to proceed.
        config = None

    if not config:
        return

    # 2. Init Camera
    camera: Optional[Camera] = None
    try:
        logger.info('Initializing Camera...')
        camera = Camera(config, logger=get_logger('camera'))
        # Note: wait_for_init might be needed if async, but Camera ctor seems sync enough for object creation.
        # The actual frame capture start might happen later or here.
        # Existing code in gui_.py calls wait_for_init.
        if camera.wait_for_init(timeout=10.0):
             camera.initialize_routes()
             camera.start_frame_capture()
             logger.info("Camera initialized successfully")
        else:
            logger.error("Camera initialization timeout")
            camera = None
    except Exception as e:
        logger.error(f"Failed to initialize camera: {e}")
        camera = None

    # 3. Init Email
    email_system: Optional[EMailSystem] = None
    try:
        logger.info('Initializing E-Mail-Notification system...')
        email_system = create_email_system_from_config(config, logger=logger)
        logger.info('E-Mail-Notification system initialized successfully')
    except Exception as exc:
        logger.error(f"E-Mail-Notification system initialization failed: {exc}")
        email_system = None

    # 4. Init Measurement
    measurement_controller: Optional[MeasurementController] = None
    try:
        if camera: # Measurement needs camera
            logger.info("Initializing measurement controller...")
            measurement_controller = create_measurement_controller_from_config(
                config=config,
                email_system=email_system,
                camera=camera,
                logger=get_logger('measurement'),
            )
            logger.info("Measurement controller initialized successfully")
        else:
            logger.warning("Skipping MeasurementController init because Camera is missing")
    except Exception as exc:
        logger.error(f"MeasurementController-Init failed: {exc}")
        measurement_controller = None

    # 5. Register instances
    instances.set_instances(camera, measurement_controller, email_system)
    logger.info("Application instances initialized and registered")
