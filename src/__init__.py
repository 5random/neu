"""Lightweight top-level package to avoid circular imports.

Exposes common symbols from subpackages and lazy-loads measurement-related
objects to break import cycles.
"""

import importlib
from typing import Any

from .notify import (
    EMailSystem,
    create_email_system_from_config,
    create_alert_system_from_config,
)
from .cam import (
    Camera,
    MotionDetector,
    MotionResult,
    create_motion_detector_from_config,
)
from .config import (
    AppConfig,
    WebcamConfig,
    UVCConfig,
    MotionDetectionConfig,
    MeasurementConfig,
    EmailConfig,
    GUIConfig,
    LoggingConfig,
    load_config,
    save_config,
)

__all__ = [
    "AppConfig",
    "WebcamConfig",
    "UVCConfig",
    "MotionDetectionConfig",
    "MeasurementConfig",
    "EmailConfig",
    "GUIConfig",
    "LoggingConfig",
    "load_config",
    "save_config",
    "EMailSystem",
    "create_email_system_from_config",
    "create_alert_system_from_config",
    "Camera",
    "MotionDetector",
    "MotionResult",
    "create_motion_detector_from_config",
]

def __getattr__(name: str) -> Any:
    # Lazy-load measurement symbols on demand to break cycles
    if name in ("MeasurementController", "create_measurement_controller_from_config"):
        mod = importlib.import_module(".measurement", __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__} has no attribute {name!r}")