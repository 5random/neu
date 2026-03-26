from datetime import datetime, timedelta

from src.config import _create_default_config
from src.measurement import MeasurementController


def test_measurement_config_timeout_seconds_overrides_legacy_minutes() -> None:
    cfg = _create_default_config()

    cfg.measurement.session_timeout_minutes = 5
    cfg.measurement.session_timeout_seconds = 0
    assert cfg.measurement.get_session_timeout_seconds() == 300

    cfg.measurement.set_session_timeout_seconds(90)
    assert cfg.measurement.session_timeout_seconds == 90
    assert cfg.measurement.session_timeout_minutes == 2
    assert cfg.measurement.get_session_timeout_seconds() == 90


def test_measurement_controller_uses_exact_timeout_seconds() -> None:
    cfg = _create_default_config()
    cfg.measurement.set_session_timeout_seconds(30)

    controller = MeasurementController(cfg.measurement, email_system=None, camera=None)
    try:
        assert controller.start_session()
        assert controller.is_session_active is True

        controller.session_start_time = datetime.now() - timedelta(seconds=29)
        controller.check_session_timeout()
        assert controller.is_session_active is True

        controller.session_start_time = datetime.now() - timedelta(seconds=30)
        controller.check_session_timeout()
        assert controller.is_session_active is False
    finally:
        controller.cleanup()


def test_measurement_status_exposes_timeout_seconds() -> None:
    cfg = _create_default_config()
    cfg.measurement.set_session_timeout_seconds(75)

    controller = MeasurementController(cfg.measurement, email_system=None, camera=None)
    try:
        controller.start_session()
        status = controller.get_session_status()

        assert status["is_active"] is True
        assert status["session_timeout_seconds"] == 75
        assert status["session_timeout_minutes"] == 2
        assert status["session_start_time"] is not None
    finally:
        controller.cleanup()


def test_measurement_status_uses_single_config_snapshot(monkeypatch) -> None:
    cfg = _create_default_config()
    old_config = cfg.measurement
    old_config.session_timeout_seconds = 75
    old_config.session_timeout_minutes = 0
    old_config.alert_delay_seconds = 120

    replacement_cfg = _create_default_config()
    replacement_config = replacement_cfg.measurement
    replacement_config.session_timeout_seconds = 0
    replacement_config.session_timeout_minutes = 10
    replacement_config.alert_delay_seconds = 5

    controller = MeasurementController(old_config, email_system=None, camera=None)
    try:
        controller.is_session_active = True
        controller.session_start_time = datetime.now() - timedelta(seconds=1)
        controller.last_motion_time = datetime.now()

        original_get_timeout = controller._get_session_timeout_seconds

        def _swap_config_mid_status(*, config=None):
            value = original_get_timeout(config=config)
            controller.update_config(replacement_config)
            return value

        monkeypatch.setattr(controller, "_get_session_timeout_seconds", _swap_config_mid_status)

        status = controller.get_session_status()

        assert status["session_timeout_seconds"] == 75
        assert status["session_timeout_minutes"] == 2
        assert status["alert_countdown"] is not None
        assert status["alert_countdown"] > 100
    finally:
        controller.cleanup()
