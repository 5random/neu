from src.gui.uvc_helpers import auto_exposure_value_is_auto, set_nested_config_value


def test_auto_exposure_value_is_auto_handles_windows_and_linux_driver_values():
    assert auto_exposure_value_is_auto(0.75) is True
    assert auto_exposure_value_is_auto(0.25) is False
    assert auto_exposure_value_is_auto(3) is True
    assert auto_exposure_value_is_auto(1) is False
    assert auto_exposure_value_is_auto(0) is False
    assert auto_exposure_value_is_auto(True) is True


class _Leaf:
    def __init__(self) -> None:
        self.brightness = 0


class _Config:
    def __init__(self) -> None:
        self.uvc_controls = _Leaf()


class _BrokenConfig:
    pass


def test_set_nested_config_value_updates_nested_field():
    config = _Config()

    set_nested_config_value(config, "uvc_controls.brightness", 42)

    assert config.uvc_controls.brightness == 42


def test_set_nested_config_value_raises_descriptive_error_for_missing_intermediate_field():
    config = _BrokenConfig()

    try:
        set_nested_config_value(config, "uvc_controls.brightness", 42)
    except AttributeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected AttributeError for missing intermediate config field")

    assert "uvc_controls" in message
    assert "uvc_controls.brightness" in message
