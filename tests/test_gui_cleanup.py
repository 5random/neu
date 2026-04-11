import threading
from types import SimpleNamespace

from src.gui import cleanup as gui_cleanup


def test_cleanup_application_runs_sync_cleanup_once_and_marks_completion(monkeypatch) -> None:
    cleanup_calls: list[str] = []

    monkeypatch.setattr(gui_cleanup, "_shutdown_requested", threading.Event())
    monkeypatch.setattr(gui_cleanup, "_cleanup_completed", threading.Event())
    monkeypatch.setattr(gui_cleanup, "_shutdown_lock", threading.Lock())
    monkeypatch.setattr(
        gui_cleanup,
        "cleanup_application_sync",
        lambda: cleanup_calls.append("cleanup_sync"),
    )

    gui_cleanup.cleanup_application()
    gui_cleanup.cleanup_application()

    assert cleanup_calls == ["cleanup_sync"]
    assert gui_cleanup._cleanup_completed.is_set() is True


def test_signal_handler_runs_cleanup_before_shutdown(monkeypatch) -> None:
    call_order: list[object] = []

    monkeypatch.setattr(
        gui_cleanup,
        "cleanup_application",
        lambda *, wait_if_already_running=False: call_order.append(("cleanup", wait_if_already_running)),
    )
    monkeypatch.setattr(gui_cleanup, "app", SimpleNamespace(shutdown=lambda: call_order.append("shutdown")))

    gui_cleanup.signal_handler(15, None)

    assert call_order == [("cleanup", True), "shutdown"]


def test_signal_handler_uses_cleanup_idempotence_for_repeated_signals(monkeypatch) -> None:
    cleanup_calls: list[str] = []
    shutdown_calls: list[str] = []

    monkeypatch.setattr(gui_cleanup, "_shutdown_requested", threading.Event())
    monkeypatch.setattr(gui_cleanup, "_cleanup_completed", threading.Event())
    monkeypatch.setattr(gui_cleanup, "_shutdown_lock", threading.Lock())
    monkeypatch.setattr(
        gui_cleanup,
        "cleanup_application_sync",
        lambda: cleanup_calls.append("cleanup_sync"),
    )
    monkeypatch.setattr(gui_cleanup, "app", SimpleNamespace(shutdown=lambda: shutdown_calls.append("shutdown")))

    gui_cleanup.signal_handler(15, None)
    gui_cleanup.signal_handler(15, None)

    assert cleanup_calls == ["cleanup_sync"]
    assert shutdown_calls == ["shutdown", "shutdown"]


def test_signal_handler_waits_for_cleanup_already_running(monkeypatch) -> None:
    cleanup_calls: list[str] = []
    shutdown_calls: list[str] = []
    shutdown_states: list[tuple[list[str], bool]] = []
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()

    monkeypatch.setattr(gui_cleanup, "_shutdown_requested", threading.Event())
    monkeypatch.setattr(gui_cleanup, "_cleanup_completed", threading.Event())
    monkeypatch.setattr(gui_cleanup, "_shutdown_lock", threading.Lock())

    def cleanup_sync() -> None:
        cleanup_started.set()
        release_cleanup.wait(timeout=1.0)
        cleanup_calls.append("cleanup_sync")

    monkeypatch.setattr(gui_cleanup, "cleanup_application_sync", cleanup_sync)
    def shutdown() -> None:
        shutdown_states.append((list(cleanup_calls), gui_cleanup._cleanup_completed.is_set()))
        shutdown_calls.append("shutdown")

    monkeypatch.setattr(gui_cleanup, "app", SimpleNamespace(shutdown=shutdown))

    worker = threading.Thread(target=gui_cleanup.cleanup_application)
    worker.start()
    assert cleanup_started.wait(timeout=1.0) is True

    release_timer = threading.Timer(0.05, release_cleanup.set)
    release_timer.start()
    gui_cleanup.signal_handler(15, None)
    worker.join(timeout=1.0)
    release_timer.join(timeout=1.0)

    assert cleanup_calls == ["cleanup_sync"]
    assert shutdown_calls == ["shutdown"]
    assert shutdown_states == [(["cleanup_sync"], True)]
    assert gui_cleanup._cleanup_completed.is_set() is True
