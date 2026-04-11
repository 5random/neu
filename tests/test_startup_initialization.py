from __future__ import annotations

import asyncio
import collections
from dataclasses import asdict
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from fastapi.responses import StreamingResponse

import main as app_main
from src import config as config_module
from src.cam import camera as camera_module
from src.cam.camera import Camera
from src.config import (
    ConfigLoadError,
    _create_default_config,
    _reset_configured_logger,
    load_config,
    save_global_config,
    set_global_config,
)
from src.gui import init as gui_init
from src.gui import instances


class _DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


def _cleanup_local_temp_dir(temp_path: Path, *paths: Path) -> None:
    _reset_configured_logger("cvd_tracker")
    for path in paths:
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except OSError:
            pass
    try:
        temp_path.rmdir()
    except OSError:
        pass


def _initialize_camera_test_state(camera: Camera) -> Camera:
    camera.capture_lock = threading.RLock()
    camera._init_state_lock = threading.Lock()
    camera._cleanup_lock = threading.Lock()
    camera._cleanup_in_progress = False
    camera.cleaned = False
    return camera


def _make_camera_runtime_stub(name: str = "test.camera.runtime") -> Camera:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.frame_lock = threading.Lock()
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger(name)
    camera._init_thread = None
    camera.frame_thread = None
    camera._capture_ready = threading.Event()
    camera._capture_runtime_error = None
    camera.current_frame = None
    camera.frame_count = 0
    camera.motion_skip_frames = 1
    camera.motion_detector = None
    camera.motion_enabled = False
    camera._motion_callbacks_lock = threading.Lock()
    camera._motion_callbacks = {}
    camera._reconnect_attempts = 0
    camera.CAPTURE_READY_TIMEOUT_SECONDS = 0.2
    return camera


def _prepare_camera_cleanup_stub(camera: Camera) -> Camera:
    camera._timer_lock = threading.Lock()
    camera._config_save_timer = None
    camera.motion_detector = None
    camera._motion_callbacks_lock = threading.Lock()
    camera._motion_callbacks = {}
    camera.motion_enabled = False
    camera._frame_pool = collections.deque()
    camera.frame_thread = None
    camera._init_thread = None
    camera.video_capture = None
    camera._try_release_video_capture = lambda timeout=0.05: True
    camera._stop_frame_capture_and_wait = lambda timeout=2.0: True
    return camera


class _FakeRouteApp:
    def __init__(self) -> None:
        self.routes: list[SimpleNamespace] = []

    def get(self, path: str):
        def decorator(func):
            self.routes.append(SimpleNamespace(path=path, endpoint=func))
            return func

        return decorator


async def _read_first_stream_chunk(response: StreamingResponse) -> bytes:
    return await response.body_iterator.__anext__()


def _restore_config_registry_state(
    monkeypatch,
    previous_config: object | None,
    previous_config_path: str,
) -> None:
    monkeypatch.setattr(config_module, "_global_config", previous_config, raising=False)
    monkeypatch.setattr(config_module, "_config_path", previous_config_path, raising=False)


def test_get_logger_without_global_config_does_not_load_config(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "_global_config", None, raising=False)
    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("load_config must not be called")),
    )

    logger = config_module.get_logger("startup.test")

    assert logger.name == "cvd_tracker.startup.test"


def test_main_uses_requested_config_path_before_gui_init(monkeypatch) -> None:
    cfg = _create_default_config()
    args = SimpleNamespace(config="custom/config.yaml", host=None, port=None, open_browser=None)
    calls: list[tuple] = []

    monkeypatch.setattr(config_module, "_global_config", None, raising=False)
    monkeypatch.setattr(config_module, "_config_path", "config/config.yaml", raising=False)
    monkeypatch.setattr(app_main, "parse_args", lambda: args)
    monkeypatch.setattr(app_main, "load_config", lambda path: calls.append(("load_config", path)) or cfg)

    def fake_set_global_config(loaded_cfg, path: str) -> None:
        resolved_path = str(config_module._resolve_config_path(path))
        calls.append(("set_global_config", path, resolved_path))
        monkeypatch.setattr(config_module, "_global_config", loaded_cfg, raising=False)
        monkeypatch.setattr(config_module, "_config_path", resolved_path, raising=False)

    monkeypatch.setattr(app_main, "set_global_config", fake_set_global_config)
    monkeypatch.setattr(app_main, "get_logger", lambda name: _DummyLogger())
    monkeypatch.setattr(app_main, "setup_exception_handlers", lambda logger: calls.append(("setup_exception_handlers",)))
    monkeypatch.setattr(app_main, "install_asyncio_exception_handler", lambda logger: calls.append(("install_asyncio_exception_handler",)))
    monkeypatch.setattr(app_main, "install_nicegui_timer_patch", lambda logger: calls.append(("install_nicegui_timer_patch",)))

    def fake_create_gui(*, config_path: str) -> None:
        calls.append(("create_gui", config_path, config_module.get_global_config_path()))

    monkeypatch.setattr(app_main, "create_gui", fake_create_gui)
    monkeypatch.setattr(app_main, "resolve_storage_secret", lambda logger: "secret")
    monkeypatch.setattr(
        app_main,
        "resolve_ui_run_settings",
        lambda cfg, args, logger: {
            "host": "127.0.0.1",
            "port": 8080,
            "show": False,
            "headless_linux": False,
            "reverse_proxy_enabled": False,
            "forwarded_allow_ips": "127.0.0.1",
            "root_path": "",
            "session_middleware_kwargs": None,
        },
    )
    monkeypatch.setattr(app_main, "compute_gui_title", lambda cfg: "CVD-Tracker")
    monkeypatch.setattr(app_main, "app", SimpleNamespace(storage=SimpleNamespace(general={})))
    monkeypatch.setattr(app_main.ui, "run", lambda **kwargs: calls.append(("ui.run", kwargs["host"], kwargs["port"])))

    expected_path = str(config_module._resolve_config_path("custom/config.yaml"))
    assert app_main.main() == 0
    assert calls[0] == ("load_config", "custom/config.yaml")
    assert ("set_global_config", "custom/config.yaml", expected_path) in calls
    create_gui_call = next(item for item in calls if item[0] == "create_gui")
    assert create_gui_call[1] == "custom/config.yaml"
    assert create_gui_call[2] == expected_path
    assert calls.index(("set_global_config", "custom/config.yaml", expected_path)) < calls.index(create_gui_call)


def test_main_returns_error_for_invalid_requested_config(monkeypatch) -> None:
    args = SimpleNamespace(config="broken/config.yaml", host=None, port=None, open_browser=None)
    calls: list[tuple] = []

    monkeypatch.setattr(app_main, "parse_args", lambda: args)
    monkeypatch.setattr(app_main, "load_config", lambda path: (_ for _ in ()).throw(ConfigLoadError("invalid config")))
    monkeypatch.setattr(app_main, "create_gui", lambda **kwargs: calls.append(("create_gui",)))
    monkeypatch.setattr(app_main.ui, "run", lambda **kwargs: calls.append(("ui.run",)))

    assert app_main.main() == 1
    assert ("create_gui",) not in calls
    assert ("ui.run",) not in calls


def test_main_uses_fallback_logger_before_app_logger_is_available(monkeypatch) -> None:
    args = SimpleNamespace(config="broken/config.yaml", host=None, port=None, open_browser=None)
    fallback_calls: list[str] = []

    _reset_configured_logger("cvd_tracker")
    monkeypatch.setattr(config_module, "_global_config", None, raising=False)
    monkeypatch.setattr(
        config_module,
        "_config_path",
        str(config_module._resolve_config_path("config/config.yaml")),
        raising=False,
    )
    monkeypatch.setattr(app_main, "parse_args", lambda: args)
    monkeypatch.setattr(app_main, "load_config", lambda path: (_ for _ in ()).throw(ConfigLoadError("invalid config")))
    monkeypatch.setattr(
        app_main,
        "create_fallback_logger",
        lambda: fallback_calls.append("fallback") or _DummyLogger(),
    )

    assert app_main.main() == 1
    assert fallback_calls == ["fallback"]


