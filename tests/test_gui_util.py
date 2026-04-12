import inspect
from types import SimpleNamespace

import pytest

from src.gui import util as gui_util
from src.gui.util import (
    _svg_data_url,
    favicon_check_circle_green,
    favicon_highlight_off_red,
    favicon_radio_button_checked_neutral,
    favicon_sensors_off_orange,
    register_client_disconnect_handler,
    schedule_bg,
)


def test_svg_data_url_escapes_hash_characters() -> None:
    url = _svg_data_url("<svg fill='#22c55e' stroke='#ef4444'/>")

    assert url.startswith("data:image/svg+xml;utf8,")
    assert "%2322c55e" in url
    assert "%23ef4444" in url
    assert "#22c55e" not in url
    assert "#ef4444" not in url


def test_favicon_helpers_return_svg_data_urls_with_escaped_hex_colors() -> None:
    green = favicon_check_circle_green()
    red = favicon_highlight_off_red()
    orange = favicon_sensors_off_orange()
    neutral = favicon_radio_button_checked_neutral()

    assert green.startswith("data:image/svg+xml;utf8,")
    assert red.startswith("data:image/svg+xml;utf8,")
    assert orange.startswith("data:image/svg+xml;utf8,")
    assert neutral.startswith("data:image/svg+xml;utf8,")
    assert "%2322c55e" in green
    assert "%23ef4444" in red
    assert "%23f59e0b" in orange
    assert "%23f8fafc" in neutral


class _LoggerStub:
    def __init__(self) -> None:
        self.warning_calls: list[str] = []
        self.debug_calls: int = 0

    def warning(self, message: str) -> None:
        self.warning_calls.append(message)

    def debug(self, message: str, *args, **kwargs) -> None:
        self.debug_calls += 1


class _DisconnectClient:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def on_disconnect(self, handler) -> None:
        self.handlers.append(handler)


class _FailingDisconnectClient:
    def on_disconnect(self, handler) -> None:
        raise RuntimeError(f"boom: {handler}")


def test_register_client_disconnect_handler_registers_and_sets_attribute() -> None:
    client = _DisconnectClient()
    logger = _LoggerStub()

    def _handler() -> None:
        return None

    assert register_client_disconnect_handler(client, _handler, logger=logger, attr_name='cleanup_handler') is True
    assert client.handlers == [_handler]
    assert getattr(client, 'cleanup_handler') is _handler
    assert logger.warning_calls == []
    assert logger.debug_calls == 0


def test_register_client_disconnect_handler_returns_false_without_on_disconnect() -> None:
    client = SimpleNamespace()
    logger = _LoggerStub()

    def _handler() -> None:
        return None

    assert register_client_disconnect_handler(client, _handler, logger=logger, attr_name='cleanup_handler') is False
    assert not hasattr(client, 'cleanup_handler')
    assert logger.warning_calls == ['Client does not support on_disconnect; cleanup may not run automatically']
    assert logger.debug_calls == 0


def test_register_client_disconnect_handler_does_not_set_attribute_when_registration_fails() -> None:
    client = _FailingDisconnectClient()
    logger = _LoggerStub()

    def _handler() -> None:
        return None

    assert register_client_disconnect_handler(client, _handler, logger=logger, attr_name='cleanup_handler') is False
    assert not hasattr(client, 'cleanup_handler')
    assert logger.warning_calls == []
    assert logger.debug_calls == 1


class _TaskStub:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def done(self) -> bool:
        return self.cancelled


class _TimerStub:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def test_schedule_bg_uses_threadsafe_loop_bridge_outside_event_loop(monkeypatch) -> None:
    callback_invocations = 0
    created_names: list[str | None] = []
    task = _TaskStub()

    class _LoopStub:
        def call_soon_threadsafe(self, callback) -> None:
            nonlocal callback_invocations
            callback_invocations += 1
            callback()

    async def _sample() -> None:
        return None

    def _create_lazy(coroutine, name=None):
        created_names.append(name)
        coroutine.close()
        return task

    monkeypatch.setattr(gui_util.core, 'loop', _LoopStub())
    monkeypatch.setattr(gui_util, 'background_tasks', SimpleNamespace(create_lazy=_create_lazy))

    handle = schedule_bg(_sample(), name='threadsafe-bg-task')

    assert callback_invocations == 1
    assert created_names == ['threadsafe-bg-task']
    assert handle is not None
    handle.cancel()
    assert task.cancelled is True


def test_schedule_bg_returns_handle_and_schedules_task_when_loop_is_missing(monkeypatch) -> None:
    task = _TaskStub()
    timers: list[_TimerStub] = []
    created_names: list[str | None] = []

    async def _sample() -> None:
        return None

    def _create_lazy(coroutine, name=None):
        created_names.append(name)
        coroutine.close()
        return task

    def _create_timer(_interval, callback, once=False):
        timer = _TimerStub(callback)
        timers.append(timer)
        return timer

    monkeypatch.setattr(gui_util.core, 'loop', None)
    monkeypatch.setattr(gui_util, 'background_tasks', SimpleNamespace(create_lazy=_create_lazy))
    monkeypatch.setattr(gui_util, 'ui', SimpleNamespace(timer=_create_timer))

    handle = schedule_bg(_sample(), name='no-loop-bg-task')

    assert isinstance(handle, gui_util._ScheduledTaskHandle)
    assert len(timers) == 1
    assert handle.done() is False

    timers[0].callback()

    assert created_names == ['no-loop-bg-task']
    assert handle.done() is False


