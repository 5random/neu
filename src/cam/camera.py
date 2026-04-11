from __future__ import annotations

import base64
import platform
import signal
import threading
import asyncio
import concurrent.futures
import collections
from contextlib import contextmanager
from functools import lru_cache
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Callable, Optional, Iterator, Dict, Any, Protocol

import cv2
import numpy as np
from fastapi import Response
from fastapi.responses import StreamingResponse
from nicegui import Client, app, core, run, ui
import logging

from src.config import get_global_config, get_logger, load_config, save_config, save_global_config
from .motion import MotionResult, MotionDetector

if TYPE_CHECKING:
    from src.config import AppConfig, WebcamConfig, UVCConfig


class CameraInitializationCancelled(RuntimeError):
    """Raised when camera initialization is cancelled before completion."""


_DEFAULT_VIDEO_PLACEHOLDER_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6QAA"
    "AANJREFUGFdjYGBg+A8AAQQBAHAgZQsAAAAASUVORK5CYII="
)
_DEFAULT_VIDEO_PLACEHOLDER_BODY = base64.b64decode(_DEFAULT_VIDEO_PLACEHOLDER_BASE64)
_VIDEO_ROUTE_LOCK = threading.Lock()
_VIDEO_ROUTES_REGISTERED = False
_VIDEO_ROUTE_APP: Any | None = None
_ACTIVE_VIDEO_CAMERA: "_ActiveVideoSource | None" = None
_VIDEO_ROUTE_LOGGER = logging.getLogger(__name__)


class _ActiveVideoSource(Protocol):
    def get_current_frame(self, copy_frame: bool = True) -> Optional[np.ndarray]:
        ...


def _get_video_route_logger(camera: "_ActiveVideoSource | None") -> logging.Logger:
    if camera is not None:
        candidate = getattr(camera, "logger", None)
        if isinstance(candidate, logging.Logger):
            return candidate
    return _VIDEO_ROUTE_LOGGER


def _get_video_placeholder_body(camera: "_ActiveVideoSource | None" = None) -> bytes:
    placeholder = getattr(camera, "placeholder", None)
    body = getattr(placeholder, "body", None)
    if isinstance(body, bytes):
        return body
    return _DEFAULT_VIDEO_PLACEHOLDER_BODY


def _get_cached_video_frame_bytes(camera: "_ActiveVideoSource | None") -> bytes | None:
    if camera is None:
        return None

    get_current_jpeg_frame = getattr(camera, "get_current_jpeg_frame", None)
    if not callable(get_current_jpeg_frame):
        return None

    try:
        cached_frame = get_current_jpeg_frame()
    except Exception as exc:
        _get_video_route_logger(camera).debug("Error reading cached jpeg frame: %s", exc, exc_info=True)
        return None

    if isinstance(cached_frame, (bytes, bytearray, memoryview)):
        return bytes(cached_frame)
    return None


def _get_active_video_camera() -> "_ActiveVideoSource | None":
    with _VIDEO_ROUTE_LOCK:
        return _ACTIVE_VIDEO_CAMERA


def _set_active_video_camera_locked(camera: "_ActiveVideoSource") -> None:
    """Set the active video source while the caller already holds _VIDEO_ROUTE_LOCK."""
    global _ACTIVE_VIDEO_CAMERA
    _ACTIVE_VIDEO_CAMERA = camera


def _clear_active_video_camera(camera: "_ActiveVideoSource | None" = None, *, force: bool = False) -> None:
    global _ACTIVE_VIDEO_CAMERA
    with _VIDEO_ROUTE_LOCK:
        if force or _ACTIVE_VIDEO_CAMERA is camera:
            _ACTIVE_VIDEO_CAMERA = None


def _video_routes_exist_in_app(target_app: Any | None = None) -> bool:
    route_app = app if target_app is None else target_app
    try:
        routes = list(getattr(route_app, "routes", []))
    except Exception:
        return False

    registered_paths = {getattr(route, "path", None) for route in routes}
    return "/video_feed" in registered_paths and "/video/frame" in registered_paths


def _build_video_frame_response(camera: "_ActiveVideoSource | None") -> Response:
    if camera is None:
        return Response(content=_get_video_placeholder_body(), media_type="image/png")

    cached_frame = _get_cached_video_frame_bytes(camera)
    if cached_frame is not None:
        return Response(content=cached_frame, media_type="image/jpeg")

    try:
        frame = camera.get_current_frame(copy_frame=False)
    except Exception as exc:
        _get_video_route_logger(camera).error("Error reading current frame: %s", exc)
        return Response(content=_get_video_placeholder_body(camera), media_type="image/png")

    if frame is None:
        return Response(content=_get_video_placeholder_body(camera), media_type="image/png")

    try:
        ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ret:
            return Response(content=_get_video_placeholder_body(camera), media_type="image/png")
        return Response(content=buffer.tobytes(), media_type="image/jpeg")
    except Exception as exc:
        _get_video_route_logger(camera).error("Error encoding frame: %s", exc)
        return Response(content=_get_video_placeholder_body(camera), media_type="image/png")


def _build_video_stream_chunk(camera: "_ActiveVideoSource | None") -> bytes:
    response = _build_video_frame_response(camera)
    media_type = response.media_type or "image/png"
    return (
        b"--frame\r\nContent-Type: "
        + media_type.encode("ascii", errors="ignore")
        + b"\r\n\r\n"
        + bytes(response.body)
        + b"\r\n"
    )


def _generate_active_video_frames() -> Iterator[bytes]:
    try:
        while True:
            current_camera = _get_active_video_camera()
            yield _build_video_stream_chunk(current_camera)
            time.sleep(0.03 if current_camera is not None else 0.1)
    except GeneratorExit:
        _get_video_route_logger(_get_active_video_camera()).debug("Video stream generator closed")
        raise


