from __future__ import annotations

from typing import TYPE_CHECKING, Optional, TypeGuard
import logging

from src import config as config_module
from src.cam import camera as camera_module
from src.config import (
    ConfigLoadError,
    get_global_config,
    get_global_config_path,
    get_global_config_warnings,
    get_logger,
    load_config,
    set_global_config,
)
from src.cam.camera import Camera
from src.measurement import create_measurement_controller_from_config, MeasurementController
from src.notify import create_email_system_from_config, EMailSystem
from src.gui import instances

if TYPE_CHECKING:
    from src.config import AppConfig

logger = logging.getLogger('cvd_tracker.gui.init')
def _load_effective_config(config_path: str) -> "AppConfig":
    resolved_requested_path = str(config_module._resolve_config_path(config_path))
    current_config = get_global_config()
    current_config_path = get_global_config_path()
    current_config_warnings = get_global_config_warnings()
    current_config_warning_attached = config_module._get_attached_startup_config_warnings(current_config)
    if (
        current_config is not None
        and current_config_path == resolved_requested_path
        and not current_config_warnings
        and not current_config_warning_attached
    ):
        logger.debug("Reusing already loaded configuration for %s", resolved_requested_path)
        return current_config

    config = load_config(config_path, startup_fallback=True)
    set_global_config(config, config_path)
    return config


def _cleanup_optional_component(component: object | None, *, label: str) -> None:
    if component is None:
        return
    try:
        cleanup = getattr(component, "cleanup", None)
        if callable(cleanup):
            cleanup()
            return
        close = getattr(component, "close", None)
        if callable(close):
            close()
    except Exception as exc:
        logger.debug("Cleanup of %s failed: %s", label, exc)


def _is_active_video_source(candidate: object) -> TypeGuard[camera_module._ActiveVideoSource]:
    return callable(getattr(candidate, "get_current_frame", None))


def _snapshot_runtime_state() -> tuple[
    Optional[Camera],
    Optional[MeasurementController],
    Optional[EMailSystem],
    Optional[instances.InitializationReport],
    "AppConfig | None",
    str,
]:
    camera, measurement, email = instances.get_instances()
    current_config = get_global_config()
    current_config_path = get_global_config_path() or config_module._config_path
    return (
        camera,
        measurement,
        email,
        instances.get_startup_report(),
        current_config,
        current_config_path,
    )


def _restore_config_snapshot(config: "AppConfig | None", path: str) -> None:
    resolved_path = str(config_module._resolve_config_path(path))
    if config is not None:
        set_global_config(config, resolved_path)
        return
    config_module._global_config = None
    config_module._config_path = resolved_path
    config_module.clear_global_config_warnings()


def _cleanup_runtime_components(
    *,
    measurement: Optional[MeasurementController],
    email: Optional[EMailSystem],
    camera: Optional[Camera],
    skip: tuple[object | None, ...] = (),
    label_prefix: str,
) -> None:
    skip_ids = {id(component) for component in skip if component is not None}
    for component, label in (
        (measurement, f"{label_prefix} measurement"),
        (email, f"{label_prefix} email"),
        (camera, f"{label_prefix} camera"),
    ):
        if component is None or id(component) in skip_ids:
            continue
        _cleanup_optional_component(component, label=label)


def _create_camera(config: "AppConfig") -> Camera:
    return Camera(
        config,
        logger=get_logger('camera'),
        async_init=False,
        initialize=False,
    )


def _restore_video_runtime(camera: object | None) -> None:
    if camera is None:
        camera_module._clear_active_video_camera(force=True)
        return
    initialize_routes = getattr(camera, "initialize_routes", None)
    if callable(initialize_routes):
        initialize_routes()
        if _is_active_video_source(camera):
            camera_module._activate_video_camera(camera)
        return
    if _is_active_video_source(camera):
        camera_module._activate_video_camera(camera)
        return
    camera_module._clear_active_video_camera(force=True)


