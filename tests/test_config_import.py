from pathlib import Path

from src.config import (
    AppConfig,
    ConfigImportEntry,
    _create_default_config,
    analyze_imported_config_text,
    apply_imported_config_preview,
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