def test_main_logs_fatal_create_gui_failure_with_active_logger(monkeypatch) -> None:
    args = SimpleNamespace(config="custom/config.yaml", host=None, port=None, open_browser=None)
    temp_path = Path(".pytest_startup_runtime_fatal_gui")
    temp_path.mkdir(exist_ok=True)
    log_path = temp_path / "fatal_gui.log"
    cfg = _create_default_config()
    cfg.logging.file = str(log_path)
    cfg.logging.console_output = False
    fallback_calls: list[str] = []

    _reset_configured_logger("cvd_tracker")
    monkeypatch.setattr(config_module, "_global_config", None, raising=False)
    monkeypatch.setattr(
        config_module,
        "_config_path",
        str(config_module._resolve_config_path("config/config.yaml")),
        raising=False,
    )
    monkeypatch.setattr(app_main, "parse_args", lambda: args)
    monkeypatch.setattr(app_main, "load_config", lambda path: cfg)
    monkeypatch.setattr(app_main, "setup_exception_handlers", lambda logger: None)
    monkeypatch.setattr(app_main, "install_asyncio_exception_handler", lambda logger: None)
    monkeypatch.setattr(app_main, "install_nicegui_timer_patch", lambda logger: None)
    monkeypatch.setattr(
        app_main,
        "create_gui",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fatal gui init")),
    )
    monkeypatch.setattr(
        app_main,
        "create_fallback_logger",
        lambda: fallback_calls.append("fallback") or _DummyLogger(),
    )

    try:
        assert app_main.main() == 1
        assert fallback_calls == []
        assert log_path.exists()
        log_text = log_path.read_text(encoding="utf-8")
        assert "Error occurred at startup: fatal gui init" in log_text
    finally:
        _cleanup_local_temp_dir(temp_path, log_path)


def test_set_global_config_stores_canonical_config_path(monkeypatch) -> None:
    cfg = _create_default_config()
    raw_path = ".pytest_startup_runtime_canonical.yaml"
    monkeypatch.setattr(config_module, "_global_config", None, raising=False)
    monkeypatch.setattr(
        config_module,
        "_config_path",
        str(config_module._resolve_config_path("config/config.yaml")),
        raising=False,
    )

    set_global_config(cfg, raw_path)

    assert config_module.get_global_config_path() == str(config_module._resolve_config_path(raw_path))


def test_save_global_config_round_trips_relative_config_path_from_non_project_cwd(monkeypatch) -> None:
    temp_path = Path(".pytest_startup_runtime_roundtrip")
    temp_path.mkdir(exist_ok=True)
    cwd_path = temp_path / "cwd"
    cwd_path.mkdir(exist_ok=True)
    config_path = Path(".pytest_startup_runtime_roundtrip.yaml")
    resolved_cwd_path = cwd_path.resolve()
    resolved_config_path = config_module._resolve_config_path(str(config_path))
    log_path = (temp_path / "roundtrip.log").resolve()
    unexpected_path = resolved_cwd_path / config_path

    raw = asdict(_create_default_config())
    raw["logging"]["file"] = str(log_path)
    resolved_config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(config_module, "_global_config", None, raising=False)
    monkeypatch.setattr(
        config_module,
        "_config_path",
        str(config_module._resolve_config_path("config/config.yaml")),
        raising=False,
    )

    try:
        monkeypatch.chdir(resolved_cwd_path)
        cfg = load_config(str(config_path))
        set_global_config(cfg, str(config_path))
        cfg.gui.title = "Roundtrip Updated"

        assert save_global_config() is True
        persisted = yaml.safe_load(resolved_config_path.read_text(encoding="utf-8"))
        assert persisted["gui"]["title"] == "Roundtrip Updated"
        assert config_module.get_global_config_path() == str(config_module._resolve_config_path(str(config_path)))
        assert not unexpected_path.exists()
    finally:
        _cleanup_local_temp_dir(temp_path, unexpected_path, cwd_path, resolved_config_path, log_path)


def test_load_effective_config_reloads_same_path_for_relative_path_from_non_project_cwd(monkeypatch) -> None:
    cfg = _create_default_config()
    temp_path = Path(".pytest_startup_runtime_effective_config")
    temp_path.mkdir(exist_ok=True)
    cwd_path = temp_path / "cwd"
    cwd_path.mkdir(exist_ok=True)
    calls: list[tuple[str, object]] = []

    try:
        monkeypatch.chdir(cwd_path.resolve())
        monkeypatch.setattr(
            gui_init,
            "load_config",
            lambda path: calls.append(("load_config", path)) or cfg,
        )
        monkeypatch.setattr(
            gui_init,
            "set_global_config",
            lambda config, path: calls.append(("set_global_config", path)),
        )

        assert gui_init._load_effective_config("custom/config.yaml") is cfg
        assert calls == [
            ("load_config", "custom/config.yaml"),
            ("set_global_config", "custom/config.yaml"),
        ]
    finally:
        _cleanup_local_temp_dir(temp_path, cwd_path)


def test_camera_wait_for_init_returns_false_on_timeout() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.timeout")
    camera._init_thread = None

    assert camera.wait_for_init(timeout=0.0) is False
    assert isinstance(camera.initialization_error, TimeoutError)


def test_camera_wait_for_init_returns_false_when_worker_failed() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera._init_complete.set()
    camera.initialization_error = RuntimeError("camera boom")
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.failed")
    camera._init_thread = None

    assert camera.wait_for_init(timeout=0.0) is False


def test_camera_wait_for_init_cancels_async_thread_on_timeout() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.timeout_cancel")
    thread_stopped = threading.Event()

    def worker() -> None:
        while not camera._init_cancel.is_set():
            time.sleep(0.01)
        thread_stopped.set()
        camera._init_complete.set()

    camera._init_thread = threading.Thread(target=worker, daemon=True)
    camera._init_thread.start()

    assert camera.wait_for_init(timeout=0.01) is False
    assert thread_stopped.wait(0.5)
    camera._init_thread.join(timeout=0.5)
    assert not camera._init_thread.is_alive()
    assert isinstance(camera.initialization_error, (TimeoutError, RuntimeError))


def test_camera_wait_for_init_marks_terminal_failure_when_thread_does_not_stop() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.timeout_stuck")
    camera._init_thread = object()
    camera._join_init_thread = lambda timeout: False
    camera.stop_frame_capture = lambda: None

    assert camera.wait_for_init(timeout=0.0) is False
    assert isinstance(camera.initialization_error, RuntimeError)
    assert "did not stop after timeout" in str(camera.initialization_error)
    assert camera._init_terminal_failure is True

    assert camera._mark_initialization_success() is False
    assert camera._initialization_succeeded is False


def test_camera_mark_initialization_success_returns_false_after_cancel() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.cancel_beats_success")
    cleanup_calls: list[str] = []

    camera._cleanup_after_failed_initialization = lambda: cleanup_calls.append("cleanup")

    camera._request_init_cancel(RuntimeError("cancelled"))

    assert camera._mark_initialization_success() is False
    assert cleanup_calls == ["cleanup"]
    assert isinstance(camera.initialization_error, RuntimeError)
    assert camera._initialization_succeeded is False


