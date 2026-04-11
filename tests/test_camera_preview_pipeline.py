from __future__ import annotations

import numpy as np

from src.cam.camera import Camera
from src.cam.motion import MotionResult
from src.config import _create_default_config


def _build_camera() -> Camera:
    return Camera(_create_default_config(), initialize=False)


def test_preview_encoding_stays_idle_without_consumers() -> None:
    camera = _build_camera()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    try:
        camera._publish_current_frame(frame)
        preview_bytes = camera._maybe_publish_preview_frame(frame)

        assert preview_bytes is None
        assert camera.get_current_jpeg_frame() is None
        assert camera.get_preview_resolution() is None
    finally:
        camera.cleanup()


def test_preview_encoding_uses_shared_budget_and_preview_resolution() -> None:
    camera = _build_camera()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    try:
        camera.register_preview_consumer()
        camera._publish_current_frame(frame)
        preview_bytes = camera._maybe_publish_preview_frame(frame)

        assert preview_bytes is not None
        assert camera.get_current_jpeg_frame() == preview_bytes
        assert camera.get_preview_resolution() == {"width": 1280, "height": 720}
        assert camera.get_preview_consumer_count() == 1
    finally:
        camera.cleanup()


def test_preview_encoding_returns_cached_frame_while_publish_is_in_progress(monkeypatch) -> None:
    camera = _build_camera()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    try:
        camera._current_jpeg_frame = b'cached-preview'
        camera._preview_publish_in_progress = True
        def _unexpected_encode(_frame):
            raise RuntimeError('should not encode')

        monkeypatch.setattr(camera, '_encode_preview_frame', _unexpected_encode)

        preview_bytes = camera._maybe_publish_preview_frame(frame, force=True)

        assert preview_bytes == b'cached-preview'
    finally:
        camera.cleanup()


def test_preview_encoding_resets_in_progress_flag_after_failed_encode(monkeypatch) -> None:
    camera = _build_camera()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    try:
        camera.register_preview_consumer()
        camera._last_preview_publish_monotonic = 42.0
        monkeypatch.setattr(camera, '_encode_preview_frame', lambda _frame: (None, None))

        preview_bytes = camera._maybe_publish_preview_frame(frame)

        assert preview_bytes is None
        assert camera._preview_publish_in_progress is False
        assert camera._last_preview_publish_monotonic == 0.0
    finally:
        camera.cleanup()


def test_dispatch_motion_callbacks_supports_result_only_listeners() -> None:
    camera = _build_camera()
    results: list[MotionResult] = []
    result = MotionResult(
        motion_detected=True,
        contour_area=42.0,
        timestamp=123.0,
        roi_used=True,
    )

    try:
        camera.register_motion_result_callback(results.append)
        camera._dispatch_motion_callbacks(None, result)

        assert results == [result]
    finally:
        camera.cleanup()
