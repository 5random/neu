import logging
import logging.handlers
from pathlib import Path
import threading
import time

import pytest

from src import config as config_module
from src.config import LoggingConfig, _reset_configured_logger


def _cleanup_logging_test_dir(temp_path: Path, *, logger_name: str, paths: list[Path]) -> None:
    _reset_configured_logger(logger_name)
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


def _configured_signature(cfg: LoggingConfig) -> tuple[str, str, int, int, bool]:
    return (
        str(cfg.level).upper(),
        str(config_module._resolve_config_path(cfg.file)),
        int(cfg.max_file_size_mb),
        int(cfg.backup_count),
        bool(cfg.console_output),
    )


def _get_rotating_file_handlers(logger: logging.Logger) -> list[logging.handlers.RotatingFileHandler]:
    return [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    ]


def _get_console_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
    ]


class _RecordingHandler(logging.Handler):
    def __init__(self, *, emit_delay_seconds: float = 0.0) -> None:
        super().__init__()
        self.emit_delay_seconds = emit_delay_seconds
        self.messages: list[str] = []
        self.close_calls = 0

    def emit(self, record: logging.LogRecord) -> None:
        if self.emit_delay_seconds > 0:
            time.sleep(self.emit_delay_seconds)
        self.messages.append(record.getMessage())

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def test_setup_logger_runs_once() -> None:
    logger_name = "test_logger"
    temp_path = Path(".pytest_logging_setup_once")
    temp_path.mkdir(exist_ok=True)
    log_file = temp_path / "app.log"

    try:
        config_module._configured_loggers.clear()
        cfg = LoggingConfig(level="INFO", file=str(log_file))
        logger = cfg.setup_logger(logger_name)
        first_count = len(logger.handlers)

        cfg.setup_logger(logger_name)
        second_count = len(logger.handlers)

        assert second_count == first_count
    finally:
        _cleanup_logging_test_dir(temp_path, logger_name=logger_name, paths=[log_file])


def test_setup_logger_reconfigures_when_target_file_changes() -> None:
    logger_name = "test_logger_reconfigure"
    temp_path = Path(".pytest_logging_setup_reconfigure")
    temp_path.mkdir(exist_ok=True)
    first_log = temp_path / "first.log"
    second_log = temp_path / "second.log"

    try:
        config_module._configured_loggers.clear()
        first_cfg = LoggingConfig(level="INFO", file=str(first_log))
        logger = first_cfg.setup_logger(logger_name)
        first_handler = next(handler for handler in logger.handlers if hasattr(handler, "baseFilename"))

        second_cfg = LoggingConfig(level="DEBUG", file=str(second_log))
        reconfigured_logger = second_cfg.setup_logger(logger_name)
        second_handler = next(handler for handler in reconfigured_logger.handlers if hasattr(handler, "baseFilename"))

        assert Path(first_handler.baseFilename).resolve() == first_log.resolve()
        assert Path(second_handler.baseFilename).resolve() == second_log.resolve()
        assert reconfigured_logger.level == getattr(logging, second_cfg.level.upper())
    finally:
        _cleanup_logging_test_dir(
            temp_path,
            logger_name=logger_name,
            paths=[first_log, second_log],
        )


def test_setup_logger_replaces_handler_list_via_copy_swap(monkeypatch) -> None:
    logger_name = "test_logger_copy_swap"
    temp_path = Path(".pytest_logging_setup_copy_swap")
    temp_path.mkdir(exist_ok=True)
    base_cfg = LoggingConfig(level="INFO", file=str(temp_path / "base.log"), console_output=False)
    new_cfg = LoggingConfig(level="DEBUG", file=str(temp_path / "new.log"), console_output=False)
    base_handler = _RecordingHandler()
    new_handler = _RecordingHandler()
    handler_map = {
        base_cfg.file: [base_handler],
        new_cfg.file: [new_handler],
    }

    def fake_build_handlers(self) -> list[logging.Handler]:
        return list(handler_map[self.file])

    monkeypatch.setattr(LoggingConfig, "_build_handlers", fake_build_handlers)

    try:
        config_module._configured_loggers.clear()
        logger = base_cfg.setup_logger(logger_name)
        published_handler_list = logger.handlers
        published_handlers_snapshot = tuple(published_handler_list)

        reconfigured_logger = new_cfg.setup_logger(logger_name)

        assert reconfigured_logger.handlers is not published_handler_list
        assert tuple(published_handler_list) == published_handlers_snapshot
        assert reconfigured_logger.handlers == [new_handler]
        assert len({id(handler) for handler in reconfigured_logger.handlers}) == 1
        assert config_module._configured_loggers[logger_name] == _configured_signature(new_cfg)
    finally:
        _reset_configured_logger(logger_name)
        try:
            temp_path.rmdir()
        except OSError:
            pass