def test_camera_wait_for_init_timeout_preserves_failure_state_after_late_success() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.timeout_late_success")
    cleanup_calls: list[str] = []

    camera._cleanup_after_failed_initialization = lambda: cleanup_calls.append("cleanup")

    def late_success_join(timeout: float) -> bool:
        assert camera._mark_initialization_success() is False
        return True

    camera._join_init_thread = late_success_join

    assert camera.wait_for_init(timeout=0.0) is False
    assert cleanup_calls == ["cleanup"]
    assert isinstance(camera.initialization_error, TimeoutError)
    assert camera._initialization_succeeded is False
    assert camera._init_complete.is_set()


def test_camera_wait_for_runtime_ready_raises_when_initialization_is_cancelled() -> None:
    class _OpenedCapture:
        def isOpened(self) -> bool:
            return True

    camera = _make_camera_runtime_stub("test.camera.runtime_cancelled")
    camera.video_capture = _OpenedCapture()
    camera.is_running = True
    camera.frame_thread = SimpleNamespace(is_alive=lambda: True)

    def cancel_wait() -> None:
        time.sleep(0.01)
        camera._request_init_cancel(RuntimeError("cancelled during runtime wait"))
        camera._capture_ready.set()

    cancel_thread = threading.Thread(target=cancel_wait, daemon=True)
    cancel_thread.start()

    with pytest.raises(camera_module.CameraInitializationCancelled):
        camera._wait_for_runtime_ready(timeout=0.5)

    cancel_thread.join(timeout=0.5)
    assert isinstance(camera.initialization_error, RuntimeError)
    assert camera._initialization_succeeded is False


def test_camera_wait_for_init_uses_remaining_budget_for_join() -> None:
    class _WaitStub:
        def wait(self, timeout: float) -> bool:
            time.sleep(0.02)
            return False

        def set(self) -> None:
            return None

    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = _WaitStub()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.timeout_budget")
    join_timeouts: list[float] = []
    camera._join_init_thread = lambda timeout: join_timeouts.append(timeout) or True

    assert camera.wait_for_init(timeout=0.05) is False
    assert join_timeouts
    assert 0.0 <= join_timeouts[0] < 0.05


def test_camera_cleanup_cancels_async_init_thread() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera.logger = logging.getLogger("test.camera.cleanup")
    camera.is_running = False
    camera._timer_lock = threading.Lock()
    camera._config_save_timer = None
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.frame_thread = None
    camera._capture_ready = threading.Event()
    camera.motion_detector = None
    camera._motion_callbacks_lock = threading.Lock()
    camera._motion_callbacks = {}
    camera.motion_enabled = False
    camera._frame_pool = collections.deque()
    camera.video_capture = None
    thread_stopped = threading.Event()

    def worker() -> None:
        while not camera._init_cancel.is_set():
            time.sleep(0.01)
        thread_stopped.set()
        camera._init_complete.set()

    camera._init_thread = threading.Thread(target=worker, daemon=True)
    camera._init_thread.start()

    camera.cleanup()

    assert thread_stopped.wait(0.5)
    assert not camera._init_thread.is_alive()
    assert camera.cleaned is True


def test_camera_cleanup_leaves_cleaned_false_when_init_thread_survives() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera.logger = logging.getLogger("test.camera.cleanup_stuck")
    camera.is_running = False
    camera._timer_lock = threading.Lock()
    camera._config_save_timer = None
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.frame_thread = None
    camera._capture_ready = threading.Event()
    cleaned_motion: list[str] = []
    camera.motion_detector = SimpleNamespace(cleanup=lambda: cleaned_motion.append("cleanup"))
    camera._motion_callbacks_lock = threading.Lock()
    camera._motion_callbacks = {"cb": object()}
    camera.motion_enabled = False
    camera._frame_pool = collections.deque([1, 2, 3])
    camera.video_capture = None
    camera._init_thread = SimpleNamespace(is_alive=lambda: True)
    camera._join_init_thread = lambda timeout: False

    camera.cleanup()

    assert camera.cleaned is False
    assert isinstance(camera.initialization_error, RuntimeError)
    assert "did not stop during cleanup" in str(camera.initialization_error)
    assert camera._init_terminal_failure is True
    assert cleaned_motion == ["cleanup"]
    assert camera.motion_detector is None
    assert camera._motion_callbacks == {}
    assert list(camera._frame_pool) == []


def test_camera_cleanup_skips_capture_release_when_capture_lock_is_busy() -> None:
    class _DummyCapture:
        def __init__(self) -> None:
            self.release_calls = 0

        def release(self) -> None:
            self.release_calls += 1

    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera.logger = logging.getLogger("test.camera.cleanup_busy_lock")
    camera.is_running = False
    camera._timer_lock = threading.Lock()
    camera._config_save_timer = None
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.frame_thread = None
    camera._capture_ready = threading.Event()
    camera.motion_detector = None
    camera._motion_callbacks_lock = threading.Lock()
    camera._motion_callbacks = {}
    camera.motion_enabled = False
    camera._frame_pool = collections.deque()
    camera.video_capture = _DummyCapture()
    camera._init_thread = SimpleNamespace(is_alive=lambda: True)
    camera._join_init_thread = lambda timeout: False
    acquired = threading.Event()
    release_holder = threading.Event()

    def hold_capture_lock() -> None:
        with camera.capture_lock:
            acquired.set()
            release_holder.wait(timeout=1.0)

    holder = threading.Thread(target=hold_capture_lock, daemon=True)
    holder.start()
    assert acquired.wait(0.5)

    try:
        camera.cleanup()
    finally:
        release_holder.set()
        holder.join(timeout=0.5)

    assert camera.cleaned is False
    assert isinstance(camera.initialization_error, RuntimeError)
    assert camera.video_capture is not None
    assert camera.video_capture.release_calls == 0


def test_camera_cleanup_does_not_mark_cleaned_or_clear_motion_resources_while_frame_thread_survives() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera.logger = logging.getLogger("test.camera.cleanup_frame_thread_stuck")
    camera.is_running = True
    camera._timer_lock = threading.Lock()
    camera._config_save_timer = None
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera._init_thread = None
    camera.frame_thread = SimpleNamespace(is_alive=lambda: True)
    cleaned_motion: list[str] = []
    camera.motion_detector = SimpleNamespace(cleanup=lambda: cleaned_motion.append("cleanup"))
    camera._motion_callbacks_lock = threading.Lock()
    camera._motion_callbacks = {"cb": object()}
    camera.motion_enabled = True
    camera._frame_pool = collections.deque([1, 2, 3])
    camera._capture_ready = threading.Event()
    camera._stop_frame_capture_and_wait = lambda timeout: False

    camera.cleanup()

    assert camera.cleaned is False
    assert cleaned_motion == []
    assert camera.motion_detector is not None
    assert set(camera._motion_callbacks.keys()) == {"cb"}
    assert camera.motion_enabled is True
    assert list(camera._frame_pool) == [1, 2, 3]


def test_camera_cleanup_retry_clears_resources_after_frame_thread_stops() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera.logger = logging.getLogger("test.camera.cleanup_retry")
    camera.is_running = True
    camera._timer_lock = threading.Lock()
    camera._config_save_timer = None
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera._init_thread = None
    camera.frame_thread = SimpleNamespace(is_alive=lambda: True)
    cleaned_motion: list[str] = []
    camera.motion_detector = SimpleNamespace(cleanup=lambda: cleaned_motion.append("cleanup"))
    camera._motion_callbacks_lock = threading.Lock()
    camera._motion_callbacks = {"cb": object()}
    camera.motion_enabled = True
    camera._frame_pool = collections.deque([1, 2, 3])
    camera._capture_ready = threading.Event()
    stop_results = iter([False, True])

    def fake_stop_frame_capture_and_wait(timeout: float) -> bool:
        result = next(stop_results)
        if result:
            camera.frame_thread = None
        return result

    camera._stop_frame_capture_and_wait = fake_stop_frame_capture_and_wait

    camera.cleanup()

    assert camera.cleaned is False
    assert cleaned_motion == []
    assert camera.motion_detector is not None
    assert set(camera._motion_callbacks.keys()) == {"cb"}
    assert list(camera._frame_pool) == [1, 2, 3]

    camera.cleanup()

    assert camera.cleaned is True
    assert cleaned_motion == ["cleanup"]
    assert camera.motion_detector is None
    assert camera._motion_callbacks == {}
    assert list(camera._frame_pool) == []


