import logging
import threading
from types import SimpleNamespace

import cv2

from src.cam.camera import Camera
from src.config import _create_default_config
from src.gui.settings_elements.camfeed_settings import _resolve_capture_dimensions


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