def test_setup_logger_retires_old_handlers_after_grace_period(monkeypatch) -> None:
    logger_name = "test_logger_handler_retirement"
    temp_path = Path(".pytest_logging_setup_retirement")
    temp_path.mkdir(exist_ok=True)
    base_cfg = LoggingConfig(level="INFO", file=str(temp_path / "base.log"), console_output=False)
    new_cfg = LoggingConfig(level="DEBUG", file=str(temp_path / "new.log"), console_output=False)
    base_handler = _RecordingHandler()
    new_handler = _RecordingHandler()
    handler_map = {
        base_cfg.file: [base_handler],
        new_cfg.file: [new_handler],
    }
    created_timers: list[object] = []

    class FakeTimer:
        def __init__(self, interval, function, args=None, kwargs=None) -> None:
            self.interval = interval
            self.function = function
            self.args = args or ()
            self.kwargs = kwargs or {}
            self.daemon = False
            self.started = False
            self.cancelled = False
            created_timers.append(self)

        def start(self) -> None:
            self.started = True

        def cancel(self) -> None:
            self.cancelled = True

        def fire(self) -> None:
            if not self.cancelled:
                self.function(*self.args, **self.kwargs)

    def fake_build_handlers(self) -> list[logging.Handler]:
        return list(handler_map[self.file])

    monkeypatch.setattr(LoggingConfig, "_build_handlers", fake_build_handlers)
    monkeypatch.setattr(config_module.threading, "Timer", FakeTimer)
    monkeypatch.setattr(config_module, "_LOGGER_HANDLER_RETIRE_GRACE_SECONDS", 0.01)

    try:
        config_module._configured_loggers.clear()
        logger = base_cfg.setup_logger(logger_name)
        assert logger.handlers == [base_handler]

        reconfigured_logger = new_cfg.setup_logger(logger_name)

        assert reconfigured_logger.handlers == [new_handler]
        assert base_handler.close_calls == 0
        assert len(created_timers) == 1
        timer = created_timers[0]
        assert timer.started is True
        assert timer.daemon is True

        timer.fire()

        assert base_handler.close_calls == 1
        assert new_handler.close_calls == 0
    finally:
        _reset_configured_logger(logger_name)
        try:
            temp_path.rmdir()
        except OSError:
            pass


def test_setup_logger_restores_previous_state_when_activation_fails(monkeypatch) -> None:
    logger_name = "test_logger_activation_rollback"
    temp_path = Path(".pytest_logging_setup_activation_rollback")
    temp_path.mkdir(exist_ok=True)
    base_log = temp_path / "base.log"
    new_log = temp_path / "new.log"
    base_cfg = LoggingConfig(level="INFO", file=str(base_log), console_output=False)
    new_cfg = LoggingConfig(level="DEBUG", file=str(new_log), console_output=True)

    try:
        config_module._configured_loggers.clear()
        logger = base_cfg.setup_logger(logger_name)
        base_handler = next(handler for handler in logger.handlers if hasattr(handler, "baseFilename"))
        original_activate = LoggingConfig._activate_logger_configuration

        def fail_after_activation(self, target_logger, *, handlers) -> None:
            original_activate(self, target_logger, handlers=handlers)
            raise RuntimeError("simulated activation failure")

        monkeypatch.setattr(LoggingConfig, "_activate_logger_configuration", fail_after_activation)

        with pytest.raises(RuntimeError, match="simulated activation failure"):
            new_cfg.setup_logger(logger_name)

        current_file_handlers = _get_rotating_file_handlers(logger)
        assert len(current_file_handlers) == 1
        assert current_file_handlers[0] is base_handler
        assert Path(current_file_handlers[0].baseFilename).resolve() == base_log.resolve()
        assert len(_get_console_handlers(logger)) == 0
        assert logger.level == getattr(logging, base_cfg.level.upper())
        assert logger.propagate is False
        assert config_module._configured_loggers[logger_name] == _configured_signature(base_cfg)
    finally:
        _cleanup_logging_test_dir(
            temp_path,
            logger_name=logger_name,
            paths=[base_log, new_log],
        )