def test_camera_cleanup_is_not_reentrant() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera.logger = logging.getLogger("test.camera.cleanup_reentrant")
    camera.is_running = False
    camera._timer_lock = threading.Lock()
    camera._config_save_timer = None
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera._init_thread = None
    camera.frame_thread = None
    camera._capture_ready = threading.Event()
    camera._motion_callbacks_lock = threading.Lock()
    camera._motion_callbacks = {}
    camera.motion_enabled = False
    camera._frame_pool = collections.deque()
    entered_cleanup = threading.Event()
    release_cleanup = threading.Event()
    second_cleanup_returned = threading.Event()
    cleanup_state_lock = threading.Lock()
    cleanup_calls = 0
    inflight = 0
    max_inflight = 0

    def slow_motion_cleanup() -> None:
        nonlocal cleanup_calls, inflight, max_inflight
        with cleanup_state_lock:
            cleanup_calls += 1
            inflight += 1
            max_inflight = max(max_inflight, inflight)
        entered_cleanup.set()
        release_cleanup.wait(timeout=1.0)
        with cleanup_state_lock:
            inflight -= 1

    camera.motion_detector = SimpleNamespace(cleanup=slow_motion_cleanup)

    first_cleanup = threading.Thread(target=camera.cleanup, daemon=True)
    first_cleanup.start()
    assert entered_cleanup.wait(0.5)

    def run_second_cleanup() -> None:
        camera.cleanup()
        second_cleanup_returned.set()

    second_cleanup = threading.Thread(target=run_second_cleanup, daemon=True)
    second_cleanup.start()

    assert second_cleanup_returned.wait(0.2)

    release_cleanup.set()
    first_cleanup.join(timeout=1.0)
    second_cleanup.join(timeout=1.0)

    assert cleanup_calls == 1
    assert max_inflight == 1
    assert camera.cleaned is True


def test_camera_initialize_sync_returns_false_when_success_commit_is_rejected(caplog) -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.sync_commit_rejected")
    camera._init_thread = None
    camera._capture_ready = threading.Event()
    camera._capture_runtime_error = None
    camera._cleanup_after_failed_initialization = lambda: None
    camera._initialize_camera = lambda: None
    camera.start_frame_capture = lambda: None

    def fake_wait_for_runtime_ready() -> None:
        camera._request_init_cancel(RuntimeError("cancelled before success commit"))

    camera._wait_for_runtime_ready = fake_wait_for_runtime_ready

    with caplog.at_level(logging.INFO, logger="test.camera.sync_commit_rejected"):
        assert Camera.initialize_sync(camera) is False

    assert isinstance(camera.initialization_error, RuntimeError)
    assert camera._initialization_succeeded is False
    assert "Synchronous camera initialization completed successfully" not in caplog.text


def test_camera_defaults_to_sync_initialization(monkeypatch) -> None:
    cfg = _create_default_config()
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(Camera, "initialize_sync", lambda self: calls.append(("initialize_sync",)) or False)
    monkeypatch.setattr(Camera, "_start_async_init", lambda self: calls.append(("start_async_init",)))

    Camera(cfg, logger=logging.getLogger("test.camera.default_sync"))

    assert ("initialize_sync",) in calls
    assert ("start_async_init",) not in calls


def test_camera_async_worker_starts_frame_capture_before_success() -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.async_worker")
    calls: list[tuple[str, ...]] = []

    def fake_initialize_camera() -> None:
        calls.append(("initialize_camera",))

    def fake_start_frame_capture() -> None:
        calls.append(("start_frame_capture",))
        camera.is_running = True

    def fake_wait_for_runtime_ready() -> None:
        calls.append(("wait_for_runtime_ready",))

    camera._initialize_camera = fake_initialize_camera
    camera.start_frame_capture = fake_start_frame_capture
    camera._wait_for_runtime_ready = fake_wait_for_runtime_ready

    camera._init_worker()

    assert calls == [("initialize_camera",), ("start_frame_capture",), ("wait_for_runtime_ready",)]
    assert camera._initialization_succeeded is True
    assert camera._init_complete.is_set()


def test_camera_async_worker_does_not_log_success_when_success_commit_is_rejected(caplog) -> None:
    camera = _initialize_camera_test_state(Camera.__new__(Camera))
    camera._init_complete = threading.Event()
    camera._init_cancel = threading.Event()
    camera.initialization_error = None
    camera._initialization_succeeded = False
    camera._init_terminal_failure = False
    camera.video_capture = None
    camera.is_running = False
    camera.logger = logging.getLogger("test.camera.async_commit_rejected")
    camera._initialize_camera = lambda: None
    camera.start_frame_capture = lambda: None

    def fake_wait_for_runtime_ready() -> None:
        camera._request_init_cancel(RuntimeError("cancelled before async success commit"))

    camera._wait_for_runtime_ready = fake_wait_for_runtime_ready
    cleanup_calls: list[str] = []
    camera._cleanup_after_failed_initialization = lambda: cleanup_calls.append("cleanup")

    with caplog.at_level(logging.INFO, logger="test.camera.async_commit_rejected"):
        camera._init_worker()

    assert cleanup_calls == ["cleanup"]
    assert isinstance(camera.initialization_error, RuntimeError)
    assert camera._initialization_succeeded is False
    assert "Async camera initialization completed successfully" not in caplog.text


def test_camera_start_frame_capture_marks_runtime_ready_after_first_frame() -> None:
    class _SuccessfulCapture:
        def isOpened(self) -> bool:
            return True

        def read(self):
            return True, np.zeros((1, 1, 3), dtype=np.uint8)

        def release(self) -> None:
            return None

    camera = _make_camera_runtime_stub("test.camera.capture_ready")
    camera.video_capture = _SuccessfulCapture()

    camera.start_frame_capture()

    camera._wait_for_runtime_ready(timeout=0.5)
    assert camera._capture_ready.is_set() is True
    assert camera.is_running is True

    camera.stop_frame_capture()
    assert camera.is_running is False


def test_camera_initialize_sync_returns_false_when_capture_thread_dies_before_first_frame() -> None:
    class _FailingCapture:
        def __init__(self) -> None:
            self.released = False

        def isOpened(self) -> bool:
            return True

        def read(self):
            return False, None

        def release(self) -> None:
            self.released = True

    camera = _make_camera_runtime_stub("test.camera.runtime_failure")
    failing_capture = _FailingCapture()

    def fake_initialize_camera() -> None:
        camera.video_capture = failing_capture

    camera._initialize_camera = fake_initialize_camera
    camera._handle_cam_disconnect = lambda: False

    assert Camera.initialize_sync(camera) is False
    assert isinstance(camera.initialization_error, RuntimeError)
    assert camera.is_running is False
    assert camera._capture_ready.is_set() is False
    assert failing_capture.released is True


