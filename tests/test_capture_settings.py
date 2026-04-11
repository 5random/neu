import logging
import inspect
import threading
from types import SimpleNamespace

import cv2

from src.cam.camera import Camera
from src.config import _create_default_config, load_config
from src.gui.settings_elements import camfeed_settings
from src.gui.settings_elements.camfeed_settings import (
    _build_settings_camfeed_refresh_script,
    _calculate_preview_dimensions,
    _capture_to_preview_coords,
    _create_settings_camfeed_image,
    _preview_to_capture_coords,
    _resolve_capture_dimensions,
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


class _DummyInteractiveImage:
    def __init__(self, args: tuple[object, ...], kwargs: dict[str, object]) -> None:
        self.args = args
        self.kwargs = kwargs
        self.source = args[0] if args else None
        self.style_calls: list[str] = []

    def style(self, *args, **_kwargs) -> "_DummyInteractiveImage":
        if args:
            self.style_calls.append(str(args[0]))
        return self

    def classes(self, *_args, **_kwargs) -> "_DummyInteractiveImage":
        return self

    def props(self, *_args, **_kwargs) -> "_DummyInteractiveImage":
        return self

    def tooltip(self, *_args, **_kwargs) -> "_DummyInteractiveImage":
        return self

    def on(self, *_args, **_kwargs) -> "_DummyInteractiveImage":
        return self

    def enable(self) -> "_DummyInteractiveImage":
        return self

    def disable(self) -> "_DummyInteractiveImage":
        return self

    def set_value(self, value: object) -> "_DummyInteractiveImage":
        self.value = value
        return self

    def set_content(self, value: str) -> "_DummyInteractiveImage":
        self.content = value
        return self


class _DummyUIElement(_DummyInteractiveImage):
    def __init__(
        self,
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        *,
        value: object = None,
        text: str = '',
    ) -> None:
        super().__init__(args, kwargs or {})
        self.value = value
        self.text = text

    def __enter__(self) -> "_DummyUIElement":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePassiveGameLayer:
    def __init__(self, fragments: list[str] | None = None) -> None:
        self.fragments = fragments or []
        self.render_calls: list[tuple[int, int]] = []

    def render_svg_fragments(self, preview_width: int, preview_height: int) -> list[str]:
        self.render_calls.append((preview_width, preview_height))
        return list(self.fragments)


class _FakeSettingsUI:
    def __init__(self) -> None:
        self.label_texts: list[str] = []
        self.number_labels: list[str] = []
        self.checkbox_labels: list[str] = []
        self.body_html_calls: list[str] = []
        self.notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.interactive_image_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.last_interactive_image: _DummyInteractiveImage | None = None

    def card(self) -> _DummyUIElement:
        return _DummyUIElement()

    def row(self) -> _DummyUIElement:
        return _DummyUIElement()

    def column(self) -> _DummyUIElement:
        return _DummyUIElement()

    def element(self, *_args, **_kwargs) -> _DummyUIElement:
        return _DummyUIElement()

    def label(self, text: str) -> _DummyUIElement:
        self.label_texts.append(text)
        return _DummyUIElement(text=text)

    def number(self, *, label: str, value=None, **_kwargs) -> _DummyUIElement:
        self.number_labels.append(label)
        return _DummyUIElement(value=value, text=label)

    def checkbox(self, text: str, value=False) -> _DummyUIElement:
        self.checkbox_labels.append(text)
        return _DummyUIElement(value=value, text=text)

    def button(self, *args, **kwargs) -> _DummyUIElement:
        label = ''
        if args and isinstance(args[0], str):
            label = args[0]
        elif isinstance(kwargs.get('label'), str):
            label = str(kwargs['label'])
        return _DummyUIElement(text=label)

    def separator(self) -> _DummyUIElement:
        return _DummyUIElement()

    def space(self) -> _DummyUIElement:
        return _DummyUIElement()

    def interactive_image(self, *args, **kwargs) -> _DummyInteractiveImage:
        self.interactive_image_calls.append((args, kwargs))
        self.last_interactive_image = _DummyInteractiveImage(args, kwargs)
        return self.last_interactive_image

    def add_body_html(self, html: str) -> None:
        self.body_html_calls.append(html)

    def notify(self, *args, **kwargs) -> None:
        self.notifications.append((args, kwargs))


def test_get_configured_capture_profile_reads_static_webcam_config() -> None:
    camera = _make_camera_stub()

    profile = camera.get_configured_capture_profile()

    assert profile == {
        'camera_index': 0,
        'resolution': {'width': 1280, 'height': 720},
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
    assert status['configured_resolution'] == {'width': 1280, 'height': 720}
    assert status['configured_fps'] == 30
    assert status['preview_resolution'] is None
    assert status['preview_active_consumers'] == 0


def test_get_camera_status_reports_live_capture_values_when_available() -> None:
    camera = _make_camera_stub()
    camera.video_capture = _DummyVideoCapture(width=1280, height=720, fps=25.0)
    camera.is_running = True

    status = camera.get_camera_status()

    assert status['connected'] is True
    assert status['resolution'] == {'width': 1280, 'height': 720}
    assert status['fps'] == 25.0
    assert status['configured_resolution'] == {'width': 1280, 'height': 720}
    assert status['configured_fps'] == 30


def test_get_camera_status_handles_partial_camera_stub_without_preview_locks() -> None:
    camera = _make_camera_stub()

    status = camera.get_camera_status()

    assert status['preview_resolution'] is None
    assert status['preview_active_consumers'] == 0


def test_resolve_capture_dimensions_falls_back_to_static_config() -> None:
    camera = _make_camera_stub()

    assert _resolve_capture_dimensions(camera) == (1280, 720)


def test_resolve_capture_dimensions_prefers_live_capture_dimensions() -> None:
    camera = SimpleNamespace(
        get_configured_capture_profile=lambda: {
            'camera_index': 4,
            'resolution': {'width': 1280, 'height': 720},
            'fps': 30,
        },
        get_camera_status=lambda: {
            'resolution': {'width': 640, 'height': 480},
        },
    )

    assert _resolve_capture_dimensions(camera) == (640, 480)


def test_resolve_capture_dimensions_uses_default_when_no_camera_is_available() -> None:
    assert _resolve_capture_dimensions(None) == (720, 405)


def test_default_webcam_config_exposes_preview_performance_defaults() -> None:
    default_cfg = _create_default_config()

    assert default_cfg.webcam.fps == 30
    assert default_cfg.webcam.preview_fps == 15
    assert default_cfg.webcam.preview_max_width == 1280
    assert default_cfg.webcam.preview_jpeg_quality == 75


def test_checked_in_config_matches_default_stream_profile() -> None:
    default_cfg = _create_default_config()
    loaded_cfg = load_config("config/config.yaml")

    assert loaded_cfg.webcam.default_resolution == default_cfg.webcam.default_resolution
    assert loaded_cfg.webcam.fps == default_cfg.webcam.fps
    assert loaded_cfg.webcam.preview_fps == default_cfg.webcam.preview_fps
    assert loaded_cfg.webcam.preview_max_width == default_cfg.webcam.preview_max_width
    assert loaded_cfg.webcam.preview_jpeg_quality == default_cfg.webcam.preview_jpeg_quality


def test_calculate_preview_dimensions_downscales_to_budgeted_width() -> None:
    assert _calculate_preview_dimensions(1280, 720, 960) == (960, 540)
    assert _calculate_preview_dimensions(1280, 720, 1600) == (1280, 720)


def test_preview_mapping_round_trips_consistently() -> None:
    preview_width, preview_height = _calculate_preview_dimensions(1280, 720, 960)

    preview_coords = _capture_to_preview_coords(
        640,
        360,
        capture_width=1280,
        capture_height=720,
        preview_width=preview_width,
        preview_height=preview_height,
    )

    assert preview_coords == (480, 270)
    assert _preview_to_capture_coords(
        *preview_coords,
        capture_width=1280,
        capture_height=720,
        preview_width=preview_width,
        preview_height=preview_height,
    ) == (640, 360)


def test_preview_mapping_stays_consistent_after_preview_width_change() -> None:
    old_preview = _calculate_preview_dimensions(1280, 720, 960)
    new_preview = _calculate_preview_dimensions(1280, 720, 640)

    old_coords = _capture_to_preview_coords(
        640,
        360,
        capture_width=1280,
        capture_height=720,
        preview_width=old_preview[0],
        preview_height=old_preview[1],
    )
    new_coords = _capture_to_preview_coords(
        640,
        360,
        capture_width=1280,
        capture_height=720,
        preview_width=new_preview[0],
        preview_height=new_preview[1],
    )

    assert old_coords == (480, 270)
    assert new_coords == (320, 180)
    assert _preview_to_capture_coords(
        *old_coords,
        capture_width=1280,
        capture_height=720,
        preview_width=old_preview[0],
        preview_height=old_preview[1],
    ) == (640, 360)
    assert _preview_to_capture_coords(
        *new_coords,
        capture_width=1280,
        capture_height=720,
        preview_width=new_preview[0],
        preview_height=new_preview[1],
    ) == (640, 360)


def test_camera_settings_feed_does_not_render_performance_controls(monkeypatch) -> None:
    fake_ui = _FakeSettingsUI()
    headings: list[str] = []
    passive_layer_calls: list[dict[str, object]] = []
    camera = _make_camera_stub()
    camera.motion_detector = SimpleNamespace(
        roi=SimpleNamespace(enabled=False, x=0, y=0, width=0, height=0),
        reset_background_model=lambda: None,
    )

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)
    monkeypatch.setattr(camfeed_settings, 'create_heading_row', lambda title, **_kwargs: headings.append(title))
    monkeypatch.setattr(
        camfeed_settings,
        'create_action_button',
        lambda *_args, **_kwargs: _DummyUIElement(),
    )
    monkeypatch.setattr(
        camfeed_settings,
        'create_passive_game_layer',
        lambda **kwargs: passive_layer_calls.append(kwargs) or _FakePassiveGameLayer(),
    )

    camfeed_settings.create_camfeed_content(camera=camera)

    assert 'Live Camera Feed & ROI' in headings
    assert 'Performance' not in headings
    assert fake_ui.number_labels == ['x', 'y', 'w', 'h']
    assert 'ROI enabled' in fake_ui.checkbox_labels
    assert 'Preview FPS' not in fake_ui.number_labels
    assert 'Preview Width' not in fake_ui.number_labels
    assert 'JPEG Quality' not in fake_ui.number_labels
    assert 'Motion Frame Skip' not in fake_ui.number_labels
    assert 'Processing Width' not in fake_ui.number_labels
    assert 'Status Refresh' not in fake_ui.number_labels
    assert 'Connecting camera...' in fake_ui.label_texts
    assert passive_layer_calls == [
        {
            'stream_host_id': camfeed_settings._SETTINGS_CAMFEED_ID,
            'on_change': passive_layer_calls[0]['on_change'],
        }
    ]


def test_create_settings_camfeed_image_uses_stream_source(monkeypatch) -> None:
    fake_ui = _FakeSettingsUI()

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)

    image = _create_settings_camfeed_image(lambda _event: None, preview_width=1280, preview_height=720)

    assert fake_ui.interactive_image_calls[0][0] == ('/video_feed',)
    assert fake_ui.interactive_image_calls[0][1]['events'] == ['click', 'move', 'mouseleave']
    assert fake_ui.interactive_image_calls[0][1]['cross'] == '#19bfd2'
    assert image.source == '/video_feed'
    assert any('aspect-ratio:1280/720' in style for style in image.style_calls)
    assert any('min-height:220px' in style for style in image.style_calls)


