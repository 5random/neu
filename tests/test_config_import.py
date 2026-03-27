from pathlib import Path

import yaml

from src.config import (
    AppConfig,
    ConfigImportEntry,
    _create_default_config,
    analyze_imported_config_text,
    apply_imported_config_preview,
    load_config,
    sync_runtime_config_instances,
)
from src.cam.motion import MotionDetector
from src.notify import EMailSystem


def _entry(preview: object, path: str) -> ConfigImportEntry:
    entries = [item for item in getattr(preview, "entries", []) if item.path == path]
    assert entries, f"missing entry for {path}"
    return entries[0]


def test_analyze_imported_config_reports_ready_invalid_missing_and_unknown() -> None:
    preview = analyze_imported_config_text(
        """
metadata:
  cvd_name: Imported CVD
measurement:
  alert_delay_seconds: 45
  alert_check_interval: -1
email:
  smtp_port: 2525
  extra_key: 1
unknown_section:
  foo: 1
""",
        source_name="import.yaml",
        current_config=_create_default_config(),
    )

    assert preview.errors == []
    assert _entry(preview, "metadata.cvd_name").status == "ready"
    assert _entry(preview, "measurement.alert_delay_seconds").status == "ready"
    assert _entry(preview, "measurement.alert_check_interval").status == "invalid"
    assert _entry(preview, "email.smtp_port").status == "ready"
    assert _entry(preview, "email.smtp_server").status == "missing"
    assert _entry(preview, "email.extra_key").status == "unknown"
    assert _entry(preview, "unknown_section").status == "unknown"


def test_analyze_imported_config_rejects_roi_that_exceeds_imported_resolution() -> None:
    preview = analyze_imported_config_text(
        """
webcam:
  default_resolution:
    width: 100
    height: 100
motion_detection:
  region_of_interest:
    enabled: true
    x: 10
    y: 10
    width: 200
    height: 50
""",
        current_config=_create_default_config(),
    )

    for path in [
        "motion_detection.region_of_interest.enabled",
        "motion_detection.region_of_interest.x",
        "motion_detection.region_of_interest.width",
    ]:
        entry = _entry(preview, path)
        assert entry.status == "invalid"
        assert "frame width" in entry.reason


def test_analyze_imported_config_rejects_unknown_active_groups() -> None:
    preview = analyze_imported_config_text(
        """
email:
  active_groups:
    - missing
""",
        current_config=_create_default_config(),
    )

    entry = _entry(preview, "email.active_groups")
    assert entry.status == "invalid"
    assert "unknown groups" in entry.reason


def test_analyze_imported_config_rejects_reserved_system_group_name() -> None:
    preview = analyze_imported_config_text(
        """
email:
  groups:
    __static__:
      - hidden@example.com
""",
        current_config=_create_default_config(),
    )

    entry = _entry(preview, "email.groups")
    assert entry.status == "invalid"
    assert "reserved group name" in entry.reason


def test_analyze_imported_config_accepts_static_recipients_and_group_prefs() -> None:
    current_cfg = _create_default_config()
    current_cfg.email.explicit_targeting = False
    preview = analyze_imported_config_text(
        """
email:
  groups:
    ops:
      - ops@example.com
  static_recipients:
    - static@example.com
  explicit_targeting: true
  group_prefs:
    ops:
      on_start: false
      on_end: true
      on_stop: true
""",
        current_config=current_cfg,
    )

    assert _entry(preview, "email.static_recipients").status == "ready"
    assert _entry(preview, "email.explicit_targeting").status == "ready"
    assert _entry(preview, "email.group_prefs").status == "ready"


def test_analyze_imported_config_rejects_invalid_explicit_targeting() -> None:
    preview = analyze_imported_config_text(
        """
email:
  explicit_targeting: maybe
""",
        current_config=_create_default_config(),
    )

    entry = _entry(preview, "email.explicit_targeting")
    assert entry.status == "invalid"
    assert "bool" in entry.reason.lower()