def test_camera_cleanup_after_failed_initialization_is_bounded_when_frame_thread_holds_capture_lock() -> None:
    class _DummyCapture:
        def __init__(self) -> None:
            self.release_calls = 0

        def release(self) -> None:
            self.release_calls += 1

    camera = _make_camera_runtime_stub("test.camera.failed_init_cleanup_bounded")
    camera.video_capture = _DummyCapture()
    camera.is_running = True
    acquired = threading.Event()
    release_holder = threading.Event()

    def hold_capture_lock() -> None:
        with camera.capture_lock:
            acquired.set()
            release_holder.wait(timeout=1.0)

    holder = threading.Thread(target=hold_capture_lock, daemon=True)
    camera.frame_thread = holder
    holder.start()
    assert acquired.wait(0.5)

    start = time.monotonic()
    try:
        Camera._cleanup_after_failed_initialization(camera)
    finally:
        release_holder.set()
        holder.join(timeout=1.5)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert camera.video_capture is not None
    assert camera.video_capture.release_calls == 0


def test_camera_wait_for_init_returns_false_when_runtime_not_ready_after_success() -> None:
    class _OpenedCapture:
        def isOpened(self) -> bool:
            return True

    camera = _make_camera_runtime_stub("test.camera.runtime_not_ready")
    camera._init_complete.set()
    camera._initialization_succeeded = True
    camera.video_capture = _OpenedCapture()
    camera.is_running = True
    camera.frame_thread = SimpleNamespace(is_alive=lambda: False)
    camera._capture_runtime_error = RuntimeError("capture failed")

    assert camera.wait_for_init(timeout=0.0) is False


def test_camera_initialize_camera_uses_warmup_constants_for_default_backend_fallback(monkeypatch) -> None:
    class _Capture:
        def __init__(self) -> None:
            self.read_calls = 0
            self.released = False

        def isOpened(self) -> bool:
            return True

        def read(self):
            self.read_calls += 1
            return False, None

        def release(self) -> None:
            self.released = True

    primary_capture = _Capture()
    fallback_capture = _Capture()
    created_backends: list[int] = []

    def fake_video_capture(_camera_index: int, backend: int):
        created_backends.append(backend)
        if len(created_backends) == 1:
            return primary_capture
        return fallback_capture

    camera = _make_camera_runtime_stub("test.camera.fallback_warmup")
    camera.webcam_config = SimpleNamespace(
        camera_index=0,
        fps=30,
        get_default_resolution=lambda: SimpleNamespace(width=640, height=480),
    )
    camera.backend = 1
    camera.WARMUP_FRAMES = 2
    camera.FRAME_WAIT_SECONDS = 0.0
    camera._set_camera_properties = lambda capture: None
    camera._apply_uvc_controls = lambda: None
    camera._check_init_cancelled = lambda: None

    monkeypatch.setattr(camera_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", fake_video_capture)

    with pytest.raises(RuntimeError, match="No frame received from camera"):
        Camera._initialize_camera(camera)

    assert created_backends == [1, 0]
    assert primary_capture.read_calls == 2
    assert fallback_capture.read_calls == 2


def test_camera_initialize_routes_registers_video_routes_once_per_process(monkeypatch) -> None:
    fake_app = _FakeRouteApp()
    camera_one = _make_camera_runtime_stub("test.camera.routes.one")
    camera_two = _make_camera_runtime_stub("test.camera.routes.two")
    camera_one._init_complete.set()
    camera_two._init_complete.set()

    monkeypatch.setattr(camera_module, "app", fake_app)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTES_REGISTERED", False, raising=False)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTE_APP", None, raising=False)
    monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)

    camera_one.initialize_routes()
    camera_two.initialize_routes()

    registered_paths = [route.path for route in fake_app.routes]
    assert registered_paths.count("/video_feed") == 1
    assert registered_paths.count("/video/frame") == 1
    assert camera_module._ACTIVE_VIDEO_CAMERA is camera_two


def test_camera_video_frame_route_uses_latest_active_camera(monkeypatch) -> None:
    fake_app = _FakeRouteApp()
    camera_one = _make_camera_runtime_stub("test.camera.routes.active.one")
    camera_two = _make_camera_runtime_stub("test.camera.routes.active.two")
    camera_one._init_complete.set()
    camera_two._init_complete.set()
    camera_one.placeholder = SimpleNamespace(body=b"camera-one")
    camera_two.placeholder = SimpleNamespace(body=b"camera-two")

    monkeypatch.setattr(camera_module, "app", fake_app)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTES_REGISTERED", False, raising=False)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTE_APP", None, raising=False)
    monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)

    camera_one.initialize_routes()
    camera_two.initialize_routes()

    video_frame_route = next(route for route in fake_app.routes if route.path == "/video/frame")
    response = video_frame_route.endpoint()

    assert bytes(response.body) == b"camera-two"
    assert response.media_type == "image/png"


def test_camera_cleanup_only_clears_active_video_camera_for_current_instance(monkeypatch) -> None:
    fake_app = _FakeRouteApp()
    camera_one = _prepare_camera_cleanup_stub(_make_camera_runtime_stub("test.camera.cleanup.active.one"))
    camera_two = _prepare_camera_cleanup_stub(_make_camera_runtime_stub("test.camera.cleanup.active.two"))
    camera_one._init_complete.set()
    camera_two._init_complete.set()

    monkeypatch.setattr(camera_module, "app", fake_app)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTES_REGISTERED", False, raising=False)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTE_APP", None, raising=False)
    monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)

    camera_one.initialize_routes()
    camera_two.initialize_routes()

    camera_one.cleanup()
    assert camera_module._ACTIVE_VIDEO_CAMERA is camera_two

    camera_two.cleanup()
    assert camera_module._ACTIVE_VIDEO_CAMERA is None


def test_camera_video_routes_return_placeholder_without_active_camera(monkeypatch) -> None:
    fake_app = _FakeRouteApp()

    monkeypatch.setattr(camera_module, "app", fake_app)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTES_REGISTERED", False, raising=False)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTE_APP", None, raising=False)
    monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)

    camera_module._ensure_video_routes_registered()

    video_frame_route = next(route for route in fake_app.routes if route.path == "/video/frame")
    video_feed_route = next(route for route in fake_app.routes if route.path == "/video_feed")

    frame_response = video_frame_route.endpoint()
    stream_response = video_feed_route.endpoint()
    first_chunk = asyncio.run(_read_first_stream_chunk(stream_response))

    assert bytes(frame_response.body) == camera_module._DEFAULT_VIDEO_PLACEHOLDER_BODY
    assert isinstance(stream_response, StreamingResponse)
    assert camera_module._DEFAULT_VIDEO_PLACEHOLDER_BODY in first_chunk
    assert b"Content-Type: image/png" in first_chunk


def test_camera_initialize_routes_registers_routes_for_new_app_without_flag_reset(monkeypatch) -> None:
    first_app = _FakeRouteApp()
    second_app = _FakeRouteApp()
    camera = _make_camera_runtime_stub("test.camera.routes.app_switch")
    camera._init_complete.set()

    monkeypatch.setattr(camera_module, "app", first_app)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTES_REGISTERED", False, raising=False)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTE_APP", None, raising=False)
    monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)

    camera.initialize_routes()
    assert [route.path for route in first_app.routes] == ["/video_feed", "/video/frame"]

    monkeypatch.setattr(camera_module, "app", second_app)
    camera.initialize_routes()

    assert [route.path for route in second_app.routes] == ["/video_feed", "/video/frame"]
    assert camera_module._VIDEO_ROUTE_APP is second_app


def test_restore_video_runtime_accepts_non_camera_video_source_stub(monkeypatch) -> None:
    fake_app = _FakeRouteApp()
    video_source = SimpleNamespace(
        placeholder=SimpleNamespace(body=b"stub-camera"),
        get_current_frame=lambda copy_frame=False: None,
        logger=logging.getLogger("test.camera.stub_active"),
    )

    monkeypatch.setattr(camera_module, "app", fake_app)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTES_REGISTERED", False, raising=False)
    monkeypatch.setattr(camera_module, "_VIDEO_ROUTE_APP", None, raising=False)
    monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)

    gui_init._restore_video_runtime(video_source)
    response = camera_module._build_video_frame_response(camera_module._get_active_video_camera())

    assert camera_module._get_active_video_camera() is video_source
    assert [route.path for route in fake_app.routes] == ["/video_feed", "/video/frame"]
    assert bytes(response.body) == b"stub-camera"


