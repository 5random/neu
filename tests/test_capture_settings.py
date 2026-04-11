import logging
import threading
from types import SimpleNamespace

import cv2

from src.gui.settings_elements import camfeed_settings
from src.cam.camera import Camera
from src.config import _create_default_config
from src.gui.settings_elements.camfeed_settings import (
    _resolve_capture_dimensions,
    _start_settings_camfeed_refresh_timer,
)


class _DummyVideoCapture:
    def __init__(self, width: int, height: int, fps: float, *, opened: bool = True) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._opened = opened

    def isOpened(self) -> bool:
        return self._opened

    def get(self, prop: int) -> float:
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        if prop == cv2.CAP_PROP_FPS:
            return float(self._fps)
        return 0.0


def _make_camera_stub() -> Camera:
    cfg = _create_default_config()
    camera = Camera.__new__(Camera)
    camera.app_config = cfg
    camera.webcam_config = cfg.webcam
    camera.capture_lock = threading.RLock()
    camera._init_state_lock = threading.Lock()
    camera._cleanup_lock = threading.Lock()
    camera._cleanup_in_progress = False
    camera.cleaned = False
    camera.video_capture = None
    camera.backend = 0
    camera.frame_count = 0
    camera.is_running = False
    camera.motion_enabled = False
    camera._reconnect_attempts = 0
    camera.max_reconnect_attempts = 5
    camera._status_cache = {}
    camera._status_cache_time = 0.0
    camera.logger = logging.getLogger('tests.camera.profile')
    return camera


def test_get_configured_capture_profile_reads_static_webcam_config() -> None:
    camera = _make_camera_stub()

    profile = camera.get_configured_capture_profile()

    assert profile == {
        'camera_index': 0,
        'resolution': {'width': 1920, 'height': 1080},
        'fps': 30,
    }


def test_get_camera_status_exposes_configured_profile_when_camera_is_disconnected() -> None:
    camera = _make_camera_stub()

    status = camera.get_camera_status()

    assert status['connected'] is False
    assert status['resolution'] is None
    assert status['fps'] is None
    assert status['camera_index'] == 0
    assert status['configured_camera_index'] == 0
    assert status['configured_resolution'] == {'width': 1920, 'height': 1080}
    assert status['configured_fps'] == 30


def test_get_camera_status_reports_live_capture_values_when_available() -> None:
    camera = _make_camera_stub()
    camera.video_capture = _DummyVideoCapture(width=1280, height=720, fps=25.0)
    camera.is_running = True

    status = camera.get_camera_status()

    assert status['connected'] is True
    assert status['resolution'] == {'width': 1280, 'height': 720}
    assert status['fps'] == 25.0
    assert status['configured_resolution'] == {'width': 1920, 'height': 1080}
    assert status['configured_fps'] == 30


def test_resolve_capture_dimensions_falls_back_to_static_config() -> None:
    camera = _make_camera_stub()

    assert _resolve_capture_dimensions(camera) == (1920, 1080)


def test_resolve_capture_dimensions_prefers_live_capture_dimensions() -> None:
    camera = SimpleNamespace(
        get_configured_capture_profile=lambda: {
            'camera_index': 4,
            'resolution': {'width': 1920, 'height': 1080},
            'fps': 30,
        },
        get_camera_status=lambda: {
            'resolution': {'width': 640, 'height': 480},
        },
    )

    assert _resolve_capture_dimensions(camera) == (640, 480)


def test_resolve_capture_dimensions_uses_default_when_no_camera_is_available() -> None:
    assert _resolve_capture_dimensions(None) == (720, 405)


class _FakeTimer:
    def __init__(self, interval: float, callback) -> None:
        self.interval = interval
        self.callback = callback
        self.cancel_calls = 0

    @property
    def cancelled(self) -> bool:
        return self.cancel_calls > 0

    def cancel(self) -> None:
        self.cancel_calls += 1


class _FailingCancelTimer(_FakeTimer):
    def cancel(self) -> None:
        self.cancel_calls += 1
        raise RuntimeError('cancel failed')


class _FakeUI:
    def __init__(self, client) -> None:
        self.context = SimpleNamespace(client=client)
        self.created_timers: list[_FakeTimer] = []

    def timer(self, interval: float, callback):
        timer = _FakeTimer(interval, callback)
        self.created_timers.append(timer)
        return timer


class _DisconnectingClient:
    def __init__(self) -> None:
        self.disconnect_handlers: list[object] = []
        self.has_socket_connection = True

    def on_disconnect(self, handler) -> None:
        self.disconnect_handlers.append(handler)