def test_analyze_imported_config_rejects_group_prefs_for_unknown_group() -> None:
    preview = analyze_imported_config_text(
        """
email:
  group_prefs:
    missing:
      on_start: true
""",
        current_config=_create_default_config(),
    )

    entry = _entry(preview, "email.group_prefs")
    assert entry.status == "invalid"
    assert "unknown groups" in entry.reason


def test_analyze_imported_config_rejects_unknown_notification_event_key() -> None:
    preview = analyze_imported_config_text(
        """
email:
  notifications:
    on_star: true
""",
        current_config=_create_default_config(),
    )

    entry = _entry(preview, "email.notifications")
    assert entry.status == "invalid"
    assert "unknown event key" in entry.reason


def test_analyze_imported_config_rejects_unknown_group_pref_event_key() -> None:
    preview = analyze_imported_config_text(
        """
email:
  groups:
    ops:
      - ops@example.com
  group_prefs:
    ops:
      on_star: false
""",
        current_config=_create_default_config(),
    )

    entry = _entry(preview, "email.group_prefs")
    assert entry.status == "invalid"
    assert "unknown event key" in entry.reason


def test_analyze_imported_config_rejects_unknown_recipient_pref_event_key() -> None:
    preview = analyze_imported_config_text(
        """
email:
  recipient_prefs:
    ops@example.com:
      on_star: false
""",
        current_config=_create_default_config(),
    )

    entry = _entry(preview, "email.recipient_prefs")
    assert entry.status == "invalid"
    assert "unknown event key" in entry.reason