def _suspend_previous_camera_for_replacement(camera: object | None) -> tuple[bool, bool]:
    if camera is None:
        return True, False
    suspend_runtime = getattr(camera, "suspend_runtime", None)
    if not callable(suspend_runtime):
        logger.error("Existing camera runtime cannot be suspended before replacement")
        return False, False
    try:
        suspended = bool(suspend_runtime())
    except Exception:
        logger.exception("Failed to suspend existing camera runtime before replacement")
        return False, True
    if suspended:
        if _is_active_video_source(camera):
            camera_module._clear_active_video_camera(camera)
        return True, True
    logger.error("Existing camera runtime refused suspension before replacement")
    return False, True


def _restore_previous_runtime_state(
    *,
    previous_config: "AppConfig | None",
    previous_config_path: str,
    previous_camera: object | None,
    previous_camera_restore_required: bool,
) -> bool:
    _restore_config_snapshot(previous_config, previous_config_path)
    if previous_camera_restore_required:
        if previous_camera is None:
            camera_module._clear_active_video_camera(force=True)
            return False
        initialize_sync = getattr(previous_camera, "initialize_sync", None)
        if not callable(initialize_sync):
            logger.error("Previous camera runtime cannot be restored after replacement failure")
            camera_module._clear_active_video_camera(force=True)
            return False
        try:
            if not bool(initialize_sync()):
                logger.error("Failed to resume previous camera runtime after replacement failure")
                camera_module._clear_active_video_camera(force=True)
                return False
        except Exception:
            logger.exception("Failed to resume previous camera runtime after replacement failure")
            camera_module._clear_active_video_camera(force=True)
            return False
    _restore_video_runtime(previous_camera)
    return True


def _mark_restore_failure_on_report(report: instances.InitializationReport) -> None:
    restore_failure_message = "previous camera could not be restored after replacement failure"
    if report.camera_error:
        if restore_failure_message not in report.camera_error.lower():
            report.camera_error = f"{report.camera_error}; Previous camera could not be restored after replacement failure"
        return
    report.camera_error = "Previous camera could not be restored after replacement failure"


def _commit_degraded_runtime_without_camera(
    *,
    previous_camera: object | None,
    previous_measurement: Optional[MeasurementController],
    previous_email: Optional[EMailSystem],
    report: instances.InitializationReport,
) -> None:
    camera_module._clear_active_video_camera(force=True)
    if previous_measurement is not None:
        set_camera = getattr(previous_measurement, "set_camera", None)
        if callable(set_camera):
            try:
                set_camera(None)
            except Exception:
                logger.exception("Failed to detach degraded measurement controller from previous camera")
        elif hasattr(previous_measurement, "camera"):
            try:
                setattr(previous_measurement, "camera", None)
            except Exception:
                logger.exception("Failed to clear degraded measurement controller camera reference")
    instances.set_instances(None, previous_measurement, previous_email)
    instances.set_startup_report(report)
    _cleanup_optional_component(previous_camera, label="previous camera")


def _restore_or_degrade_previous_runtime(
    *,
    previous_config: "AppConfig | None",
    previous_config_path: str,
    previous_camera: object | None,
    previous_camera_restore_required: bool,
    previous_measurement: Optional[MeasurementController],
    previous_email: Optional[EMailSystem],
    report: instances.InitializationReport,
) -> None:
    if _restore_previous_runtime_state(
        previous_config=previous_config,
        previous_config_path=previous_config_path,
        previous_camera=previous_camera,
        previous_camera_restore_required=previous_camera_restore_required,
    ):
        return
    _mark_restore_failure_on_report(report)
    _commit_degraded_runtime_without_camera(
        previous_camera=previous_camera,
        previous_measurement=previous_measurement,
        previous_email=previous_email,
        report=report,
    )


def _commit_runtime(
    *,
    config: "AppConfig",
    config_path: str,
    camera: Optional[Camera],
    measurement: Optional[MeasurementController],
    email: Optional[EMailSystem],
    report: instances.InitializationReport,
    previous_camera: Optional[Camera],
    previous_measurement: Optional[MeasurementController],
    previous_email: Optional[EMailSystem],
) -> None:
    set_global_config(config, config_path)
    if camera is not None:
        camera.initialize_routes()
        camera.start_frame_capture()
    else:
        camera_module._clear_active_video_camera(force=True)
    instances.set_instances(camera, measurement, email)
    instances.set_startup_report(report)
    _cleanup_runtime_components(
        measurement=previous_measurement,
        email=previous_email,
        camera=previous_camera,
        skip=(measurement, email, camera),
        label_prefix="previous",
    )