def test_init_application_uses_synchronous_camera_startup(monkeypatch) -> None:
    cfg = _create_default_config()
    calls: list[tuple] = []
    measurement_controller = SimpleNamespace(email_system=None, camera=None)
    email_system = SimpleNamespace()

    class _FakeCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = None
            calls.append(("camera_init", async_init, auto_initialize))

        def initialize_sync(self) -> bool:
            calls.append(("initialize_sync",))
            return True

        def initialize_routes(self) -> None:
            calls.append(("initialize_routes",))

        def start_frame_capture(self) -> None:
            calls.append(("start_frame_capture",))

    monkeypatch.setattr(gui_init, "_load_effective_config", lambda path: cfg)
    monkeypatch.setattr(gui_init, "Camera", _FakeCamera)
    monkeypatch.setattr(gui_init, "create_email_system_from_config", lambda config, logger=None: email_system)

    def fake_create_measurement_controller_from_config(*, config, email_system, camera, logger=None):
        measurement_controller.email_system = email_system
        measurement_controller.camera = camera
        return measurement_controller

    monkeypatch.setattr(gui_init, "create_measurement_controller_from_config", fake_create_measurement_controller_from_config)
    instances.set_instances(None, None, None)
    instances.set_startup_report(None)

    report = gui_init.init_application("custom/config.yaml")

    assert report.camera_ok is True
    assert ("camera_init", False, False) in calls
    assert ("initialize_sync",) in calls
    assert ("initialize_routes",) in calls
    assert ("start_frame_capture",) in calls


def test_init_application_keeps_measurement_available_without_camera_or_email(monkeypatch) -> None:
    cfg = _create_default_config()
    cfg.email.recipients = []
    captured_calls: list[tuple] = []

    class _FakeCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = RuntimeError("camera unavailable")
            self.cleaned = False
            captured_calls.append(("camera_init", async_init, auto_initialize))

        def initialize_sync(self) -> bool:
            captured_calls.append(("initialize_sync",))
            return False

        def initialize_routes(self) -> None:
            captured_calls.append(("initialize_routes",))

        def start_frame_capture(self) -> None:
            captured_calls.append(("start_frame_capture",))

        def cleanup(self) -> None:
            self.cleaned = True
            captured_calls.append(("camera_cleanup",))

    measurement_controller = SimpleNamespace(email_system=None, camera=None)

    monkeypatch.setattr(gui_init, "_load_effective_config", lambda path: cfg)
    monkeypatch.setattr(gui_init, "Camera", _FakeCamera)
    monkeypatch.setattr(
        gui_init,
        "create_email_system_from_config",
        lambda config, logger=None: (_ for _ in ()).throw(ValueError("At least one recipient must be configured")),
    )

    def fake_create_measurement_controller_from_config(*, config, email_system, camera, logger=None):
        captured_calls.append(("measurement_init", email_system, camera))
        measurement_controller.email_system = email_system
        measurement_controller.camera = camera
        return measurement_controller

    monkeypatch.setattr(gui_init, "create_measurement_controller_from_config", fake_create_measurement_controller_from_config)
    instances.set_instances(None, None, None)
    instances.set_startup_report(None)

    report = gui_init.init_application("custom/config.yaml")

    assert report.config_ok is True
    assert report.camera_ok is False
    assert "camera unavailable" in (report.camera_error or "")
    assert report.email_ok is False
    assert "recipient" in (report.email_error or "").lower()
    assert report.measurement_ok is True
    assert report.fatal is False
    assert ("camera_init", False, False) in captured_calls
    assert ("initialize_sync",) in captured_calls
    assert ("initialize_routes",) not in captured_calls
    assert ("start_frame_capture",) not in captured_calls
    assert ("camera_cleanup",) in captured_calls
    assert instances.get_camera() is None
    assert instances.get_email_system() is None
    assert instances.get_measurement_controller() is measurement_controller


def test_init_application_preserves_existing_runtime_on_reinit_failure(monkeypatch) -> None:
    old_cfg = _create_default_config()
    new_cfg = _create_default_config()
    previous_config = config_module._global_config
    previous_config_path = config_module._config_path
    old_report = instances.InitializationReport(
        config_ok=True,
        camera_ok=True,
        email_ok=True,
        measurement_ok=True,
    )
    cleanup_calls: list[str] = []
    call_log: list[tuple[str, object | None]] = []
    old_camera = SimpleNamespace(
        cleanup=lambda: cleanup_calls.append("old_camera"),
        initialize_routes=lambda: None,
    )
    old_email = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_email"))
    old_measurement = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_measurement"))

    class _ReplacementCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = None
            call_log.append(("camera_init", async_init, auto_initialize))

        def initialize_sync(self) -> bool:
            call_log.append(("initialize_sync", None))
            return True

        def initialize_routes(self) -> None:
            call_log.append(("initialize_routes", None))

        def start_frame_capture(self) -> None:
            call_log.append(("start_frame_capture", None))

        def cleanup(self) -> None:
            cleanup_calls.append("new_camera")

    new_email = SimpleNamespace(cleanup=lambda: cleanup_calls.append("new_email"))

    def fail_measurement(*, config, email_system, camera, logger=None):
        raise RuntimeError("measurement failed")

    try:
        instances.set_instances(old_camera, old_measurement, old_email)
        instances.set_startup_report(old_report)
        set_global_config(old_cfg, "config/old_runtime.yaml")
        monkeypatch.setattr(gui_init, "Camera", _ReplacementCamera)
        monkeypatch.setattr(gui_init, "load_config", lambda path: new_cfg)
        monkeypatch.setattr(gui_init, "create_email_system_from_config", lambda config, logger=None: new_email)
        monkeypatch.setattr(gui_init, "create_measurement_controller_from_config", fail_measurement)

        report = gui_init.init_application("config/replacement_runtime.yaml")

        assert report.measurement_error == "measurement failed"
        assert instances.get_camera() is old_camera
        assert instances.get_email_system() is old_email
        assert instances.get_measurement_controller() is old_measurement
        assert instances.get_startup_report() is old_report
        assert cleanup_calls == ["new_email", "new_camera"]
        assert ("initialize_routes", None) not in call_log
        assert ("start_frame_capture", None) not in call_log
        assert config_module.get_global_config() is old_cfg
        assert config_module.get_global_config_path() == str(config_module._resolve_config_path("config/old_runtime.yaml"))
    finally:
        instances.set_instances(None, None, None)
        instances.set_startup_report(None)
        _restore_config_registry_state(monkeypatch, previous_config, previous_config_path)