def test_setup_logger_serializes_parallel_configuration(monkeypatch) -> None:
    logger_name = "test_logger_parallel_same_config"
    temp_path = Path(".pytest_logging_setup_parallel_same")
    temp_path.mkdir(exist_ok=True)
    log_file = temp_path / "parallel.log"
    cfg = LoggingConfig(level="INFO", file=str(log_file))
    original_set_level = logging.Logger.setLevel
    concurrency_lock = threading.Lock()
    state = {"inflight": 0, "max_inflight": 0}
    errors: list[BaseException] = []

    def instrumented_set_level(self, level) -> None:
        if self.name == logger_name:
            with concurrency_lock:
                state["inflight"] += 1
                state["max_inflight"] = max(state["max_inflight"], state["inflight"])
            time.sleep(0.05)
            try:
                original_set_level(self, level)
            finally:
                with concurrency_lock:
                    state["inflight"] -= 1
            return
        original_set_level(self, level)

    def worker(start_barrier: threading.Barrier) -> None:
        try:
            start_barrier.wait(timeout=1.0)
            cfg.setup_logger(logger_name)
        except BaseException as exc:
            errors.append(exc)

    try:
        with config_module._configured_loggers_lock:
            config_module._configured_loggers.clear()
        monkeypatch.setattr(logging.Logger, "setLevel", instrumented_set_level)
        start_barrier = threading.Barrier(2)
        threads = [
            threading.Thread(target=worker, args=(start_barrier,)),
            threading.Thread(target=worker, args=(start_barrier,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)
            assert not thread.is_alive()

        assert errors == []
        logger = logging.getLogger(logger_name)
        assert state["max_inflight"] == 1
        assert len(_get_rotating_file_handlers(logger)) == 1
        assert len(_get_console_handlers(logger)) == 1
        assert len(logger.handlers) == 2
        assert config_module._configured_loggers[logger_name] == _configured_signature(cfg)
    finally:
        _cleanup_logging_test_dir(temp_path, logger_name=logger_name, paths=[log_file])


def test_setup_logger_parallel_reconfigure_keeps_final_state_consistent(monkeypatch) -> None:
    logger_name = "test_logger_parallel_reconfigure"
    temp_path = Path(".pytest_logging_setup_parallel_reconfigure")
    temp_path.mkdir(exist_ok=True)
    base_log = temp_path / "base.log"
    first_log = temp_path / "first.log"
    second_log = temp_path / "second.log"
    base_cfg = LoggingConfig(level="WARNING", file=str(base_log), console_output=True)
    first_cfg = LoggingConfig(level="INFO", file=str(first_log), console_output=True)
    second_cfg = LoggingConfig(level="DEBUG", file=str(second_log), console_output=False)
    original_set_level = logging.Logger.setLevel
    concurrency_lock = threading.Lock()
    state = {"inflight": 0, "max_inflight": 0}
    errors: list[BaseException] = []

    def instrumented_set_level(self, level) -> None:
        if self.name == logger_name:
            with concurrency_lock:
                state["inflight"] += 1
                state["max_inflight"] = max(state["max_inflight"], state["inflight"])
            time.sleep(0.05)
            try:
                original_set_level(self, level)
            finally:
                with concurrency_lock:
                    state["inflight"] -= 1
            return
        original_set_level(self, level)

    def worker(cfg: LoggingConfig, start_barrier: threading.Barrier) -> None:
        try:
            start_barrier.wait(timeout=1.0)
            cfg.setup_logger(logger_name)
        except BaseException as exc:
            errors.append(exc)

    try:
        with config_module._configured_loggers_lock:
            config_module._configured_loggers.clear()
        base_cfg.setup_logger(logger_name)
        monkeypatch.setattr(logging.Logger, "setLevel", instrumented_set_level)
        start_barrier = threading.Barrier(2)
        threads = [
            threading.Thread(target=worker, args=(first_cfg, start_barrier)),
            threading.Thread(target=worker, args=(second_cfg, start_barrier)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)
            assert not thread.is_alive()

        assert errors == []
        logger = logging.getLogger(logger_name)
        signature = config_module._configured_loggers[logger_name]
        expected_level, expected_file, expected_max_size_mb, expected_backup_count, expected_console = signature
        file_handlers = _get_rotating_file_handlers(logger)
        console_handlers = _get_console_handlers(logger)

        assert state["max_inflight"] == 1
        assert len(file_handlers) == 1
        assert len(console_handlers) == int(expected_console)
        assert len(logger.handlers) == 1 + int(expected_console)
        assert Path(file_handlers[0].baseFilename).resolve() == Path(expected_file).resolve()
        assert file_handlers[0].maxBytes == int(expected_max_size_mb) * 1024 * 1024
        assert file_handlers[0].backupCount == int(expected_backup_count)
        assert logger.level == getattr(logging, str(expected_level).upper())
        assert logger.propagate is False
    finally:
        _cleanup_logging_test_dir(
            temp_path,
            logger_name=logger_name,
            paths=[base_log, first_log, second_log],
        )


def test_setup_logger_concurrent_logging_and_reconfigure_keeps_final_state_consistent(monkeypatch) -> None:
    logger_name = "test_logger_concurrent_logging_reconfigure"
    temp_path = Path(".pytest_logging_setup_concurrent_logging")
    temp_path.mkdir(exist_ok=True)
    base_cfg = LoggingConfig(level="INFO", file=str(temp_path / "base.log"), console_output=False)
    new_cfg = LoggingConfig(level="DEBUG", file=str(temp_path / "new.log"), console_output=False)
    base_handler = _RecordingHandler(emit_delay_seconds=0.001)
    new_handler = _RecordingHandler()
    handler_map = {
        base_cfg.file: [base_handler],
        new_cfg.file: [new_handler],
    }
    errors: list[BaseException] = []

    def fake_build_handlers(self) -> list[logging.Handler]:
        return list(handler_map[self.file])

    def logging_worker(target_logger: logging.Logger, ready: threading.Event) -> None:
        try:
            ready.set()
            for index in range(120):
                target_logger.info("message-%s", index)
        except BaseException as exc:
            errors.append(exc)

    def reconfigure_worker() -> None:
        try:
            new_cfg.setup_logger(logger_name)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(LoggingConfig, "_build_handlers", fake_build_handlers)
    monkeypatch.setattr(config_module, "_LOGGER_HANDLER_RETIRE_GRACE_SECONDS", 0.01)

    try:
        config_module._configured_loggers.clear()
        logger = base_cfg.setup_logger(logger_name)
        ready = threading.Event()
        log_thread = threading.Thread(target=logging_worker, args=(logger, ready))
        log_thread.start()
        assert ready.wait(timeout=1.0)
        time.sleep(0.01)

        reconfigure_thread = threading.Thread(target=reconfigure_worker)
        reconfigure_thread.start()

        log_thread.join(timeout=2.0)
        reconfigure_thread.join(timeout=2.0)

        assert not log_thread.is_alive()
        assert not reconfigure_thread.is_alive()
        assert errors == []
        assert logger.handlers == [new_handler]
        assert logger.level == getattr(logging, new_cfg.level.upper())
        assert logger.propagate is False
        assert config_module._configured_loggers[logger_name] == _configured_signature(new_cfg)
    finally:
        _reset_configured_logger(logger_name)
        try:
            temp_path.rmdir()
        except OSError:
            pass


def test_get_fallback_main_logger_adds_single_marked_handler_under_parallel_calls(monkeypatch) -> None:
    original_add_handler = logging.Logger.addHandler
    concurrency_lock = threading.Lock()
    state = {"inflight": 0, "max_inflight": 0}
    errors: list[BaseException] = []

    def instrumented_add_handler(self, handler) -> None:
        if self.name == "cvd_tracker":
            with concurrency_lock:
                state["inflight"] += 1
                state["max_inflight"] = max(state["max_inflight"], state["inflight"])
            time.sleep(0.05)
            try:
                original_add_handler(self, handler)
            finally:
                with concurrency_lock:
                    state["inflight"] -= 1
            return
        original_add_handler(self, handler)

    def worker(start_barrier: threading.Barrier) -> None:
        try:
            start_barrier.wait(timeout=1.0)
            config_module._get_fallback_main_logger()
        except BaseException as exc:
            errors.append(exc)

    _reset_configured_logger("cvd_tracker")
    monkeypatch.setattr(logging.Logger, "addHandler", instrumented_add_handler)

    try:
        start_barrier = threading.Barrier(3)
        threads = [
            threading.Thread(target=worker, args=(start_barrier,)),
            threading.Thread(target=worker, args=(start_barrier,)),
            threading.Thread(target=worker, args=(start_barrier,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)
            assert not thread.is_alive()

        assert errors == []
        fallback_logger = logging.getLogger("cvd_tracker")
        fallback_handlers = [
            handler
            for handler in fallback_logger.handlers
            if getattr(handler, config_module._FALLBACK_HANDLER_MARKER_ATTR, False)
        ]
        assert state["max_inflight"] == 1
        assert len(fallback_handlers) == 1
    finally:
        _reset_configured_logger("cvd_tracker")


def test_reset_configured_logger_clears_handlers_and_registry() -> None:
    logger_name = "test_logger_reset"
    temp_path = Path(".pytest_logging_setup_reset")
    temp_path.mkdir(exist_ok=True)
    log_file = temp_path / "reset.log"

    try:
        cfg = LoggingConfig(level="INFO", file=str(log_file))
        logger = cfg.setup_logger(logger_name)
        assert logger.handlers
        assert logger_name in config_module._configured_loggers

        _reset_configured_logger(logger_name)

        assert logger.handlers == []
        assert logger_name not in config_module._configured_loggers
    finally:
        _cleanup_logging_test_dir(temp_path, logger_name=logger_name, paths=[log_file])


def test_setup_logger_resolves_relative_log_file_against_project_root(monkeypatch) -> None:
    logger_name = "test_logger_relative_path"
    temp_path = Path(".pytest_logging_setup_relative_path")
    temp_path.mkdir(exist_ok=True)
    cwd_path = temp_path / "cwd"
    cwd_path.mkdir(exist_ok=True)
    relative_log_path = ".pytest_logging_setup_relative_path/logs/app.log"
    resolved_log_path = config_module._resolve_config_path(relative_log_path)
    cfg = LoggingConfig(level="INFO", file=relative_log_path)

    try:
        monkeypatch.chdir(cwd_path.resolve())
        logger = cfg.setup_logger(logger_name)
        file_handler = next(handler for handler in logger.handlers if hasattr(handler, "baseFilename"))

        assert Path(file_handler.baseFilename).resolve() == resolved_log_path.resolve()
        assert config_module._configured_loggers[logger_name] == _configured_signature(cfg)
    finally:
        _cleanup_logging_test_dir(
            temp_path,
            logger_name=logger_name,
            paths=[resolved_log_path, cwd_path],
        )
        try:
            resolved_log_path.parent.rmdir()
        except OSError:
            pass
        try:
            temp_path.rmdir()
        except OSError:
            pass