def init_application(config_path: str = "config/config.yaml") -> instances.InitializationReport:
    """Initialize runtime components and register them in the shared instances registry."""
    report = instances.InitializationReport()
    (
        previous_camera,
        previous_measurement,
        previous_email,
        previous_report,
        previous_config,
        previous_config_path,
    ) = _snapshot_runtime_state()
    has_existing_runtime = any(
        component is not None
        for component in (previous_camera, previous_measurement, previous_email)
    )
    previous_camera_restore_required = False

    try:
        config = _load_effective_config(config_path)
        report.config_ok = True
        report.config_warnings = get_global_config_warnings()
        logger.info('Configuration loaded successfully')
    except ConfigLoadError as exc:
        report.config_error = str(exc)
        logger.error("Failed to load config: %s", exc)
        _restore_config_snapshot(previous_config, previous_config_path)
        if has_existing_runtime:
            _restore_video_runtime(previous_camera)
            return report
        instances.set_instances(None, None, None)
        instances.set_startup_report(report)
        camera_module._clear_active_video_camera(force=True)
        return report
    except Exception as exc:
        report.config_error = str(exc)
        logger.exception("Unexpected failure while loading config")
        _restore_config_snapshot(previous_config, previous_config_path)
        if has_existing_runtime:
            _restore_video_runtime(previous_camera)
            return report
        instances.set_instances(None, None, None)
        instances.set_startup_report(report)
        camera_module._clear_active_video_camera(force=True)
        return report

    camera: Optional[Camera] = None
    try:
        logger.info('Preparing Camera runtime...')
        camera = _create_camera(config)
    except Exception as exc:
        report.camera_error = str(exc)
        logger.warning("Camera construction degraded startup: %s", exc)
        if has_existing_runtime:
            _cleanup_runtime_components(
                measurement=None,
                email=None,
                camera=camera,
                skip=(previous_camera,),
                label_prefix="replacement",
            )
            _restore_or_degrade_previous_runtime(
                previous_config=previous_config,
                previous_config_path=previous_config_path,
                previous_camera=previous_camera,
                previous_camera_restore_required=previous_camera_restore_required,
                previous_measurement=previous_measurement,
                previous_email=previous_email,
                report=report,
            )
            return report
        camera = None

    try:
        if camera is not None:
            logger.info('Initializing Camera...')
            if has_existing_runtime:
                suspension_ok, previous_camera_restore_required = _suspend_previous_camera_for_replacement(previous_camera)
                if not suspension_ok and previous_camera is not None:
                    report.camera_error = "Failed to suspend existing camera runtime before replacement"
                    _cleanup_runtime_components(
                        measurement=None,
                        email=None,
                        camera=camera,
                        skip=(previous_camera,),
                        label_prefix="replacement",
                    )
                    _restore_or_degrade_previous_runtime(
                        previous_config=previous_config,
                        previous_config_path=previous_config_path,
                        previous_camera=previous_camera,
                        previous_camera_restore_required=previous_camera_restore_required,
                        previous_measurement=previous_measurement,
                        previous_email=previous_email,
                        report=report,
                    )
                    return report

            if camera.initialize_sync():
                report.camera_ok = True
                logger.info("Camera initialized successfully")
            else:
                camera_error = camera.initialization_error or RuntimeError("Camera initialization did not reach a ready state")
                report.camera_error = str(camera_error)
                logger.warning("Camera initialization degraded startup: %s", camera_error)
                if has_existing_runtime:
                    _cleanup_runtime_components(
                        measurement=None,
                        email=None,
                        camera=camera,
                        skip=(previous_camera,),
                        label_prefix="replacement",
                    )
                    _restore_or_degrade_previous_runtime(
                        previous_config=previous_config,
                        previous_config_path=previous_config_path,
                        previous_camera=previous_camera,
                        previous_camera_restore_required=previous_camera_restore_required,
                        previous_measurement=previous_measurement,
                        previous_email=previous_email,
                        report=report,
                    )
                    return report
                _cleanup_runtime_components(
                    measurement=None,
                    email=None,
                    camera=camera,
                    label_prefix="new",
                )
                camera = None
    except Exception as exc:
        report.camera_error = str(exc)
        logger.warning("Camera initialization degraded startup: %s", exc)
        if has_existing_runtime:
            _cleanup_runtime_components(
                measurement=None,
                email=None,
                camera=camera,
                skip=(previous_camera,),
                label_prefix="replacement",
            )
            _restore_or_degrade_previous_runtime(
                previous_config=previous_config,
                previous_config_path=previous_config_path,
                previous_camera=previous_camera,
                previous_camera_restore_required=previous_camera_restore_required,
                previous_measurement=previous_measurement,
                previous_email=previous_email,
                report=report,
            )
            return report
        _cleanup_runtime_components(
            measurement=None,
            email=None,
            camera=camera,
            label_prefix="new",
        )
        camera = None

    email_system: Optional[EMailSystem] = None
    try:
        logger.info('Initializing E-Mail-Notification system...')
        email_system = create_email_system_from_config(config, logger=logger)
        report.email_ok = True
        logger.info('E-Mail-Notification system initialized successfully')
    except Exception as exc:
        report.email_error = str(exc)
        logger.warning("E-Mail-Notification system degraded startup: %s", exc)
        email_system = None

    measurement_controller: Optional[MeasurementController] = None
    try:
        logger.info("Initializing measurement controller...")
        measurement_controller = create_measurement_controller_from_config(
            config=config,
            email_system=email_system,
            camera=camera,
            logger=get_logger('measurement'),
        )
        report.measurement_ok = True
        logger.info("Measurement controller initialized successfully")
    except Exception as exc:
        report.measurement_error = str(exc)
        logger.error("MeasurementController init failed: %s", exc)
        if has_existing_runtime:
            _cleanup_runtime_components(
                measurement=measurement_controller,
                email=email_system,
                camera=camera,
                skip=(previous_measurement, previous_email, previous_camera),
                label_prefix="replacement",
            )
            _restore_or_degrade_previous_runtime(
                previous_config=previous_config,
                previous_config_path=previous_config_path,
                previous_camera=previous_camera,
                previous_camera_restore_required=previous_camera_restore_required,
                previous_measurement=previous_measurement,
                previous_email=previous_email,
                report=report,
            )
            return report
        measurement_controller = None

    if has_existing_runtime:
        if report.fatal:
            _cleanup_runtime_components(
                measurement=measurement_controller,
                email=email_system,
                camera=camera,
                skip=(previous_measurement, previous_email, previous_camera),
                label_prefix="replacement",
            )
            _restore_or_degrade_previous_runtime(
                previous_config=previous_config,
                previous_config_path=previous_config_path,
                previous_camera=previous_camera,
                previous_camera_restore_required=previous_camera_restore_required,
                previous_measurement=previous_measurement,
                previous_email=previous_email,
                report=report,
            )
            return report
        _commit_runtime(
            config=config,
            config_path=config_path,
            camera=camera,
            measurement=measurement_controller,
            email=email_system,
            report=report,
            previous_camera=previous_camera,
            previous_measurement=previous_measurement,
            previous_email=previous_email,
        )
        logger.info(
            "Application instances initialized and registered (degraded=%s)",
            report.degraded,
        )
        return report

    if report.fatal:
        _cleanup_runtime_components(
            measurement=measurement_controller,
            email=email_system,
            camera=camera,
            label_prefix="new",
        )
        instances.set_instances(None, None, None)
        instances.set_startup_report(report)
        camera_module._clear_active_video_camera(force=True)
        return report

    _commit_runtime(
        config=config,
        config_path=config_path,
        camera=camera,
        measurement=measurement_controller,
        email=email_system,
        report=report,
        previous_camera=None,
        previous_measurement=None,
        previous_email=None,
    )
    logger.info(
        "Application instances initialized and registered (degraded=%s)",
        report.degraded,
    )
    return report