def test_load_config_uses_default_fallback_for_unknown_email_event_key() -> None:
    default_cfg = _create_default_config()
    temp_path = Path(".pytest_local_runtime")
    temp_path.mkdir(exist_ok=True)
    config_path = temp_path / "invalid_event_config.yaml"
    log_path = temp_path / "app.log"
    raw = {
        "metadata": {
            "version": default_cfg.metadata.version,
            "description": default_cfg.metadata.description,
            "cvd_id": default_cfg.metadata.cvd_id,
            "cvd_name": default_cfg.metadata.cvd_name,
            "released_at": default_cfg.metadata.released_at,
        },
        "webcam": {
            "camera_index": default_cfg.webcam.camera_index,
            "default_resolution": dict(default_cfg.webcam.default_resolution),
            "fps": default_cfg.webcam.fps,
            "resolution": list(default_cfg.webcam.resolution),
        },
        "uvc_controls": {
            "brightness": default_cfg.uvc_controls.brightness,
            "hue": default_cfg.uvc_controls.hue,
            "contrast": default_cfg.uvc_controls.contrast,
            "saturation": default_cfg.uvc_controls.saturation,
            "sharpness": default_cfg.uvc_controls.sharpness,
            "gamma": default_cfg.uvc_controls.gamma,
            "white_balance": {
                "auto": default_cfg.uvc_controls.white_balance.auto,
                "value": default_cfg.uvc_controls.white_balance.value,
            },
            "gain": default_cfg.uvc_controls.gain,
            "backlight_compensation": default_cfg.uvc_controls.backlight_compensation,
            "exposure": {
                "auto": default_cfg.uvc_controls.exposure.auto,
                "value": default_cfg.uvc_controls.exposure.value,
            },
        },
        "motion_detection": {
            "region_of_interest": dict(default_cfg.motion_detection.region_of_interest),
            "sensitivity": default_cfg.motion_detection.sensitivity,
            "background_learning_rate": default_cfg.motion_detection.background_learning_rate,
            "min_contour_area": default_cfg.motion_detection.min_contour_area,
        },
        "measurement": {
            "auto_start": default_cfg.measurement.auto_start,
            "session_timeout_minutes": default_cfg.measurement.session_timeout_minutes,
            "session_timeout_seconds": default_cfg.measurement.session_timeout_seconds,
            "save_alert_images": default_cfg.measurement.save_alert_images,
            "image_save_path": default_cfg.measurement.image_save_path,
            "image_format": default_cfg.measurement.image_format,
            "image_quality": default_cfg.measurement.image_quality,
            "alert_delay_seconds": default_cfg.measurement.alert_delay_seconds,
            "max_alerts_per_session": default_cfg.measurement.max_alerts_per_session,
            "alert_check_interval": default_cfg.measurement.alert_check_interval,
            "alert_cooldown_seconds": default_cfg.measurement.alert_cooldown_seconds,
            "alert_include_snapshot": default_cfg.measurement.alert_include_snapshot,
            "inactivity_timeout_minutes": default_cfg.measurement.inactivity_timeout_minutes,
            "motion_summary_interval_seconds": default_cfg.measurement.motion_summary_interval_seconds,
            "enable_motion_summary_logs": default_cfg.measurement.enable_motion_summary_logs,
        },
        "email": {
            "website_url": default_cfg.email.website_url,
            "recipients": list(default_cfg.email.recipients),
            "smtp_server": default_cfg.email.smtp_server,
            "smtp_port": default_cfg.email.smtp_port,
            "sender_email": default_cfg.email.sender_email,
            "templates": dict(default_cfg.email.templates),
            "groups": dict(default_cfg.email.groups),
            "active_groups": list(default_cfg.email.active_groups),
            "static_recipients": list(default_cfg.email.static_recipients),
            "explicit_targeting": default_cfg.email.explicit_targeting,
            "notifications": {"on_star": True},
            "group_prefs": dict(default_cfg.email.group_prefs),
            "recipient_prefs": dict(default_cfg.email.recipient_prefs),
        },
        "gui": {
            "title": default_cfg.gui.title,
            "host": default_cfg.gui.host,
            "port": default_cfg.gui.port,
            "auto_open_browser": default_cfg.gui.auto_open_browser,
            "update_interval_ms": default_cfg.gui.update_interval_ms,
        },
        "logging": {
            "level": default_cfg.logging.level,
            "file": str(log_path),
            "max_file_size_mb": default_cfg.logging.max_file_size_mb,
            "backup_count": default_cfg.logging.backup_count,
            "console_output": False,
        },
    }
    try:
        config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        loaded = load_config(str(config_path))
        assert loaded.email.notifications == default_cfg.email.notifications
    finally:
        for path in (config_path, log_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            temp_path.rmdir()
        except OSError:
            pass


def test_apply_imported_config_preview_applies_only_selected_paths() -> None:
    cfg = _create_default_config()
    preview = analyze_imported_config_text(
        """
metadata:
  cvd_name: Imported CVD
measurement:
  alert_delay_seconds: 45
""",
        current_config=cfg,
    )

    result = apply_imported_config_preview(
        preview,
        selected_paths=["metadata.cvd_name"],
        target_config=cfg,
    )

    assert result.ok
    assert result.applied_paths == ["metadata.cvd_name"]
    assert cfg.metadata.cvd_name == "Imported CVD"
    assert cfg.measurement.alert_delay_seconds == 300


def test_apply_imported_config_preview_requires_target_path_for_non_global_persist() -> None:
    cfg = _create_default_config()
    preview = analyze_imported_config_text(
        """
metadata:
  cvd_name: Persisted Name
""",
        current_config=cfg,
    )

    result = apply_imported_config_preview(
        preview,
        target_config=cfg,
        persist=True,
    )

    assert not result.ok
    assert result.errors
    assert "target_path" in result.errors[0]


def test_apply_imported_config_preview_persists_explicit_target_path() -> None:
    cfg = _create_default_config()
    preview = analyze_imported_config_text(
        """
metadata:
  cvd_name: Persisted Name
""",
        current_config=cfg,
    )
    target_path = Path.cwd() / "config_import_persist_test.yaml"
    try:
        result = apply_imported_config_preview(
            preview,
            target_config=cfg,
            target_path=str(target_path),
            persist=True,
        )

        assert result.ok
        assert target_path.exists()
        assert "Persisted Name" in target_path.read_text(encoding="utf-8")
    finally:
        target_path.unlink(missing_ok=True)


class _DummyCamera:
    def __init__(self, cfg: AppConfig) -> None:
        self.app_config = cfg
        self.webcam_config = cfg.webcam
        self.uvc_config = cfg.uvc_controls
        self.measurement_config = cfg.measurement
        self.motion_detector = MotionDetector(cfg.motion_detection)


class _DummyMeasurementController:
    def __init__(self, cfg: AppConfig) -> None:
        self.config = cfg.measurement


def test_sync_runtime_config_instances_updates_motion_detector_and_email_system() -> None:
    cfg = _create_default_config()
    camera = _DummyCamera(cfg)
    measurement_controller = _DummyMeasurementController(cfg)
    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    preview = analyze_imported_config_text(
        """
measurement:
  alert_delay_seconds: 600
  alert_cooldown_seconds: 600
motion_detection:
  sensitivity: 0.77
  background_learning_rate: 0.123
  min_contour_area: 444
  region_of_interest:
    enabled: true
    x: 10
    y: 20
    width: 300
    height: 200
""",
        current_config=cfg,
    )

    apply_result = apply_imported_config_preview(preview, target_config=cfg)
    assert apply_result.ok

    sync_result = sync_runtime_config_instances(
        cfg,
        applied_paths=apply_result.applied_paths,
        camera=camera,
        measurement_controller=measurement_controller,
        email_system=email_system,
    )

    assert sync_result.ok
    assert measurement_controller.config is cfg.measurement
    assert camera.motion_detector.sensitivity == 0.77
    assert camera.motion_detector.learning_rate == 0.123
    assert camera.motion_detector.roi.enabled is True
    assert camera.motion_detector.roi.x == 10
    assert email_system.alert_cooldown_seconds == 600
    assert email_system.cooldown_minutes == 10
    email_system.close()


def test_sync_runtime_config_instances_keeps_effective_motion_threshold_on_roi_only_import() -> None:
    cfg = _create_default_config()
    cfg.motion_detection.sensitivity = 0.33
    camera = _DummyCamera(cfg)
    camera.motion_detector.update_sensitivity(cfg.motion_detection.sensitivity)
    expected_min_contour_area = camera.motion_detector.min_contour_area

    preview = analyze_imported_config_text(
        """
motion_detection:
  region_of_interest:
    enabled: true
    x: 10
    y: 20
    width: 100
    height: 100
""",
        current_config=cfg,
    )

    apply_result = apply_imported_config_preview(preview, target_config=cfg)
    assert apply_result.ok

    sync_result = sync_runtime_config_instances(
        cfg,
        applied_paths=apply_result.applied_paths,
        camera=camera,
    )

    assert sync_result.ok
    assert camera.motion_detector.roi.enabled is True
    assert camera.motion_detector.min_contour_area == expected_min_contour_area


def test_sync_runtime_config_instances_does_not_reset_background_model_on_sensitivity_only_import() -> None:
    cfg = _create_default_config()
    camera = _DummyCamera(cfg)
    camera.motion_detector.is_learning = False
    camera.motion_detector.learning_frame_count = 99

    preview = analyze_imported_config_text(
        """
motion_detection:
  sensitivity: 0.33
""",
        current_config=cfg,
    )

    apply_result = apply_imported_config_preview(preview, target_config=cfg)
    assert apply_result.ok

    sync_result = sync_runtime_config_instances(
        cfg,
        applied_paths=apply_result.applied_paths,
        camera=camera,
    )

    assert sync_result.ok
    assert camera.motion_detector.sensitivity == 0.33
    assert camera.motion_detector.is_learning is False
    assert camera.motion_detector.learning_frame_count == 99