def test_init_application_suspends_existing_camera_before_replacement_camera_initializes(monkeypatch) -> None:
    old_cfg = _create_default_config()
    new_cfg = _create_default_config()
    previous_config = config_module._global_config
    previous_config_path = config_module._config_path
    old_report = instances.InitializationReport(
        config_ok=True,
        camera_ok=True,
        email_ok=True,
        measurement_ok=True,
    )
    cleanup_calls: list[str] = []
    call_log: list[tuple[str, object | None]] = []
    old_camera = SimpleNamespace(
        suspend_runtime=lambda: call_log.append(("suspend_old_camera", None)) or True,
        initialize_sync=lambda: call_log.append(("resume_old_camera", None)) or True,
        initialize_routes=lambda: call_log.append(("restore_old_routes", None)),
        cleanup=lambda: cleanup_calls.append("old_camera"),
    )
    old_email = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_email"))
    old_measurement = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_measurement"))

    class _ReplacementCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = None
            call_log.append(("camera_init", async_init, auto_initialize))

        def initialize_sync(self) -> bool:
            call_log.append(("initialize_new_camera", None))
            return True

        def initialize_routes(self) -> None:
            call_log.append(("initialize_new_routes", None))

        def start_frame_capture(self) -> None:
            call_log.append(("start_new_capture", None))

        def cleanup(self) -> None:
            cleanup_calls.append("new_camera")

    new_email = SimpleNamespace(cleanup=lambda: cleanup_calls.append("new_email"))
    new_measurement = SimpleNamespace(cleanup=lambda: cleanup_calls.append("new_measurement"))

    try:
        instances.set_instances(old_camera, old_measurement, old_email)
        instances.set_startup_report(old_report)
        set_global_config(old_cfg, "config/old_handover_runtime.yaml")
        monkeypatch.setattr(gui_init, "Camera", _ReplacementCamera)
        monkeypatch.setattr(gui_init, "load_config", lambda path: new_cfg)
        monkeypatch.setattr(gui_init, "create_email_system_from_config", lambda config, logger=None: new_email)
        monkeypatch.setattr(
            gui_init,
            "create_measurement_controller_from_config",
            lambda *, config, email_system, camera, logger=None: new_measurement,
        )

        report = gui_init.init_application("config/new_handover_runtime.yaml")

        assert report.degraded is False
        assert call_log.index(("suspend_old_camera", None)) < call_log.index(("initialize_new_camera", None))
        assert ("resume_old_camera", None) not in call_log
        assert cleanup_calls == ["old_measurement", "old_email", "old_camera"]
    finally:
        instances.set_instances(None, None, None)
        instances.set_startup_report(None)
        _restore_config_registry_state(monkeypatch, previous_config, previous_config_path)


def test_init_application_resumes_suspended_camera_when_replacement_camera_fails(monkeypatch) -> None:
    cfg = _create_default_config()
    previous_config = config_module._global_config
    previous_config_path = config_module._config_path
    old_report = instances.InitializationReport(
        config_ok=True,
        camera_ok=True,
        email_ok=True,
        measurement_ok=True,
    )
    cleanup_calls: list[str] = []
    call_log: list[tuple[str, object | None]] = []
    old_camera = SimpleNamespace(
        placeholder=SimpleNamespace(body=b"old-camera"),
        get_current_frame=lambda copy_frame=False: None,
        logger=logging.getLogger("test.camera.old_active_resume"),
        suspend_runtime=lambda: call_log.append(("suspend_old_camera", None)) or True,
        initialize_sync=lambda: call_log.append(("resume_old_camera", None)) or True,
        initialize_routes=lambda: call_log.append(("restore_old_routes", None)),
        cleanup=lambda: cleanup_calls.append("old_camera"),
    )
    old_email = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_email"))
    old_measurement = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_measurement"))

    class _FailingCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = RuntimeError("camera unavailable")
            call_log.append(("camera_init", async_init, auto_initialize))

        def initialize_sync(self) -> bool:
            call_log.append(("initialize_new_camera", None))
            return False

        def cleanup(self) -> None:
            cleanup_calls.append("new_camera")

    try:
        instances.set_instances(old_camera, old_measurement, old_email)
        instances.set_startup_report(old_report)
        set_global_config(cfg, "config/old_resume_runtime.yaml")
        monkeypatch.setattr(gui_init, "Camera", _FailingCamera)
        monkeypatch.setattr(gui_init, "load_config", lambda path: cfg)
        monkeypatch.setattr(gui_init, "create_email_system_from_config", lambda config, logger=None: old_email)
        monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", old_camera, raising=False)

        report = gui_init.init_application("config/resume_runtime.yaml")
        response = camera_module._build_video_frame_response(camera_module._get_active_video_camera())

        assert report.camera_error == "camera unavailable"
        assert instances.get_camera() is old_camera
        assert call_log.index(("suspend_old_camera", None)) < call_log.index(("initialize_new_camera", None))
        assert call_log.index(("initialize_new_camera", None)) < call_log.index(("resume_old_camera", None))
        assert ("restore_old_routes", None) in call_log
        assert bytes(response.body) == b"old-camera"
        assert cleanup_calls == ["new_camera"]
    finally:
        instances.set_instances(None, None, None)
        instances.set_startup_report(None)
        monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)
        _restore_config_registry_state(monkeypatch, previous_config, previous_config_path)


def test_init_application_commits_replacement_runtime_when_email_init_fails(monkeypatch) -> None:
    old_cfg = _create_default_config()
    new_cfg = _create_default_config()
    previous_config = config_module._global_config
    previous_config_path = config_module._config_path
    old_report = instances.InitializationReport(
        config_ok=True,
        camera_ok=True,
        email_ok=True,
        measurement_ok=True,
    )
    cleanup_calls: list[str] = []
    call_log: list[tuple[str, object | None]] = []
    old_camera = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_camera"))
    old_email = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_email"))
    old_measurement = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_measurement"))

    class _ReplacementCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = None
            call_log.append(("camera_init", async_init, auto_initialize))

        def initialize_sync(self) -> bool:
            call_log.append(("initialize_sync", None))
            return True

        def initialize_routes(self) -> None:
            call_log.append(("initialize_routes", None))

        def start_frame_capture(self) -> None:
            call_log.append(("start_frame_capture", None))

        def cleanup(self) -> None:
            cleanup_calls.append("new_camera")

    new_measurement = SimpleNamespace(cleanup=lambda: cleanup_calls.append("new_measurement"))

    def fake_create_measurement_controller_from_config(*, config, email_system, camera, logger=None):
        call_log.append(("measurement_init", email_system))
        return new_measurement

    try:
        instances.set_instances(old_camera, old_measurement, old_email)
        instances.set_startup_report(old_report)
        set_global_config(old_cfg, "config/old_email_runtime.yaml")
        monkeypatch.setattr(gui_init, "Camera", _ReplacementCamera)
        monkeypatch.setattr(gui_init, "load_config", lambda path: new_cfg)
        monkeypatch.setattr(
            gui_init,
            "create_email_system_from_config",
            lambda config, logger=None: (_ for _ in ()).throw(RuntimeError("email failed")),
        )
        monkeypatch.setattr(gui_init, "create_measurement_controller_from_config", fake_create_measurement_controller_from_config)

        report = gui_init.init_application("config/new_email_runtime.yaml")

        assert report.email_ok is False
        assert report.email_error == "email failed"
        assert report.measurement_ok is True
        assert report.fatal is False
        assert report.degraded is True
        assert isinstance(instances.get_camera(), _ReplacementCamera)
        assert instances.get_email_system() is None
        assert instances.get_measurement_controller() is new_measurement
        assert instances.get_startup_report() is report
        assert config_module.get_global_config() is new_cfg
        assert config_module.get_global_config_path() == str(config_module._resolve_config_path("config/new_email_runtime.yaml"))
        assert ("measurement_init", None) in call_log
        assert ("initialize_routes", None) in call_log
        assert ("start_frame_capture", None) in call_log
        assert cleanup_calls == ["old_measurement", "old_email", "old_camera"]
    finally:
        instances.set_instances(None, None, None)
        instances.set_startup_report(None)
        _restore_config_registry_state(monkeypatch, previous_config, previous_config_path)


