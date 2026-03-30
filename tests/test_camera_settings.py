from unittest.mock import Mock

from src.gui.settings_elements import camera_settings


def test_handle_config_save_error_reports_invalid_config_path(monkeypatch) -> None:
    notify_mock = Mock()
    error_mock = Mock()
    monkeypatch.setattr(camera_settings.ui, "notify", notify_mock)
    monkeypatch.setattr(camera_settings.logger, "error", error_mock)

    camera_settings._handle_config_save_error(
        "uvc_controls.brightness",
        AttributeError("Missing config field 'uvc_controls' under '_Config' while resolving 'uvc_controls.brightness'"),
    )

    error_message = error_mock.call_args.args[0]
    notify_message = notify_mock.call_args.args[0]

    assert "Invalid config path for uvc_controls.brightness" in error_message
    assert "uvc_controls.brightness" in notify_message


def test_handle_config_save_error_keeps_generic_failures_generic(monkeypatch) -> None:
    notify_mock = Mock()
    error_mock = Mock()
    monkeypatch.setattr(camera_settings.ui, "notify", notify_mock)
    monkeypatch.setattr(camera_settings.logger, "error", error_mock)

    camera_settings._handle_config_save_error("uvc_controls.brightness", RuntimeError("disk full"))

    assert error_mock.call_args.args[0] == "Error saving config for uvc_controls.brightness: disk full"
    assert notify_mock.call_args.args[0] == "Error saving uvc_controls.brightness: disk full"
