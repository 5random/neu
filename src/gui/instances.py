from dataclasses import dataclass, field
from typing import Optional, Tuple

from src.cam.camera import Camera
from src.measurement import MeasurementController
from src.notify import EMailSystem

# Non-persistent registry for runtime-only core instances.
# These must NOT be placed into NiceGUI's persistent app.storage.* dicts
# because those are JSON-serialized to disk.

__all__ = [
    'InitializationReport',
    'set_instances',
    'get_instances',
    'set_startup_report',
    'get_startup_report',
    'get_startup_warnings',
    'clear_startup_config_warnings',
    'set_camera',
    'set_measurement',
    'set_email',
    'get_camera',
    'get_measurement',
    'get_email',
    'get_measurement_controller',
    'get_email_system',
]


@dataclass
class InitializationReport:
    config_ok: bool = False
    config_error: str | None = None
    config_warnings: list[str] = field(default_factory=list)
    camera_ok: bool = False
    camera_error: str | None = None
    email_ok: bool = False
    email_error: str | None = None
    measurement_ok: bool = False
    measurement_error: str | None = None

    @property
    def degraded(self) -> bool:
        return not (self.config_ok and self.camera_ok and self.email_ok and self.measurement_ok)

    @property
    def fatal(self) -> bool:
        return not self.config_ok or not self.measurement_ok

    def degraded_messages(self) -> list[str]:
        messages: list[str] = []
        messages.extend(f"Configuration: {warning}" for warning in self.config_warnings if warning)
        if self.config_error:
            messages.append(f"Configuration: {self.config_error}")
        if self.camera_error:
            messages.append(f"Camera: {self.camera_error}")
        if self.email_error:
            messages.append(f"E-Mail: {self.email_error}")
        if self.measurement_error:
            messages.append(f"Measurement: {self.measurement_error}")
        return messages

    def summary(self) -> str:
        messages = self.degraded_messages()
        if messages:
            return "; ".join(messages)
        if self.degraded:
            return "Startup completed in degraded mode"
        return "Startup completed successfully"


_camera: Optional[Camera] = None
_measurement: Optional[MeasurementController] = None
_email: Optional[EMailSystem] = None
_startup_report: Optional[InitializationReport] = None


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


def set_startup_report(report: Optional[InitializationReport]) -> None:
    global _startup_report
    _startup_report = report


def get_startup_report() -> Optional[InitializationReport]:
    return _startup_report


def get_startup_warnings() -> list[str]:
    report = get_startup_report()
    if report is None:
        return []
    messages = report.degraded_messages()
    if messages:
        return messages
    if report.degraded:
        return ["Startup completed in degraded mode"]
    return []


def clear_startup_config_warnings() -> None:
    report = get_startup_report()
    if report is None:
        return
    report.config_warnings.clear()


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