def test_settings_camfeed_refresh_script_only_starts_stream_when_page_is_visible() -> None:
    script = _build_settings_camfeed_refresh_script()

    assert "/video_feed?ts=" not in script
    assert "var url = '/video_feed';" in script
    assert "if (!force && currentSrc === url)" in script
    assert script.count("scheduleReconnect();") >= 2
    assert "start(true);" in script
    assert "document.visibilityState !== 'visible'" in script
    assert "window.__cvdSettingsCamState" in script
    assert "addEventListener('load'" in script
    assert "addEventListener('error'" in script
    assert "emitEvent('cvd_gol_stream_phase'" in script
    assert "dataset.conwayReady" in script


def test_settings_camfeed_delegates_passive_game_overlay_to_shared_easter_egg_layer() -> None:
    source = inspect.getsource(camfeed_settings.create_camfeed_content)

    assert "create_passive_game_layer(" in source
    assert "stream_host_id=_SETTINGS_CAMFEED_ID" in source
    assert "on_change=update_overlay" in source


def test_settings_overlay_composes_passive_game_layer_before_roi_annotations(monkeypatch) -> None:
    fake_ui = _FakeSettingsUI()
    camera = _make_camera_stub()
    camera.motion_detector = SimpleNamespace(
        roi=SimpleNamespace(enabled=True, x=100, y=120, width=200, height=160),
        reset_background_model=lambda: None,
    )
    passive_layer = _FakePassiveGameLayer(['<rect id="gol-fragment" />'])

    monkeypatch.setattr(camfeed_settings, 'ui', fake_ui)
    monkeypatch.setattr(camfeed_settings, 'create_heading_row', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        camfeed_settings,
        'create_action_button',
        lambda *_args, **_kwargs: _DummyUIElement(),
    )
    monkeypatch.setattr(
        camfeed_settings,
        'create_passive_game_layer',
        lambda **_kwargs: passive_layer,
    )

    camfeed_settings.create_camfeed_content(camera=camera)

    assert fake_ui.last_interactive_image is not None
    overlay = getattr(fake_ui.last_interactive_image, 'content', '')
    assert 'id="gol-fragment"' in overlay
    assert 'stroke="#19bfd2"' in overlay
    assert overlay.index('id="gol-fragment"') < overlay.index('stroke="#19bfd2"')