def test_start_settings_camfeed_refresh_timer_replaces_previous_timer(monkeypatch) -> None:
    client = _DisconnectingClient()
    previous_timer = _FakeTimer(0.2, lambda: None)
    setattr(client, camfeed_settings._SETTINGS_CAMFEED_TIMER_ATTR, previous_timer)
    fake_ui = _FakeUI(client)

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)

    timer = _start_settings_camfeed_refresh_timer(lambda: None)

    assert previous_timer.cancelled is True
    assert len(fake_ui.created_timers) == 1
    assert timer is fake_ui.created_timers[0]
    assert timer.cancelled is False
    assert getattr(client, camfeed_settings._SETTINGS_CAMFEED_TIMER_ATTR) is timer
    assert len(client.disconnect_handlers) == 1


def test_start_settings_camfeed_refresh_timer_stops_on_next_tick_when_disconnect_registration_returns_false(monkeypatch) -> None:
    client = SimpleNamespace(has_socket_connection=True)
    fake_ui = _FakeUI(client)
    refresh_calls: list[str] = []

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)
    monkeypatch.setattr(
        camfeed_settings,
        'register_client_disconnect_handler',
        lambda *_args, **_kwargs: False,
    )

    timer = _start_settings_camfeed_refresh_timer(lambda: refresh_calls.append('tick'))

    assert len(fake_ui.created_timers) == 1
    assert timer is fake_ui.created_timers[0]
    assert timer.cancelled is False
    assert getattr(client, camfeed_settings._SETTINGS_CAMFEED_TIMER_ATTR) is timer

    timer.callback()

    assert refresh_calls == ['tick']
    assert timer.cancelled is False

    client.has_socket_connection = False
    timer.callback()

    assert refresh_calls == ['tick']
    assert timer.cancelled is True
    assert not hasattr(client, camfeed_settings._SETTINGS_CAMFEED_TIMER_ATTR)


def test_start_settings_camfeed_refresh_timer_runs_refresh_while_connected(monkeypatch) -> None:
    client = _DisconnectingClient()
    fake_ui = _FakeUI(client)
    refresh_calls: list[str] = []

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)

    timer = _start_settings_camfeed_refresh_timer(lambda: refresh_calls.append('tick'))

    timer.callback()

    assert refresh_calls == ['tick']
    assert timer.cancelled is False
    assert getattr(client, camfeed_settings._SETTINGS_CAMFEED_TIMER_ATTR) is timer


def test_start_settings_camfeed_refresh_timer_reuses_previous_timer_when_cancel_fails(monkeypatch) -> None:
    client = _DisconnectingClient()
    previous_timer = _FailingCancelTimer(0.2, lambda: None)
    setattr(client, camfeed_settings._SETTINGS_CAMFEED_TIMER_ATTR, previous_timer)
    fake_ui = _FakeUI(client)
    register_calls: list[object] = []

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)
    monkeypatch.setattr(
        camfeed_settings,
        'register_client_disconnect_handler',
        lambda *args, **kwargs: register_calls.append((args, kwargs)) or True,
    )

    timer = _start_settings_camfeed_refresh_timer(lambda: None)

    assert previous_timer.cancel_calls == 1
    assert len(fake_ui.created_timers) == 0
    assert timer is previous_timer
    assert getattr(client, camfeed_settings._SETTINGS_CAMFEED_TIMER_ATTR) is previous_timer
    assert register_calls == []


def test_start_settings_camfeed_refresh_timer_cancels_managed_timer_before_fallback_on_exception(monkeypatch) -> None:
    client = _DisconnectingClient()
    fake_ui = _FakeUI(client)

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)

    def _raise(*_args, **_kwargs) -> bool:
        raise RuntimeError('boom')

    monkeypatch.setattr(camfeed_settings, 'register_client_disconnect_handler', _raise)

    timer = _start_settings_camfeed_refresh_timer(lambda: None)

    assert len(fake_ui.created_timers) == 2
    managed_timer, fallback_timer = fake_ui.created_timers
    assert managed_timer.cancelled is True
    assert fallback_timer.cancelled is False
    assert timer is fallback_timer
    assert not hasattr(client, camfeed_settings._SETTINGS_CAMFEED_TIMER_ATTR)


def test_start_settings_camfeed_refresh_timer_uses_single_fallback_without_client_context(monkeypatch) -> None:
    fake_ui = _FakeUI(client=None)

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)

    timer = _start_settings_camfeed_refresh_timer(lambda: None)

    assert len(fake_ui.created_timers) == 1
    assert timer is fake_ui.created_timers[0]
    assert timer.cancelled is False