def _ensure_video_routes_registered_locked(route_app: Any) -> None:
    global _VIDEO_ROUTES_REGISTERED, _VIDEO_ROUTE_APP
    if _VIDEO_ROUTE_APP is route_app and _video_routes_exist_in_app(route_app):
        _VIDEO_ROUTES_REGISTERED = True
        return
    if _video_routes_exist_in_app(route_app):
        _VIDEO_ROUTE_APP = route_app
        _VIDEO_ROUTES_REGISTERED = True
        return

    @route_app.get("/video_feed")
    def video_feed() -> StreamingResponse:
        return StreamingResponse(
            _generate_active_video_frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @route_app.get("/video/frame")
    def video_frame() -> Response:
        return _build_video_frame_response(_get_active_video_camera())

    _VIDEO_ROUTE_APP = route_app
    _VIDEO_ROUTES_REGISTERED = True


def _ensure_video_routes_registered(target_app: Any | None = None) -> None:
    route_app = app if target_app is None else target_app
    with _VIDEO_ROUTE_LOCK:
        _ensure_video_routes_registered_locked(route_app)


def _activate_video_camera(camera: "_ActiveVideoSource", target_app: Any | None = None) -> None:
    route_app = app if target_app is None else target_app
    with _VIDEO_ROUTE_LOCK:
        _ensure_video_routes_registered_locked(route_app)
        _set_active_video_camera_locked(camera)


class Camera:
    """
    Kameraklasse mit vollständiger (und funktionierender) UVC‑Steuerung.
    
    Standardverhalten: Die Initialisierung erfolgt synchron waehrend der Instanziierung.
    Asynchrone Initialisierung ist nur noch ueber `async_init=True` verfuegbar und
    sollte nicht fuer startup-kritische Pfade verwendet werden.
    """
    
    # Konstanten für Kamera-Initialisierung (Issue #13)
    WARMUP_FRAMES = 30
    FRAME_WAIT_SECONDS = 0.03
    RECONNECT_INTERVAL_SECONDS = 5
    MAX_RECONNECT_ATTEMPTS = 5
    CAPTURE_READY_TIMEOUT_SECONDS = 2.0
    UVC_DEFAULTS: Dict[str, Any] = {
        "brightness": 0,
        "contrast": 16,
        "saturation": 64,
        "hue": 0,
        "gain": 10,
        "sharpness": 2,
        "gamma": 164,
        "backlight_compensation": 42,
        "white_balance": {"auto": True, "value": 4600},
        "exposure": {"auto": True, "value": -6},
    }

    # ------------------------- Initialisierung ------------------------- #

    def __init__(
        self,
        config: "AppConfig",
        logger: Optional[logging.Logger] = None,
        *,
        async_init: bool = False,
        initialize: bool = True,
    ) -> None:
        # -- Config & Logger --
        self.app_config: "AppConfig" = config
        self.webcam_config: "WebcamConfig" = self.app_config.webcam
        self.uvc_config: "UVCConfig" = self.app_config.uvc_controls
        self.logger = logger or get_logger('camera')
        self.logger.info("Initializing Camera")

        # Measurement config for alerts
        self.measurement_config = self.app_config.measurement

        # Ensure the active alert persistence path exists.
        # Alert images are stored under measurement.history_path; image_save_path is legacy fallback only.
        if getattr(self.measurement_config, 'save_alert_images', False):
            self.measurement_config.ensure_save_path()

        # -- Interne State‑Variablen --
        self.video_capture: Optional[cv2.VideoCapture] = None
        self.current_frame: Optional[np.ndarray] = None
        self._current_jpeg_frame: Optional[bytes] = None
        self.frame_lock = threading.Lock()
        self.capture_lock = threading.RLock()  # Protects video_capture access
        self._init_complete = threading.Event()  # Signals when async init is done
        self._init_cancel = threading.Event()
        self._init_state_lock = threading.Lock()
        self._init_thread: Optional[threading.Thread] = None
        self.initialization_error: Exception | None = None
        self._initialization_succeeded = False
        self._init_terminal_failure = False
        self.is_running = False
        self.frame_thread: Optional[threading.Thread] = None  # Initialize before use
        self._capture_ready = threading.Event()
        self._capture_runtime_error: Exception | None = None
        self._motion_callbacks: Dict[
            Callable[[np.ndarray, MotionResult], None],
            Callable[[np.ndarray, MotionResult], None],
        ] = {}
        self._motion_callbacks_lock = threading.Lock()

        # -- Motion Detection --
        self.motion_detector: Optional[MotionDetector] = None
        self.motion_enabled = False
        self.frame_count = 0

        # Letztes Bewegungsergebnis für Metrics
        self.last_motion_result: Optional[MotionResult] = None

        # Reconnection settings
        self.reconnect_interval = self.RECONNECT_INTERVAL_SECONDS
        self.max_reconnect_attempts = self.MAX_RECONNECT_ATTEMPTS
        self._reconnect_attempts = 0
        
        # Performance optimization
        self.motion_skip_frames = 2  # Run motion detection only every 2nd frame
        
        # -- Platzhalterbild für fehlende Kamera --
        self._uvc_cache_time: float = 0.0

        self._max_pool_size = 3
        self._frame_pool: collections.deque = collections.deque(maxlen=self._max_pool_size)

        self.cleaned = False
        self._cleanup_lock = threading.Lock()
        self._cleanup_in_progress = False
        
        # Placeholder object with body attribute
        self.placeholder = SimpleNamespace(body=_DEFAULT_VIDEO_PLACEHOLDER_BODY)

        self._jpeg_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._config_dirty = False
        self._config_dirty_generation = 0
        self._config_save_timer: Optional[threading.Timer] = None
        self._timer_lock = threading.Lock()

        # -- Backend je nach Plattform explizit wählen --
        system = platform.system()
        if system == "Windows":
            self.logger.info("Using DirectShow backend for Windows")
            self.backend = cv2.CAP_DSHOW  # DirectShow - garantiert alle Regler
        elif system == "Linux":
            self.logger.info("Using Video4Linux2 backend for Linux")
            self.backend = cv2.CAP_V4L2   # Video4Linux2
        else:
            # macOS oder unbekannt → OpenCV entscheidet selbst
            self.backend = 0
            self.logger.warning("Unknown OS - use standard backend (can restrict controllers)")

        # -- Kamera initialisieren --
        if initialize:
            if async_init:
                self._start_async_init()
            else:
                self.initialize_sync()

    def _start_async_init(self) -> None:
        """Startet die Kamera-Initialisierung im Hintergrund."""
        self._init_cancel.clear()
        self._init_complete.clear()
        self._reset_initialization_state()
        self.logger.info("Starting async camera initialization...")
        self._init_thread = threading.Thread(target=self._init_worker, daemon=True)
        self._init_thread.start()

    def _get_init_state_lock(self) -> threading.Lock:
        return self._init_state_lock

    def _reset_initialization_state(self) -> None:
        with self._get_init_state_lock():
            self._init_terminal_failure = False
            self.initialization_error = None
            self._initialization_succeeded = False

    def _get_initialization_snapshot(self) -> tuple[Exception | None, bool, bool]:
        with self._get_init_state_lock():
            return (
                self.initialization_error,
                self._initialization_succeeded,
                self._init_terminal_failure,
            )

    def _request_init_cancel(self, reason: Exception | None = None) -> None:
        with self._get_init_state_lock():
            self._init_cancel.set()
            if reason is not None and (
                self.initialization_error is None or isinstance(reason, TimeoutError)
            ):
                self.initialization_error = reason
            self._initialization_succeeded = False

    def _join_init_thread(self, timeout: float) -> bool:
        if self._init_thread is None or not self._init_thread.is_alive():
            return True
        self._init_thread.join(timeout=timeout)
        return not self._init_thread.is_alive()

    def _check_init_cancelled(self) -> None:
        if self._init_cancel.is_set():
            raise CameraInitializationCancelled("Camera initialization was cancelled")

    def _mark_initialization_success(self) -> bool:
        late_success_reason: str | None = None
        with self._get_init_state_lock():
            terminal_failure = self._init_terminal_failure
            init_cancelled = self._init_cancel.is_set()
            if terminal_failure or init_cancelled:
                self._initialization_succeeded = False
                if init_cancelled and self.initialization_error is None:
                    self.initialization_error = CameraInitializationCancelled(
                        "Camera initialization was cancelled before completion"
                    )
                if terminal_failure and init_cancelled:
                    late_success_reason = "cancellation and terminal failure"
                elif init_cancelled:
                    late_success_reason = "cancellation"
                else:
                    late_success_reason = "terminal failure"
            else:
                self.initialization_error = None
                self._initialization_succeeded = True
        if late_success_reason is not None:
            self.logger.error(
                "Ignoring late camera init success after %s",
                late_success_reason,
            )
            try:
                self._cleanup_after_failed_initialization()
            except Exception:
                self.logger.debug(
                    "Failed to clean up late camera init success after %s",
                    late_success_reason,
                    exc_info=True,
                )
            self._init_complete.set()
            return False
        self._init_complete.set()
        return True

    def _mark_initialization_failure(self, exc: Exception) -> None:
        with self._get_init_state_lock():
            if self.initialization_error is None or not isinstance(exc, CameraInitializationCancelled):
                self.initialization_error = exc
            self._initialization_succeeded = False
        self._init_complete.set()

    def _mark_terminal_init_failure(self, message: str) -> RuntimeError:
        error = RuntimeError(message)
        with self._get_init_state_lock():
            self.initialization_error = error
            self._initialization_succeeded = False
            self._init_terminal_failure = True
        self._init_complete.set()
        return error

    def _is_camera_ready(self) -> bool:
        with self.capture_lock:
            return self.video_capture is not None and self.video_capture.isOpened()

    def _is_capture_thread_alive(self) -> bool:
        frame_thread = self.frame_thread
        return frame_thread is not None and frame_thread.is_alive()

    def _is_runtime_ready(self) -> bool:
        return (
            self._is_camera_ready()
            and self.is_running
            and self._capture_ready.is_set()
            and self._is_capture_thread_alive()
            and self._capture_runtime_error is None
        )

    def _wait_for_runtime_ready(self, timeout: float | None = None) -> None:
        ready_timeout = (
            self.CAPTURE_READY_TIMEOUT_SECONDS
            if timeout is None
            else max(0.0, float(timeout))
        )
        deadline = time.monotonic() + ready_timeout

        while True:
            self._check_init_cancelled()
            if self._is_runtime_ready():
                return

            capture_error = self._capture_runtime_error
            if capture_error is not None:
                raise RuntimeError(
                    "Camera frame capture stopped before the runtime became ready"
                ) from capture_error

            if self.frame_thread is not None and not self._is_capture_thread_alive():
                raise RuntimeError(
                    "Camera frame capture stopped before the first runtime frame was processed"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_frame_capture_and_wait(timeout=0.0)
                raise TimeoutError(
                    f"Camera frame capture did not become ready after {ready_timeout}s"
                )

            self._capture_ready.wait(timeout=min(0.05, remaining))

    def _cleanup_after_failed_initialization(self) -> None:
        frame_thread_stopped = True
        try:
            frame_thread_stopped = self._stop_frame_capture_and_wait(timeout=0.1)
            if not frame_thread_stopped:
                self.logger.warning(
                    "Frame capture thread did not stop during failed initialization cleanup"
                )
        except Exception:
            frame_thread_stopped = False
            self.logger.debug(
                "Failed to stop frame capture after initialization failure",
                exc_info=True,
            )

        self._capture_ready.clear()
        if frame_thread_stopped:
            if not self._try_release_video_capture(timeout=0.0):
                self.logger.warning(
                    "Camera cleanup after initialization failure remained partial because video capture could not be released"
                )
        else:
            self.logger.warning(
                "Skipping video capture release during failed initialization cleanup because frame capture thread is still alive"
            )
        self._clear_published_frame()

    def initialize_sync(self) -> bool:
        """Initialize the camera in the current thread and return readiness."""
        if self._init_thread is not None and self._init_thread.is_alive():
            return self.wait_for_init()
        if self._init_thread is not None and not self._init_thread.is_alive():
            self._init_thread = None
        if self._init_complete.is_set():
            _, initialization_succeeded, _ = self._get_initialization_snapshot()
            if initialization_succeeded and self._is_runtime_ready():
                return True
            self.logger.info("Camera runtime is not ready; restarting synchronous initialization")

        self._init_cancel.clear()
        self._init_complete.clear()
        self._reset_initialization_state()
        self._capture_runtime_error = None
        self._capture_ready.clear()
        self.logger.info("Starting synchronous camera initialization...")
        try:
            self._initialize_camera()
            self.start_frame_capture()
            self._wait_for_runtime_ready()
        except Exception as exc:
            self.logger.error("Synchronous camera initialization failed: %s", exc)
            self._cleanup_after_failed_initialization()
            self._mark_initialization_failure(exc)
            return False

        if not self._mark_initialization_success():
            self.logger.warning(
                "Synchronous camera initialization completed work but success commit was rejected"
            )
            return False

        self.logger.info("Synchronous camera initialization completed successfully")
        return True

    def _init_worker(self) -> None:
        """Worker-Thread für die Initialisierung."""
        try:
            self._initialize_camera()
            self.start_frame_capture()
            self._wait_for_runtime_ready()
            if self._mark_initialization_success():
                self.logger.info("Async camera initialization completed successfully")
            else:
                self.logger.warning(
                    "Async camera initialization completed work but success commit was rejected"
                )
        except CameraInitializationCancelled as exc:
            self.logger.info("Async camera initialization cancelled: %s", exc)
            self._cleanup_after_failed_initialization()
            self._mark_initialization_failure(exc)
        except Exception as exc:
            self.logger.error(f"Async camera initialization failed: {exc}")
            self._cleanup_after_failed_initialization()
            self._mark_initialization_failure(exc)

    def _initialize_camera(self) -> None:
        video_capture: Optional[cv2.VideoCapture] = None
        try:
            self._check_init_cancelled()
            self.logger.info(
                f"Open camera index {self.webcam_config.camera_index} with backend {self.backend}"
            )

            with self.capture_lock:
                if self.video_capture:
                    try:
                        self.video_capture.release()
                    except Exception as e:
                        pass
                    finally:
                        self.video_capture = None
                    
                video_capture = cv2.VideoCapture(
                    self.webcam_config.camera_index, self.backend
                )
                if video_capture:
                    if not video_capture.isOpened():
                        video_capture.release()
                        video_capture = None
                        # Windows: Fallback auf MSMF versuchen
                        if platform.system() == "Windows" and self.backend == cv2.CAP_DSHOW:
                            self.logger.warning("DirectShow failed; trying MSMF backend")
                            vc2 = cv2.VideoCapture(self.webcam_config.camera_index, cv2.CAP_MSMF)
                            if vc2 and vc2.isOpened():
                                video_capture = vc2
                            else:
                                if vc2:
                                    vc2.release()
                                raise RuntimeError(f"Camera {self.webcam_config.camera_index} could not be opened")
                    
                    if video_capture:
                        # Properties setzen
                        self._set_camera_properties(video_capture)
                        # Warmup
                        ok = False
                        for i in range(self.WARMUP_FRAMES):
                            self._check_init_cancelled()
                            ret, _ = video_capture.read()
                            if ret:
                                ok = True
                                break
                            time.sleep(self.FRAME_WAIT_SECONDS)
                        
                        if not ok:
                            # letzter Fallback: Default-Backend probieren
                            if platform.system() == "Windows" and self.backend != 0:
                                self.logger.warning("No frame after warmup; trying default backend")
                                try:
                                    tmp = cv2.VideoCapture(self.webcam_config.camera_index, 0)
                                    if tmp and tmp.isOpened():
                                        video_capture.release()
                                        video_capture = tmp
                                        self._set_camera_properties(video_capture)
                                        # noch einmal warmup
                                        for _ in range(self.WARMUP_FRAMES):
                                            self._check_init_cancelled()
                                            ret, _ = video_capture.read()
                                            if ret:
                                                ok = True
                                                break
                                            time.sleep(self.FRAME_WAIT_SECONDS)
                                except Exception:
                                    self.logger.exception("Default backend attempt failed")
                            if not ok:
                                if video_capture:
                                    video_capture.release()
                                raise RuntimeError("No frame received from camera during initialization (all backends)")

                self._check_init_cancelled()
                self.video_capture = video_capture

            self._check_init_cancelled()
            if self.video_capture:
                self._apply_uvc_controls()
                self.logger.info("Camera successfully initialized")

        except Exception as exc:
            self.logger.error(f"Initialization failed: {exc}")
            if video_capture is not None:
                try:
                    video_capture.release()
                except Exception as e:
                    self.logger.debug(f"Error during video_capture cleanup: {e}")

            with self.capture_lock:
                if self.video_capture is not None:
                    try:
                        self.video_capture.release()
                    except Exception as e:
                        self.logger.debug(f"Error during self.video_capture cleanup: {e}")
                    finally:
                        self.video_capture = None
            raise


    def wait_for_init(self, timeout: float = 10.0) -> bool:
        """
        Wartet bis die Kamera-Initialisierung abgeschlossen ist.
        
        Args:
            timeout: Maximale Gesamtwartezeit in Sekunden inklusive Join-Versuch
            
        Returns:
            True nur wenn die Kamera innerhalb des Timeouts betriebsbereit ist.
        """
        timeout_budget = max(0.0, float(timeout))
        deadline = time.monotonic() + timeout_budget
        result = self._init_complete.wait(timeout=timeout_budget)
        if not result:
            timeout_error = TimeoutError(f"Camera initialization timed out after {timeout_budget}s")
            self.logger.error("Camera initialization timeout after %ss", timeout_budget)
            self._request_init_cancel(reason=timeout_error)
            remaining_timeout = max(0.0, deadline - time.monotonic())
            if not self._join_init_thread(timeout=remaining_timeout):
                self.logger.error("Camera initialization thread did not stop after timeout")
                self._mark_terminal_init_failure("Camera initialization thread did not stop after timeout")
            return False
        initialization_error, initialization_succeeded, _ = self._get_initialization_snapshot()
        if not initialization_succeeded:
            if initialization_error is not None:
                self.logger.error("Camera initialization completed with error: %s", initialization_error)
            return False
        return self._is_runtime_ready()

    def _try_release_video_capture(self, *, timeout: float = 0.0) -> bool:
        acquired = self.capture_lock.acquire(timeout=timeout)
        if not acquired:
            self.logger.warning(
                "Skipping video capture release because capture_lock could not be acquired during cleanup"
            )
            return False
        try:
            if self.video_capture:
                try:
                    self.video_capture.release()
                except Exception as exc:
                    self.logger.error("Error releasing video capture: %s", exc)
                    return False
                finally:
                    self.video_capture = None
            return True
        finally:
            self.capture_lock.release()

    def _set_camera_properties(self, capture: cv2.VideoCapture) -> None:
        """Grundlegende Auflösung / FPS etc. setzen."""
        # Note: This is called with a local capture object during init, or needs lock if called on self.video_capture
        # Here we assume it's called on the local object during init, so no lock needed yet.
        # BUT _safe_set_for_capture is used which is generic.
        
        res = self.webcam_config.get_default_resolution()
        self._safe_set_for_capture(capture, cv2.CAP_PROP_FRAME_WIDTH, res.width)
        self._safe_set_for_capture(capture, cv2.CAP_PROP_FRAME_HEIGHT, res.height)
        self._safe_set_for_capture(capture, cv2.CAP_PROP_FPS, self.webcam_config.fps)
        self._safe_set_for_capture(capture, cv2.CAP_PROP_BUFFERSIZE, 1)

        self.logger.info(
            "current camera status: %dx%d @ %.1f FPS",
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            capture.get(cv2.CAP_PROP_FPS),
        )

    # --------------------- Low‑Level‑Hilfsfunktionen ------------------- #

    def _safe_set(self, prop: int, value: float) -> bool:
        """Setzt ein VideoCapture‑Property und prüft, ob es übernommen wurde."""
        with self.capture_lock:
            if not self.video_capture or not self.video_capture.isOpened():
                self.logger.error("safe_set: Camera not available")
                return False
            try:
                ok = self.video_capture.set(prop, value)
                actual = self.video_capture.get(prop)
                if not ok or abs(float(actual) - float(value)) > 1e-3:
                    self.logger.debug(f"Property {prop} to set to={value}, received={actual}; tolerance=1e-3")
                    return False
                return True
            except cv2.error as e:
                self.logger.error(f"OpenCV error setting property {prop}: {e}")
                return False        
            except Exception as e:
                self.logger.error(f"Unexpected error setting property {prop}: {e}")
                return False
    
    def _safe_set_for_capture(self, capture: cv2.VideoCapture, prop: int, value: float) -> bool:
        """Thread-sichere Property-Setter für spezifische VideoCapture"""
        try:
            ok = capture.set(prop, value)
            actual = capture.get(prop)
            if not ok or abs(float(actual) - float(value)) > 1e-3:
                self.logger.debug(f"Property {prop} to set to={value}, received={actual}")
                return False
            return True
        except Exception as e:
            self.logger.error(f"Error setting property {prop}: {e}")
            return False
    
    def _schedule_uvc_config_save(self) -> None:
        with self._timer_lock:
            self._config_dirty = True
            self._config_dirty_generation = getattr(self, '_config_dirty_generation', 0) + 1

            existing_timer = self._config_save_timer
            if existing_timer is not None:
                try:
                    existing_timer.cancel()
                except Exception:
                    pass

            self._config_save_timer = threading.Timer(5.0, self._auto_save_config)
            self._config_save_timer.start()

    def get_uvc_defaults(self) -> dict:
        defaults = self.UVC_DEFAULTS
        return {
            "brightness": int(defaults["brightness"]),
            "contrast": int(defaults["contrast"]),
            "saturation": int(defaults["saturation"]),
            "hue": int(defaults["hue"]),
            "gain": int(defaults["gain"]),
            "sharpness": int(defaults["sharpness"]),
            "gamma": int(defaults["gamma"]),
            "backlight_compensation": int(defaults["backlight_compensation"]),
            "white_balance": {
                "auto": bool(defaults["white_balance"]["auto"]),
                "value": int(defaults["white_balance"]["value"]),
            },
            "exposure": {
                "auto": bool(defaults["exposure"]["auto"]),
                "value": int(defaults["exposure"]["value"]),
            },
        }

    def get_uvc_default_control_values(self) -> dict:
        defaults = self.get_uvc_defaults()
        return {
            "brightness": defaults["brightness"],
            "contrast": defaults["contrast"],
            "saturation": defaults["saturation"],
            "sharpness": defaults["sharpness"],
            "gamma": defaults["gamma"],
            "gain": defaults["gain"],
            "backlight_compensation": defaults["backlight_compensation"],
            "hue": defaults["hue"],
            "white_balance_auto": defaults["white_balance"]["auto"],
            "white_balance_manual": defaults["white_balance"]["value"],
            "exposure_auto": defaults["exposure"]["auto"],
            "exposure_manual": defaults["exposure"]["value"],
        }

    def _sync_uvc_config_from_defaults(self, defaults: dict) -> None:
        self.uvc_config.brightness = int(defaults["brightness"])
        self.uvc_config.contrast = int(defaults["contrast"])
        self.uvc_config.saturation = int(defaults["saturation"])
        self.uvc_config.hue = int(defaults["hue"])
        self.uvc_config.gain = int(defaults["gain"])
        self.uvc_config.sharpness = int(defaults["sharpness"])
        self.uvc_config.gamma = int(defaults["gamma"])
        self.uvc_config.backlight_compensation = int(defaults["backlight_compensation"])
        self.uvc_config.white_balance.auto = bool(defaults["white_balance"]["auto"])
        self.uvc_config.white_balance.value = int(defaults["white_balance"]["value"])
        self.uvc_config.exposure.auto = bool(defaults["exposure"]["auto"])
        self.uvc_config.exposure.value = int(defaults["exposure"]["value"])

    def save_uvc_config(self, path: Optional[str] = None) -> bool:
        """Saves the current UVC settings back to the config file."""

        with self._timer_lock:
            if not getattr(self, '_config_dirty', True):
                self.logger.debug("UVC configuration not changed, skipping save")
                return True
            dirty_generation = getattr(self, '_config_dirty_generation', 0)

        try:
            global_cfg = get_global_config()
            if path is not None:
                save_config(self.app_config, path)
            elif global_cfg is self.app_config:
                if not save_global_config():
                    return False
            else:
                save_config(self.app_config)

            with self._timer_lock:
                if getattr(self, '_config_dirty_generation', dirty_generation) == dirty_generation:
                    self._config_dirty = False

            self.logger.info("UVC-Configuration saved!")
            return True
        except Exception as exc:
            self.logger.error(f"Error saving UVC configuration: {exc}")
            return False

    def _apply_uvc_controls(self) -> None:
        # Called inside init (with lock or local object) or needs lock
        # Since this calls _safe_set which uses lock, we need to be careful about reentrancy.
        # RLock handles reentrancy.
        
        # Hilfsfunktionen für Auto‑/Manuell‑Flags
        def _set_auto_exposure(auto: bool) -> None:
            if platform.system() == "Windows":
                value = 0.75 if auto else 0.25  # DirectShow‑Konvention
            else:  # Linux V4L2
                value = 3 if auto else 1       # V4L2_EXPOSURE_AUTO / MANUAL
            self._safe_set(cv2.CAP_PROP_AUTO_EXPOSURE, value)

        def _set_auto_wb(auto: bool) -> None:
            self._safe_set(cv2.CAP_PROP_AUTO_WB, 1 if auto else 0)

        # ----------------- Exposure -----------------
        if hasattr(self.uvc_config, "exposure") and self.uvc_config.exposure:
            _set_auto_exposure(self.uvc_config.exposure.auto)
            if not self.uvc_config.exposure.auto and self.uvc_config.exposure.value is not None:
                self._safe_set(cv2.CAP_PROP_EXPOSURE, int(self.uvc_config.exposure.value))

        # ----------------- White Balance -----------
        if hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            _set_auto_wb(self.uvc_config.white_balance.auto)
            if not self.uvc_config.white_balance.auto and self.uvc_config.white_balance.value is not None:
                self._safe_set(cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, int(self.uvc_config.white_balance.value))

        # --------- Weitere Standardregler ----------
        param_map = {
            "brightness": cv2.CAP_PROP_BRIGHTNESS,
            "contrast": cv2.CAP_PROP_CONTRAST,
            "saturation": cv2.CAP_PROP_SATURATION,
            "hue": cv2.CAP_PROP_HUE,
            "gain": cv2.CAP_PROP_GAIN,
            "sharpness": cv2.CAP_PROP_SHARPNESS,
            "gamma": cv2.CAP_PROP_GAMMA,
            "backlight_compensation": cv2.CAP_PROP_BACKLIGHT,
        }

        for name, prop in param_map.items():
            value = getattr(self.uvc_config, name, None)
            if value is not None:
                if not self._safe_set(prop, float(value)):
                    self.logger.debug(f"Setting of {name} ({value}) was ignored by the driver")

        self.logger.info("UVC controls applied")

    # ------------------ Öffentliche Setter‑Methoden ------------------- #

    # Allgemeiner Setter wird genutzt, damit GUI‑Slider etc. einfach callen können
    def _set_uvc_parameter(self, name: str, cv_prop: int, value: float) -> bool:
        """Setzt einen UVC-Parameter und aktualisiert die Konfiguration."""
        if not isinstance(value, (int, float)):
            self.logger.error(f"Invalid value type for {name}: {type(value)}")
            return False
        
        if not self._safe_set(cv_prop, value):
            self.logger.warning(f"{name} could not be set - driver ignores value {value}")
            return False
        
        try:
            setattr(self.uvc_config, name, value)  # nur RAM - Persistenz separat
            self._invalidate_uvc_cache()
            self._schedule_uvc_config_save()
            return True
        except Exception as e:
            self.logger.error(f"Error setting config for {name}: {e}")
            return False

    # Convenience‑Funktionen (können bei Bedarf erweitert werden)
    def set_brightness(self, value: float) -> bool:
        return self._set_uvc_parameter("brightness", cv2.CAP_PROP_BRIGHTNESS, value)

    def set_contrast(self, value: float) -> bool:
        return self._set_uvc_parameter("contrast", cv2.CAP_PROP_CONTRAST, value)

    def set_saturation(self, value: float) -> bool:
        return self._set_uvc_parameter("saturation", cv2.CAP_PROP_SATURATION, value)

    def set_exposure(self, value: float, auto: Optional[bool] = None) -> bool:
        # Auto-Exposure falls angegeben
        if auto is not None:
            if not self.set_auto_exposure(auto):
                return False
        # Manueller Exposure-Wert (auto=False oder auto=None)
        int_value = int(value)
        success = self._safe_set(cv2.CAP_PROP_EXPOSURE, int_value)
        if success and hasattr(self.uvc_config, "exposure") and self.uvc_config.exposure:
            self.uvc_config.exposure.value = int_value
            self.uvc_config.exposure.auto = False
            self._invalidate_uvc_cache()
            self._schedule_uvc_config_save()
        return success

    def set_hue(self, value: float) -> bool:
        return self._set_uvc_parameter("hue", cv2.CAP_PROP_HUE, value)

    def set_sharpness(self, value: float) -> bool:
        return self._set_uvc_parameter("sharpness", cv2.CAP_PROP_SHARPNESS, value)

    def set_gamma(self, value: float) -> bool:
        return self._set_uvc_parameter("gamma", cv2.CAP_PROP_GAMMA, value)

    def set_gain(self, value: float) -> bool:
        return self._set_uvc_parameter("gain", cv2.CAP_PROP_GAIN, value)

    def set_backlight_compensation(self, value: float) -> bool:
        return self._set_uvc_parameter("backlight_compensation", cv2.CAP_PROP_BACKLIGHT, value)

    def set_auto_exposure(self, auto: bool) -> bool:
        """Setzt Auto-Exposure ohne Wert zu ändern"""
        if platform.system() == "Windows":
            result = self._safe_set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if auto else 0.25)
        else:
            result = self._safe_set(cv2.CAP_PROP_AUTO_EXPOSURE, 3 if auto else 1)
        # Update der verschachtelten Konfiguration
        if result and hasattr(self.uvc_config, "exposure") and self.uvc_config.exposure:
            self.uvc_config.exposure.auto = auto
            self._invalidate_uvc_cache()
            self._schedule_uvc_config_save()
        return result

    def set_manual_exposure(self, value: float) -> bool:
        """Setzt manuellen Exposure-Wert und deaktiviert Auto-Exposure"""
        self.set_auto_exposure(False)
        return self.set_exposure(value, auto=False)

    def set_auto_white_balance(self, auto: bool) -> bool:
        """Setzt Auto-White-Balance ohne Wert zu ändern"""
        result = self._safe_set(cv2.CAP_PROP_AUTO_WB, 1 if auto else 0)
        # Update der verschachtelten Konfiguration
        if result and hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            self.uvc_config.white_balance.auto = auto
            self._invalidate_uvc_cache()
            self._schedule_uvc_config_save()
        return result

    def set_manual_white_balance(self, value: float) -> bool:
        """Setzt manuellen White-Balance-Wert und deaktiviert Auto-White-Balance"""
        self.set_auto_white_balance(False)
        # Manueller WB-Wert
        int_value = int(value)
        success = self._safe_set(cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, int_value)
        if success and hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            self.uvc_config.white_balance.auto = False
            self.uvc_config.white_balance.value = int_value
            self._invalidate_uvc_cache()
            self._schedule_uvc_config_save()
        return success

    def set_white_balance(self, value: float, auto: Optional[bool] = None) -> bool:
        """Setzt den White-Balance-Wert und/oder den Auto-Modus."""
        # Auto-White-Balance falls angegeben
        if auto is not None:
            if not self.set_auto_white_balance(auto):
                return False
        # Manueller WB-Wert (auto=False oder auto=None)
        int_value = int(value)
        success = self._safe_set(cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, int_value)
        if success and hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            self.uvc_config.white_balance.value = int_value
            self.uvc_config.white_balance.auto = False
            self._invalidate_uvc_cache()      
            self._schedule_uvc_config_save()
        return success
    
    def _auto_save_config(self) -> None:
        """Automatisches Speichern von Config nach Timeout"""
        with self._timer_lock:
            is_dirty = getattr(self, '_config_dirty', False)

        if not is_dirty:
            return

        if self.save_uvc_config():
            self.logger.debug('Config auto-saved after parameter changes')

    # ------------------ Laufende Bilderfassung ------------------------ #

    def start_frame_capture(self) -> None:
        if self.is_running and self._is_capture_thread_alive():
            return
        with self.capture_lock:
            if self.video_capture is None or not self.video_capture.isOpened():
                raise RuntimeError("Camera frame capture cannot start before initialization succeeds")
        self._capture_ready.clear()
        self._capture_runtime_error = None
        self.is_running = True
        self.frame_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.frame_thread.start()

    def _stop_frame_capture_and_wait(self, timeout: float = 2.0) -> bool:
        """Request frame capture stop and return whether the thread fully stopped."""
        self.is_running = False
        self._capture_ready.clear()
        frame_thread = self.frame_thread
        if frame_thread and frame_thread.is_alive():
            if threading.current_thread() is frame_thread:
                return False
            join = getattr(frame_thread, "join", None)
            if callable(join):
                join(timeout=max(0.0, float(timeout)))
        if self.frame_thread is not None and not self.frame_thread.is_alive():
            self.frame_thread = None
            return True
        return self.frame_thread is None

    def stop_frame_capture(self) -> None:
        """Stop the frame grabbing thread."""
        self._stop_frame_capture_and_wait(timeout=2.0)

    def _capture_loop(self) -> None:
        """Vereinfachte Capture-Loop ohne Retry-Logik"""
        consecutive_failures = 0
        max_consecutive_failures = 5
        capture_error: Exception | None = None
        current_thread = threading.current_thread()

        try:
            while self.is_running:
                with self.capture_lock:
                    video_capture_ref = self.video_capture
                    if not video_capture_ref or not video_capture_ref.isOpened():
                        video_capture_ref = None

                if video_capture_ref is None:
                    # Camera not ready yet or disconnected
                    time.sleep(0.1)
                    continue

                # Frame lesen ausserhalb des capture_lock, damit UVC-Operationen nicht blockieren
                try:
                    ret, frame = video_capture_ref.read()
                except cv2.error as e:
                    self.logger.error(f"OpenCV error reading frame: {e}")
                    ret, frame = False, None
                except Exception as e:
                    self.logger.error(f"Frame read error: {e}")
                    ret, frame = False, None

                if not ret or frame is None:
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        self.logger.debug(f'Framegrab failed {consecutive_failures} times in a row')
                        rec = self._handle_cam_disconnect()
                        if rec:
                            self.logger.info("Reconnection successful, resuming frame capture")
                            consecutive_failures = 0
                            continue
                        capture_error = RuntimeError(
                            "Camera frame capture stopped after repeated read and reconnect failures"
                        )
                        self.logger.error("Reconnection failed")
                        break
                    continue

                # Erfolgreicher Frame
                consecutive_failures = 0
                self._reconnect_attempts = 0

                frame_copy = frame.copy()

                self._publish_current_frame(frame_copy)

                self._capture_runtime_error = None
                self._capture_ready.set()

                # Motion Detection
                self._process_motion_detection(frame, frame_copy)
        except Exception as exc:
            capture_error = exc
            self.logger.error("Unhandled error in frame capture loop: %s", exc, exc_info=True)
        finally:
            if capture_error is not None:
                self._capture_runtime_error = capture_error
            self._capture_ready.clear()
            self.is_running = False
            self._clear_published_frame()
            if self.frame_thread is current_thread:
                self.frame_thread = None
            self.logger.info("Frame capture loop stopped")
    
    def _process_motion_detection(self, original_frame: np.ndarray, frame_copy: np.ndarray) -> None:
        # Frame skipping optimization
        if self.frame_count % self.motion_skip_frames != 0:
            return

        if self.motion_detector and self.motion_enabled:
            try:
                motion_result = self.motion_detector.detect_motion(original_frame)
                self.last_motion_result = motion_result
                self._dispatch_motion_callbacks(frame_copy, motion_result)
            except Exception as exc:
                self.logger.error(f"Motion-Detection-Error: {exc}")

        elif self._has_motion_callbacks():
            # Fallback: Alten Callback-Stil unterstützen für Rückwärtskompatibilität
            try:
                # Erstelle Dummy MotionResult für Kompatibilität und speichere
                dummy_result = MotionResult(
                    motion_detected=False,
                    contour_area=0.0,
                    timestamp=time.time(),
                    roi_used=False,
                )
                self.last_motion_result = dummy_result
                self._dispatch_motion_callbacks(original_frame, dummy_result)
                self.logger.debug("Motion callback called with dummy result")
            except Exception as exc:
                self.logger.error(f"Dummy-Motion-Callback-Error: {exc}")
    
    def _handle_cam_disconnect(self) -> bool:
        """Handle camera disconnection gracefully."""
        self.logger.warning("Camera disconnected, trying to reconnect...")
        
        if self._has_motion_callbacks():
            try:
                # Dummy-Frame für Callback
                dummy_frame = self.get_current_frame()
                if dummy_frame is None:
                    res = self.webcam_config.get_default_resolution()
                    dummy_frame = np.zeros((res.height, res.width, 3), dtype=np.uint8)
                
                # Disconnect-MotionResult erstellen
                disconnect_result = MotionResult(
                    motion_detected=False,
                    contour_area=0.0,
                    timestamp=time.time(),
                    roi_used=False,
                )
                
                # Callback aufrufen um GUI/MeasurementController zu benachrichtigen
                self._dispatch_motion_callbacks(dummy_frame, disconnect_result)
                self.logger.debug("Motion callback notified about disconnect")
            except Exception as exc:
                self.logger.error(f"Error notifying motion callback about disconnect: {exc}")

        with self.capture_lock:
            if self.video_capture and self.video_capture.isOpened():
                try:
                    self.video_capture.release()
                except cv2.error as e:
                    self.logger.error(f"OpenCV error releasing video capture: {e}")
                except Exception as e:
                    self.logger.error(f"Error releasing video capture: {e}")
                finally:
                    self.video_capture = None

        base_interval = self.reconnect_interval
        while self._reconnect_attempts < self.max_reconnect_attempts:
            self._reconnect_attempts += 1

            wait_time = base_interval * (2 ** (self._reconnect_attempts - 1))
            wait_time = min(wait_time, 60)
            self.logger.warning(f"Camera not reachable, retrying ({self._reconnect_attempts}/{self.max_reconnect_attempts})")

            time.sleep(wait_time)

            try:
                self._initialize_camera()
                # Check if init was successful
                with self.capture_lock:
                    if self.video_capture and self.video_capture.isOpened():
                        self.logger.info("Camera reconnected successfully")
                        self._reconnect_attempts = 0
                        return True
            except Exception as exc:
                self.logger.error(f"Reconnect attempt {self._reconnect_attempts} failed: {exc}")

        self.logger.error("Max. reconnect attempts reached")
        self.stop_frame_capture()
        return False


    # ---------------- Motion-Detection Steuerung --------------------- #
    def _has_motion_callbacks(self) -> bool:
        with self._motion_callbacks_lock:
            return bool(self._motion_callbacks)

    def _dispatch_motion_callbacks(self, frame: np.ndarray, motion_result: MotionResult) -> None:
        with self._motion_callbacks_lock:
            callbacks = list(self._motion_callbacks.values())

        for callback in callbacks:
            try:
                callback(frame, motion_result)
            except Exception as exc:
                self.logger.error(f"Motion callback dispatch error: {exc}")

    def enable_motion_detection(self, callback: Callable[[np.ndarray, MotionResult], None]) -> None:
        """Aktiviert die Bewegungserkennung und registriert einen Listener."""
        if not self.motion_detector:
            try:
                self.motion_detector = MotionDetector(self.app_config.motion_detection)
                self.logger.info("Motion detector initialized")
            except Exception as e:
                self.logger.error(f"Error initializing motion detector: {e}")
                return

        def safe_callback(frame: np.ndarray, motion_result: MotionResult) -> None:
            """Sicherer Callback, der sicherstellt, dass der Frame gültig ist."""
            if not isinstance(frame, np.ndarray):
                self.logger.error(f"Motion callback called with non-ndarray frame: {type(frame)}")
                return
            
            if frame.size == 0:
                self.logger.warning("Motion callback called with empty frame")
                return
            
            if not isinstance(motion_result, MotionResult):
                self.logger.error(f"Motion callback called with non-MotionResult: {type(motion_result)}")
                return
            
            try:
                callback(frame, motion_result)
            except Exception as exc:
                self.logger.error(f"Motion-Callback-Error: {exc}")

        with self._motion_callbacks_lock:
            self._motion_callbacks[callback] = safe_callback
            self.motion_enabled = bool(self._motion_callbacks)
            callback_count = len(self._motion_callbacks)

        self.logger.debug("Motion callback registered; total listeners=%s", callback_count)

    def disable_motion_detection(
        self,
        callback: Optional[Callable[[np.ndarray, MotionResult], None]] = None,
    ) -> None:
        """Deaktiviert die Bewegungserkennung oder entfernt einen einzelnen Listener."""
        with self._motion_callbacks_lock:
            if callback is None:
                self._motion_callbacks.clear()
            else:
                self._motion_callbacks.pop(callback, None)
            self.motion_enabled = bool(self._motion_callbacks)
            callback_count = len(self._motion_callbacks)

        self.logger.debug("Motion callback unregistered; total listeners=%s", callback_count)

    def is_motion_active(self) -> bool:
        """Gibt zurück, ob die Bewegungserkennung aktiv ist."""
        return self.motion_enabled

    def get_last_motion_result(self) -> Optional[MotionResult]:
        """Gibt das letzte Bewegungsergebnis zurück."""
        return self.last_motion_result

    def get_motion_metrics(self) -> dict:
        """Gibt Metriken zur Bewegungserkennung zurück."""
        return {
            "frame_count": self.frame_count,
            "last_timestamp": self.last_motion_result.timestamp if self.last_motion_result else None,
            "last_contour_area": self.last_motion_result.contour_area if self.last_motion_result else None,
            "roi_used": self.last_motion_result.roi_used if self.last_motion_result else None,
            "motion_enabled": self.motion_enabled
        }

    def is_camera_available(self) -> bool:
        """Returns True if the camera is connected and operational."""
        with self.capture_lock:
            return (
                self.video_capture is not None 
                and self.video_capture.isOpened() 
                and self.is_running
            )

    # ----------------- GUI-Integration Methoden ----------------------- #

    def get_configured_capture_profile(self) -> dict:
        """Return the statically configured webcam profile from the loaded config."""
        resolution = self.webcam_config.get_default_resolution()
        return {
            "camera_index": int(self.webcam_config.camera_index),
            "resolution": {
                "width": int(resolution.width),
                "height": int(resolution.height),
            },
            "fps": int(self.webcam_config.fps),
        }

    def get_camera_status(self) -> dict:
        """Gibt aktuellen Kamera-Status für GUI zurück"""
        current_time = time.time()
        # Cache für 200ms - GUI braucht nicht 60fps Status-Updates
        cache_time = getattr(self, '_status_cache_time', 0)
        if (current_time - cache_time) < 0.2:
            return getattr(self, '_status_cache', {})
        
        with self.capture_lock:
            video_capture_ref = self.video_capture
            is_connected = video_capture_ref is not None and video_capture_ref.isOpened()
            configured_profile = self.get_configured_capture_profile()

            base_status: Dict[str, Any] = {
                "connected": is_connected,
                "resolution": None,
                "fps": None,
                "camera_index": configured_profile["camera_index"],
                "configured_camera_index": configured_profile["camera_index"],
                "configured_resolution": dict(configured_profile["resolution"]),
                "configured_fps": configured_profile["fps"],
                "backend": self.backend,
                "frame_count": self.frame_count,
                "is_running": self.is_running,
                "motion_enabled": self.motion_enabled,
                "reconnect_attempts": self._reconnect_attempts,
                "error_status": self._reconnect_attempts >= self.max_reconnect_attempts
            }
            if is_connected and video_capture_ref is not None:
                try:
                    width = video_capture_ref.get(cv2.CAP_PROP_FRAME_WIDTH)
                    height = video_capture_ref.get(cv2.CAP_PROP_FRAME_HEIGHT)
                    fps = video_capture_ref.get(cv2.CAP_PROP_FPS)
                    base_status.update({
                        "resolution": {"width": int(width or 0), "height": int(height or 0)},
                        "fps": float(fps) if fps and fps > 0 else 0.0,
                        "uptime_frames": self.frame_count if self.is_running else 0,
                    })
                except Exception as e:
                    self.logger.debug(f"Error getting camera status: {e}")
                    base_status.update({"resolution": None, "fps": None, "uptime_frames": 0, "error_status": True})
            else:
                base_status.update({"resolution": None, "fps": None, "uptime_frames": 0})

            self._status_cache = base_status
            self._status_cache_time = current_time
            return base_status

    def get_uvc_current_values(self) -> dict:
        """Gibt aktuelle UVC-Werte für GUI-Anzeige zurück"""
        with self.capture_lock:
            if not self.video_capture or not self.video_capture.isOpened():
                return {}
            video_capture_ref = self.video_capture
        
            current_time = time.time()
            if hasattr(self, '_uvc_cache_time') and (current_time - self._uvc_cache_time) < 0.1:
                return getattr(self, '_uvc_cache_values', {})
                
            current_values = {}
            
            # Standard UVC-Parameter auslesen
            param_map = {
                "brightness": cv2.CAP_PROP_BRIGHTNESS,
                "contrast": cv2.CAP_PROP_CONTRAST,
                "saturation": cv2.CAP_PROP_SATURATION,
                "hue": cv2.CAP_PROP_HUE,
                "gain": cv2.CAP_PROP_GAIN,
                "sharpness": cv2.CAP_PROP_SHARPNESS,
                "gamma": cv2.CAP_PROP_GAMMA,
                "backlight_compensation": cv2.CAP_PROP_BACKLIGHT,
                "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
                "exposure": cv2.CAP_PROP_EXPOSURE,
                "auto_white_balance": cv2.CAP_PROP_AUTO_WB,
                "white_balance": cv2.CAP_PROP_WHITE_BALANCE_BLUE_U
            }
            
            for name, prop in param_map.items():
                try:
                    value = video_capture_ref.get(prop)
                    if value is not None and isinstance(value, (int, float)):
                        current_values[name] = value
                except cv2.error as e:
                    self.logger.debug(f"OpenCV get failed for {name}: {e}")
                except Exception as e:
                    self.logger.debug(f"Error reading {name}: {e}")
            
            self._uvc_cache_values = current_values
            self._uvc_cache_time = current_time
            return current_values
    
    
    def _invalidate_uvc_cache(self) -> None:
        """Invalidate the cached UVC values."""
        if getattr(self, '_batch_update_mode', False):
            return
        
        try:
            delattr(self, '_uvc_cache_time')
        except AttributeError:
            pass
        
        try:
            delattr(self, '_uvc_cache_values')
        except AttributeError:
            pass

    def get_uvc_ranges(self) -> dict:
        """Gibt Min/Max-Werte für GUI-Slider zurück"""

        defaults = self.get_uvc_defaults()
        return {
            "brightness": {"min": -64, "max": 64, "default": defaults["brightness"]},
            "contrast": {"min": 0, "max": 64, "default": defaults["contrast"]},
            "saturation": {"min": 0, "max": 128, "default": defaults["saturation"]},
            "hue": {"min": -40, "max": 40, "default": defaults["hue"]},
            "gain": {"min": 0, "max": 100, "default": defaults["gain"]},
            "sharpness": {"min": 0, "max": 14, "default": defaults["sharpness"]},
            "gamma": {"min": 72, "max": 500, "default": defaults["gamma"]},
            "backlight_compensation": {"min": 0, "max": 160, "default": defaults["backlight_compensation"]},
            "exposure": {"min": -13, "max": -1, "default": defaults["exposure"]["value"]},
            "white_balance": {"min": 2800, "max": 6500, "default": defaults["white_balance"]["value"]},
        }
    

    def _legacy_reset_uvc_to_defaults(self) -> bool:
        """Setzt alle UVC-Parameter auf Default-Werte zurück"""
        try:
            ranges = self.get_uvc_ranges()
            success = True
            
            # Standard-Parameter zurücksetzen (außer spezielle)
            for param, values in ranges.items():
                if param in ("exposure", "white_balance"):
                    continue
                setter_name = f"set_{param}"
                if hasattr(self, setter_name):
                    result = getattr(self, setter_name)(values["default"])
                    success = success and result

            # Auto-Exposure und Auto-White-Balance aktivieren
            self.set_auto_exposure(True)
            self.set_auto_white_balance(True)

            self._invalidate_uvc_cache()  # Cache invalidieren
            return success
        except Exception as e:
            self.logger.error(f"Error resetting UVC defaults: {e}")
            return False

    def reset_uvc_to_defaults(self) -> bool:
        """Setzt alle UVC-Parameter auf Default-Werte zurück."""
        try:
            defaults = self.get_uvc_defaults()
            failed_params: list[str] = []

            scalar_setters = {
                "brightness": self.set_brightness,
                "contrast": self.set_contrast,
                "saturation": self.set_saturation,
                "sharpness": self.set_sharpness,
                "gamma": self.set_gamma,
                "gain": self.set_gain,
                "backlight_compensation": self.set_backlight_compensation,
                "hue": self.set_hue,
            }
            for param, setter in scalar_setters.items():
                if not setter(defaults[param]):
                    failed_params.append(param)

            white_balance_defaults = defaults["white_balance"]
            if white_balance_defaults["auto"]:
                if not self.set_auto_white_balance(True):
                    failed_params.append("white_balance.auto")
            else:
                if not self.set_manual_white_balance(white_balance_defaults["value"]):
                    failed_params.append("white_balance.value")

            exposure_defaults = defaults["exposure"]
            if exposure_defaults["auto"]:
                if not self.set_auto_exposure(True):
                    failed_params.append("exposure.auto")
            else:
                if not self.set_manual_exposure(exposure_defaults["value"]):
                    failed_params.append("exposure.value")

            self._sync_uvc_config_from_defaults(defaults)
            self._invalidate_uvc_cache()
            self._schedule_uvc_config_save()

            if failed_params:
                self.logger.warning(
                    "Some UVC defaults could not be applied by the driver: %s",
                    ", ".join(failed_params),
                )
            return True
        except Exception as e:
            self.logger.error(f"Error resetting UVC defaults: {e}")
            return False

    # ----------------- Snapshot & Cleanup ----------------------- #

    def _clear_published_frame(self) -> None:
        frame_lock = getattr(self, 'frame_lock', None)
        if frame_lock is None:
            self.current_frame = None
            self._current_jpeg_frame = None
            return
        with frame_lock:
            self.current_frame = None
            self._current_jpeg_frame = None

    def _encode_frame_to_jpeg_bytes(self, frame: np.ndarray) -> Optional[bytes]:
        try:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        except Exception as exc:
            self.logger.debug('Error encoding cached frame: %s', exc, exc_info=True)
            return None
        if not ret:
            return None
        return buffer.tobytes()

    def _publish_current_frame(self, frame: np.ndarray) -> None:
        jpeg_bytes = self._encode_frame_to_jpeg_bytes(frame)
        with self.frame_lock:
            self.current_frame = frame
            self._current_jpeg_frame = jpeg_bytes
            self.frame_count += 1

    def take_snapshot(self) -> Optional[np.ndarray]:
        """Erstellt einen Snapshot (Thread-sicher)."""
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy()
        return None

    def get_current_jpeg_frame(self) -> Optional[bytes]:
        with self.frame_lock:
            if self.current_frame is None:
                return None
            current_jpeg_frame = getattr(self, '_current_jpeg_frame', None)
            if current_jpeg_frame is not None:
                return bytes(current_jpeg_frame)
        return None
    
    def get_current_frame(self, copy_frame: bool = True) -> Optional[np.ndarray]:
        """
        Gibt den aktuellen Frame zurück (Thread-sicher).
        
        Args:
            copy_frame: Wenn True (Default), wird eine Kopie zurückgegeben (sicher).
                       Wenn False, wird eine Referenz zurückgegeben (schneller, aber Mutation vermeiden!).
        """
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy() if copy_frame else self.current_frame
            return None

    def initialize_routes(self) -> None:
        """Initialize the API routes for the video stream."""
        if not self._init_complete.is_set():
            self.logger.warning("initialize_routes called before camera initialization complete")
        _activate_video_camera(self)
            
    def _deprecated_stream_frames(self) -> Iterator[bytes]:
        """Deprecated legacy instance stream helper; the route stack uses global video streaming now."""
        """Generator für den Videostream."""
        while True:
            # Für Streaming keine Kopie nötig, da Encoding nicht mutiert
            frame = self.get_current_frame(copy_frame=False)
            if frame is None:
                # Platzhalter senden wenn kein Frame verfügbar
                yield (b'--frame\r\n'
                       b'Content-Type: image/png\r\n\r\n' + self.placeholder.body + b'\r\n')
                time.sleep(0.1)
                continue
                
            try:
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if not ret:
                    continue
                frame_bytes = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            except Exception as e:
                self.logger.error(f"Error encoding frame: {e}")
                time.sleep(0.1)
            
            time.sleep(0.03) # Limit FPS slightly for stream

    def _cancel_config_save_timer(self) -> None:
        with self._timer_lock:
            if self._config_save_timer is not None:
                try:
                    self._config_save_timer.cancel()
                except Exception:
                    pass
                self._config_save_timer = None

    def suspend_runtime(self) -> bool:
        """Suspend the active runtime so this instance can be resumed later."""
        with self._cleanup_lock:
            if self.cleaned or self._cleanup_in_progress:
                return False
            self._cleanup_in_progress = True

        suspended = False
        self.logger.info("Suspending Camera runtime...")
        try:
            self.is_running = False
            self._capture_ready.clear()
            self._capture_runtime_error = None
            self._cancel_config_save_timer()

            if self._init_thread and self._init_thread.is_alive():
                self.logger.info("Cancelling init thread during runtime suspension...")
                self._request_init_cancel(RuntimeError("Camera initialization cancelled during runtime suspension"))
                if not self._join_init_thread(timeout=2.0):
                    self.logger.error("Init thread did not stop during runtime suspension")
                    return False
                self._init_thread = None

            frame_thread_stopped = True
            if self.frame_thread and self.frame_thread.is_alive():
                self.logger.info("Stopping frame capture for runtime suspension...")
                frame_thread_stopped = self._stop_frame_capture_and_wait(timeout=2.0)
                if not frame_thread_stopped:
                    self.logger.error("Frame capture thread did not stop during runtime suspension")
                    return False

            if frame_thread_stopped and not self._try_release_video_capture(timeout=0.05):
                self.logger.error("Failed to release video capture during runtime suspension")
                return False

            self._clear_published_frame()
            self._init_complete.clear()
            self._init_cancel.clear()
            with self._get_init_state_lock():
                self.initialization_error = None
                self._initialization_succeeded = False
                self._init_terminal_failure = False

            suspended = True
            return True
        finally:
            with self._cleanup_lock:
                self._cleanup_in_progress = False
            if suspended:
                self.logger.info("Camera runtime suspended")
            else:
                self.logger.warning("Camera runtime suspension completed partially")

    def cleanup(self) -> None:  # Entfernt 'async' - keine await-Statements
        """Cleanup method."""
        with self._cleanup_lock:
            if self.cleaned or self._cleanup_in_progress:
                return
            self._cleanup_in_progress = True

        self.logger.info("Starting Camera cleanup...")
        _clear_active_video_camera(self)
        cleanup_complete = False
        try:
            self.is_running = False
            self._capture_ready.clear()
            cleanup_complete = True

            self._cancel_config_save_timer()

            # Stop init thread if running
            if self._init_thread and self._init_thread.is_alive():
                self.logger.info("Cancelling init thread...")
                self._request_init_cancel(RuntimeError("Camera initialization cancelled during cleanup"))
                if not self._join_init_thread(timeout=2.0):
                    self.logger.error("Init thread did not stop during cleanup")
                    self._mark_terminal_init_failure("Camera initialization thread did not stop during cleanup")
                    cleanup_complete = False
            
            # Stop frame capture thread
            frame_thread_stopped = True
            if self.frame_thread and self.frame_thread.is_alive():
                self.logger.info("Stopping frame capture...")
                frame_thread_stopped = self._stop_frame_capture_and_wait(timeout=2.0)
                if not frame_thread_stopped:
                    self.logger.error("Frame capture thread did not stop during cleanup")
                    cleanup_complete = False
            
            if frame_thread_stopped:
                # Cleanup motion detector
                if self.motion_detector is not None:
                    try:
                        self.motion_detector.cleanup()
                    except Exception as e:
                        self.logger.debug(f"Error cleaning up motion detector: {e}")
                    self.motion_detector = None
                with self._motion_callbacks_lock:
                    self._motion_callbacks.clear()
                    self.motion_enabled = False
                
                # Clear frame pool (Issue #5)
                if hasattr(self, '_frame_pool'):
                    self._frame_pool.clear()
                
                # Release camera
                if not self._try_release_video_capture(timeout=0.05):
                    cleanup_complete = False
                self._clear_published_frame()
            else:
                self.logger.warning(
                    "Skipping cleanup of motion resources and video capture because frame capture thread is still alive"
                )
        finally:
            with self._cleanup_lock:
                self.cleaned = cleanup_complete
                self._cleanup_in_progress = False

        if cleanup_complete:
            self.logger.info("Camera cleanup completed")
        else:
            self.logger.warning("Camera cleanup completed partially; object remains not fully cleaned")