def test_schedule_bg_cancel_closes_coroutine_before_no_loop_timer_runs(monkeypatch) -> None:
    timers: list[_TimerStub] = []

    async def _sample() -> None:
        return None

    def _create_timer(_interval, callback, once=False):
        timer = _TimerStub(callback)
        timers.append(timer)
        return timer

    coroutine = _sample()
    monkeypatch.setattr(gui_util.core, 'loop', None)
    monkeypatch.setattr(
        gui_util,
        'background_tasks',
        SimpleNamespace(create_lazy=lambda coroutine, name=None: _TaskStub()),
    )
    monkeypatch.setattr(gui_util, 'ui', SimpleNamespace(timer=_create_timer))

    handle = schedule_bg(coroutine, name='no-loop-bg-task')
    handle.cancel()

    assert isinstance(handle, gui_util._ScheduledTaskHandle)
    assert len(timers) == 1
    assert timers[0].cancelled is True
    assert inspect.getcoroutinestate(coroutine) == 'CORO_CLOSED'
    assert handle.done() is True


def test_schedule_bg_marks_failure_and_closes_coroutine_when_no_loop_timer_callback_fails(monkeypatch) -> None:
    timers: list[_TimerStub] = []

    async def _sample() -> None:
        return None

    def _create_timer(_interval, callback, once=False):
        timer = _TimerStub(callback)
        timers.append(timer)
        return timer

    coroutine = _sample()
    monkeypatch.setattr(gui_util.core, 'loop', None)
    monkeypatch.setattr(
        gui_util,
        'background_tasks',
        SimpleNamespace(create_lazy=lambda _coroutine, name=None: (_ for _ in ()).throw(RuntimeError(f'boom: {name}'))),
    )
    monkeypatch.setattr(gui_util, 'ui', SimpleNamespace(timer=_create_timer))

    handle = schedule_bg(coroutine, name='no-loop-bg-task')

    assert isinstance(handle, gui_util._ScheduledTaskHandle)
    assert len(timers) == 1

    with pytest.raises(RuntimeError, match='boom: no-loop-bg-task'):
        timers[0].callback()

    assert inspect.getcoroutinestate(coroutine) == 'CORO_CLOSED'
    assert handle.done() is True


def test_schedule_bg_retries_via_timer_when_cross_thread_create_lazy_asserts(monkeypatch) -> None:
    task = _TaskStub()
    timers: list[_TimerStub] = []
    create_lazy_calls = 0
    created_names: list[str | None] = []

    class _LoopStub:
        def call_soon_threadsafe(self, callback) -> None:
            callback()

    async def _sample() -> None:
        return None

    def _create_lazy(coroutine, name=None):
        nonlocal create_lazy_calls
        create_lazy_calls += 1
        if create_lazy_calls == 1:
            raise AssertionError('loop not ready yet')
        created_names.append(name)
        coroutine.close()
        return task

    def _create_timer(_interval, callback, once=False):
        timer = _TimerStub(callback)
        timers.append(timer)
        return timer

    monkeypatch.setattr(gui_util.core, 'loop', _LoopStub())
    monkeypatch.setattr(gui_util, 'background_tasks', SimpleNamespace(create_lazy=_create_lazy))
    monkeypatch.setattr(gui_util, 'ui', SimpleNamespace(timer=_create_timer))

    handle = schedule_bg(_sample(), name='threadsafe-bg-task')

    assert handle is not None
    assert create_lazy_calls == 1
    assert len(timers) == 1
    assert handle.done() is False

    timers[0].callback()

    assert create_lazy_calls == 2
    assert created_names == ['threadsafe-bg-task']
    assert handle.done() is False


def test_schedule_bg_cancel_cancels_deferred_timer_after_cross_thread_assertion(monkeypatch) -> None:
    timers: list[_TimerStub] = []

    class _LoopStub:
        def call_soon_threadsafe(self, callback) -> None:
            callback()

    async def _sample() -> None:
        return None

    def _create_lazy(_coroutine, name=None):
        raise AssertionError(f'loop not ready yet: {name}')

    def _create_timer(_interval, callback, once=False):
        timer = _TimerStub(callback)
        timers.append(timer)
        return timer

    monkeypatch.setattr(gui_util.core, 'loop', _LoopStub())
    monkeypatch.setattr(gui_util, 'background_tasks', SimpleNamespace(create_lazy=_create_lazy))
    monkeypatch.setattr(gui_util, 'ui', SimpleNamespace(timer=_create_timer))

    handle = schedule_bg(_sample(), name='threadsafe-bg-task')

    assert handle is not None
    assert len(timers) == 1
    assert timers[0].cancelled is False

    handle.cancel()

    assert timers[0].cancelled is True