def test_init_application_preserves_active_video_camera_on_failed_reinit(monkeypatch) -> None:
    cfg = _create_default_config()
    previous_config = config_module._global_config
    previous_config_path = config_module._config_path
    old_report = instances.InitializationReport(
        config_ok=True,
        camera_ok=True,
        email_ok=True,
        measurement_ok=True,
    )
    cleanup_calls: list[str] = []
    old_camera = SimpleNamespace(
        placeholder=SimpleNamespace(body=b"old-camera"),
        get_current_frame=lambda copy_frame=False: None,
        logger=logging.getLogger("test.camera.old_active"),
        cleanup=lambda: cleanup_calls.append("old_camera"),
        initialize_routes=lambda: None,
    )
    old_email = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_email"))
    old_measurement = SimpleNamespace(cleanup=lambda: cleanup_calls.append("old_measurement"))

    class _FailingCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = RuntimeError("camera unavailable")

        def initialize_sync(self) -> bool:
            return False

        def cleanup(self) -> None:
            cleanup_calls.append("new_camera")

    try:
        instances.set_instances(old_camera, old_measurement, old_email)
        instances.set_startup_report(old_report)
        set_global_config(cfg, "config/active_runtime.yaml")
        monkeypatch.setattr(gui_init, "Camera", _FailingCamera)
        monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", old_camera, raising=False)

        report = gui_init.init_application("config/active_runtime.yaml")
        response = camera_module._build_video_frame_response(camera_module._get_active_video_camera())

        assert report.camera_error == "camera unavailable"
        assert instances.get_camera() is old_camera
        assert instances.get_startup_report() is old_report
        assert camera_module._get_active_video_camera() is old_camera
        assert bytes(response.body) == b"old-camera"
        assert cleanup_calls == ["new_camera"]
    finally:
        instances.set_instances(None, None, None)
        instances.set_startup_report(None)
        monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)
        _restore_config_registry_state(monkeypatch, previous_config, previous_config_path)


def test_init_application_commits_new_runtime_and_cleans_previous_runtime_in_order(monkeypatch) -> None:
    old_cfg = _create_default_config()
    new_cfg = _create_default_config()
    previous_config = config_module._global_config
    previous_config_path = config_module._config_path
    old_report = instances.InitializationReport(
        config_ok=True,
        camera_ok=True,
        email_ok=True,
        measurement_ok=True,
    )
    call_log: list[tuple[str, object | None]] = []
    old_camera = SimpleNamespace(cleanup=lambda: call_log.append(("cleanup_old_camera", None)))
    old_email = SimpleNamespace(cleanup=lambda: call_log.append(("cleanup_old_email", None)))
    old_measurement = SimpleNamespace(cleanup=lambda: call_log.append(("cleanup_old_measurement", None)))

    class _ReplacementCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = None
            call_log.append(("camera_init", async_init, auto_initialize))

        def initialize_sync(self) -> bool:
            call_log.append(("initialize_sync", None))
            return True

        def initialize_routes(self) -> None:
            call_log.append(("initialize_routes", None))

        def start_frame_capture(self) -> None:
            call_log.append(("start_frame_capture", None))

        def cleanup(self) -> None:
            call_log.append(("cleanup_new_camera", None))

    new_email = SimpleNamespace(cleanup=lambda: call_log.append(("cleanup_new_email", None)))
    new_measurement = SimpleNamespace(cleanup=lambda: call_log.append(("cleanup_new_measurement", None)))

    try:
        instances.set_instances(old_camera, old_measurement, old_email)
        instances.set_startup_report(old_report)
        set_global_config(old_cfg, "config/old_success.yaml")
        monkeypatch.setattr(gui_init, "Camera", _ReplacementCamera)
        monkeypatch.setattr(gui_init, "load_config", lambda path: new_cfg)
        monkeypatch.setattr(gui_init, "create_email_system_from_config", lambda config, logger=None: new_email)
        monkeypatch.setattr(
            gui_init,
            "create_measurement_controller_from_config",
            lambda *, config, email_system, camera, logger=None: new_measurement,
        )

        report = gui_init.init_application("config/new_success.yaml")

        assert report.degraded is False
        assert isinstance(instances.get_camera(), _ReplacementCamera)
        assert instances.get_email_system() is new_email
        assert instances.get_measurement_controller() is new_measurement
        assert instances.get_startup_report() is report
        assert config_module.get_global_config() is new_cfg
        assert config_module.get_global_config_path() == str(config_module._resolve_config_path("config/new_success.yaml"))
        assert call_log == [
            ("camera_init", False, False),
            ("initialize_sync", None),
            ("initialize_routes", None),
            ("start_frame_capture", None),
            ("cleanup_old_measurement", None),
            ("cleanup_old_email", None),
            ("cleanup_old_camera", None),
        ]
    finally:
        instances.set_instances(None, None, None)
        instances.set_startup_report(None)
        _restore_config_registry_state(monkeypatch, previous_config, previous_config_path)


def test_init_application_cold_start_camera_failure_clears_active_video_camera(monkeypatch) -> None:
    cfg = _create_default_config()
    stale_camera = SimpleNamespace(
        placeholder=SimpleNamespace(body=b"stale-camera"),
        get_current_frame=lambda copy_frame=False: None,
        logger=logging.getLogger("test.camera.stale_active"),
    )
    cleanup_calls: list[str] = []

    class _FailingCamera:
        def __init__(self, config, logger=None, async_init: bool = True, auto_initialize: bool = True):
            self.initialization_error = RuntimeError("camera unavailable")

        def initialize_sync(self) -> bool:
            return False

        def cleanup(self) -> None:
            cleanup_calls.append("new_camera")

    measurement_controller = SimpleNamespace(cleanup=lambda: cleanup_calls.append("measurement"))
    email_system = SimpleNamespace(cleanup=lambda: cleanup_calls.append("email"))

    try:
        instances.set_instances(None, None, None)
        instances.set_startup_report(None)
        monkeypatch.setattr(gui_init, "_load_effective_config", lambda path: cfg)
        monkeypatch.setattr(gui_init, "Camera", _FailingCamera)
        monkeypatch.setattr(gui_init, "create_email_system_from_config", lambda config, logger=None: email_system)
        monkeypatch.setattr(
            gui_init,
            "create_measurement_controller_from_config",
            lambda *, config, email_system, camera, logger=None: measurement_controller,
        )
        monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", stale_camera, raising=False)

        report = gui_init.init_application("config/cold_start.yaml")
        response = camera_module._build_video_frame_response(camera_module._get_active_video_camera())

        assert report.camera_ok is False
        assert instances.get_camera() is None
        assert instances.get_measurement_controller() is measurement_controller
        assert instances.get_email_system() is email_system
        assert camera_module._get_active_video_camera() is None
        assert bytes(response.body) == camera_module._DEFAULT_VIDEO_PLACEHOLDER_BODY
        assert cleanup_calls == ["new_camera"]
    finally:
        instances.set_instances(None, None, None)
        instances.set_startup_report(None)
        monkeypatch.setattr(camera_module, "_ACTIVE_VIDEO_CAMERA", None, raising=False)


def test_load_config_uses_default_config_when_file_is_missing() -> None:
    temp_path = Path(".pytest_startup_runtime_missing")
    temp_path.mkdir(exist_ok=True)
    missing_path = temp_path / "missing-config.yaml"

    try:
        cfg = load_config(str(missing_path))

        assert cfg.metadata.cvd_name == _create_default_config().metadata.cvd_name
    finally:
        _cleanup_local_temp_dir(temp_path, missing_path)


def test_load_config_raises_for_invalid_yaml() -> None:
    temp_path = Path(".pytest_startup_runtime_invalid_yaml")
    temp_path.mkdir(exist_ok=True)
    config_path = temp_path / "invalid.yaml"
    config_path.write_text("email: [broken\n", encoding="utf-8")

    try:
        with pytest.raises(ConfigLoadError, match="YAML parsing error"):
            load_config(str(config_path))
    finally:
        _cleanup_local_temp_dir(temp_path, config_path)
