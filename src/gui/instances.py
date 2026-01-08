from typing import Optional, Tuple

from src.cam.camera import Camera
from src.measurement import MeasurementController
from src.notify import EMailSystem

# Non-persistent registry for runtime-only core instances.
# These must NOT be placed into NiceGUI's persistent app.storage.* dicts
# because those are JSON-serialized to disk.

__all__ = [
    'set_instances',
    'get_instances',
    'set_camera',
    'set_measurement',
    'set_email',
    'get_camera',
    'get_measurement',
    'get_email',
    'get_measurement_controller',
    'get_email_system',
]

_camera: Optional[Camera] = None
_measurement: Optional[MeasurementController] = None
_email: Optional[EMailSystem] = None


def set_instances(
    camera: Optional[Camera],
    measurement: Optional[MeasurementController],
    email: Optional[EMailSystem],
) -> None:
    global _camera, _measurement, _email
    _camera = camera
    _measurement = measurement
    _email = email


def get_instances() -> Tuple[Optional[Camera], Optional[MeasurementController], Optional[EMailSystem]]:
    return _camera, _measurement, _email


def set_camera(camera: Optional[Camera]) -> None:
    global _camera
    _camera = camera


def set_measurement(measurement: Optional[MeasurementController]) -> None:
    global _measurement
    _measurement = measurement


def set_email(email: Optional[EMailSystem]) -> None:
    global _email
    _email = email


def get_camera() -> Optional[Camera]:
    return _camera


def get_measurement() -> Optional[MeasurementController]:
    return _measurement


def get_email() -> Optional[EMailSystem]:
    return _email


# Aliases for convenience and clearer naming
get_measurement_controller = get_measurement
get_email_system = get_email
