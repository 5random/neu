from __future__ import annotations

import threading

from src.cam import camera as camera_module
from src.cam.camera import Camera
from src.config import _create_default_config


class _LoggerStub:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def warning(self, message: str, *args) -> None:
        self.warnings.append(message % args if args else message)

    def error(self, message: str, *args) -> None:
        self.errors.append(message % args if args else message)

    def debug(self, *_args, **_kwargs) -> None:
        return

    def info(self, *_args, **_kwargs) -> None:
        return


def _build_camera() -> tuple[Camera, object]:
    cfg = _create_default_config()
    camera = Camera.__new__(Camera)
    camera.app_config = cfg
    camera.uvc_config = cfg.uvc_controls
    camera.logger = _LoggerStub()
    camera._config_dirty = False
    camera._config_dirty_generation = 0
    camera._config_save_timer = None
    camera._timer_lock = threading.Lock()
    camera._invalidate_uvc_cache = lambda: None

    def _mark_dirty() -> None:
        with camera._timer_lock:
            camera._config_dirty = True
            camera._config_dirty_generation += 1

    camera._schedule_uvc_config_save = _mark_dirty
    return camera, cfg


def test_uvc_default_control_values_follow_camera_defaults():
    camera, _ = _build_camera()

    defaults = camera.get_uvc_default_control_values()
    ranges = camera.get_uvc_ranges()

    assert defaults["brightness"] == ranges["brightness"]["default"]
    assert defaults["contrast"] == ranges["contrast"]["default"]
    assert defaults["saturation"] == ranges["saturation"]["default"]
    assert defaults["gamma"] == ranges["gamma"]["default"]
    assert defaults["white_balance_manual"] == ranges["white_balance"]["default"]
    assert defaults["exposure_manual"] == ranges["exposure"]["default"]
    assert defaults["white_balance_auto"] is True
    assert defaults["exposure_auto"] is True


def test_reset_uvc_to_defaults_updates_config_even_with_partial_driver_failures():
    camera, cfg = _build_camera()
    calls: list[tuple[str, int | bool]] = []

    cfg.uvc_controls.brightness = 12
    cfg.uvc_controls.saturation = 31
    cfg.uvc_controls.white_balance.auto = False
    cfg.uvc_controls.white_balance.value = 5200
    cfg.uvc_controls.exposure.auto = False
    cfg.uvc_controls.exposure.value = -10

    def _setter(name: str, result: bool = True):
        def _apply(value: int | bool) -> bool:
            calls.append((name, value))
            return result

        return _apply

    camera.set_brightness = _setter("brightness")
    camera.set_contrast = _setter("contrast")
    camera.set_saturation = _setter("saturation")
    camera.set_sharpness = _setter("sharpness")
    camera.set_gamma = _setter("gamma")
    camera.set_gain = _setter("gain")
    camera.set_backlight_compensation = _setter("backlight_compensation")
    camera.set_hue = _setter("hue", result=False)
    camera.set_manual_white_balance = _setter("white_balance.value", result=False)
    camera.set_auto_white_balance = _setter("white_balance.auto")
    camera.set_manual_exposure = _setter("exposure.value", result=False)
    camera.set_auto_exposure = _setter("exposure.auto", result=False)

    assert camera.reset_uvc_to_defaults() is True

    assert ("brightness", 0) in calls
    assert ("saturation", 64) in calls
    assert ("white_balance.auto", True) in calls
    assert ("exposure.auto", True) in calls
    assert ("white_balance.value", 4600) not in calls
    assert ("exposure.value", -6) not in calls

    assert cfg.uvc_controls.brightness == 0
    assert cfg.uvc_controls.contrast == 16
    assert cfg.uvc_controls.saturation == 64
    assert cfg.uvc_controls.hue == 0
    assert cfg.uvc_controls.gain == 10
    assert cfg.uvc_controls.sharpness == 2
    assert cfg.uvc_controls.gamma == 164
    assert cfg.uvc_controls.backlight_compensation == 42
    assert cfg.uvc_controls.white_balance.auto is True
    assert cfg.uvc_controls.white_balance.value == 4600
    assert cfg.uvc_controls.exposure.auto is True
    assert cfg.uvc_controls.exposure.value == -6
    assert camera._config_dirty is True
    assert any("hue" in warning for warning in camera.logger.warnings)
    assert any("exposure.auto" in warning for warning in camera.logger.warnings)
    assert not any("white_balance.value" in warning for warning in camera.logger.warnings)
    assert not any("exposure.value" in warning for warning in camera.logger.warnings)


def test_reset_uvc_to_defaults_applies_manual_values_when_defaults_disable_auto():
    camera, cfg = _build_camera()
    calls: list[tuple[str, int | bool]] = []

    camera.UVC_DEFAULTS = {
        **camera.UVC_DEFAULTS,
        "white_balance": {"auto": False, "value": 5100},
        "exposure": {"auto": False, "value": -9},
    }

    def _setter(name: str, result: bool = True):
        def _apply(value: int | bool) -> bool:
            calls.append((name, value))
            return result

        return _apply

    camera.set_brightness = _setter("brightness")
    camera.set_contrast = _setter("contrast")
    camera.set_saturation = _setter("saturation")
    camera.set_sharpness = _setter("sharpness")
    camera.set_gamma = _setter("gamma")
    camera.set_gain = _setter("gain")
    camera.set_backlight_compensation = _setter("backlight_compensation")
    camera.set_hue = _setter("hue")
    camera.set_manual_white_balance = _setter("white_balance.value")
    camera.set_auto_white_balance = _setter("white_balance.auto", result=False)
    camera.set_manual_exposure = _setter("exposure.value")
    camera.set_auto_exposure = _setter("exposure.auto", result=False)

    assert camera.reset_uvc_to_defaults() is True

    assert ("white_balance.value", 5100) in calls
    assert ("exposure.value", -9) in calls
    assert ("white_balance.auto", False) not in calls
    assert ("exposure.auto", False) not in calls
    assert cfg.uvc_controls.white_balance.auto is False
    assert cfg.uvc_controls.white_balance.value == 5100
    assert cfg.uvc_controls.exposure.auto is False
    assert cfg.uvc_controls.exposure.value == -9


def test_save_uvc_config_clears_dirty_flag_after_success(monkeypatch):
    camera, _ = _build_camera()
    camera._config_dirty = True
    camera._config_dirty_generation = 1

    monkeypatch.setattr(camera_module, "save_config", lambda *_args, **_kwargs: None)

    assert camera.save_uvc_config(path="dummy.yaml") is True
    assert camera._config_dirty is False


def test_save_uvc_config_preserves_dirty_flag_when_new_change_arrives_during_save(monkeypatch):
    camera, _ = _build_camera()
    camera._config_dirty = True
    camera._config_dirty_generation = 1

    def _save_with_interleaved_change(*_args, **_kwargs):
        camera._schedule_uvc_config_save()

    monkeypatch.setattr(camera_module, "save_config", _save_with_interleaved_change)

    assert camera.save_uvc_config(path="dummy.yaml") is True
    assert camera._config_dirty is True
    assert camera._config_dirty_generation == 2
