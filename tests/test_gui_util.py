from unittest.mock import Mock

from src.gui import util


def test_schedule_bg_returns_task_when_loop_is_ready(monkeypatch) -> None:
    coroutine = object()
    task = object()
    create_lazy = Mock(return_value=task)
    timer = Mock()

    monkeypatch.setattr(util.core, "loop", object(), raising=False)
    monkeypatch.setattr(util.background_tasks, "create_lazy", create_lazy)
    monkeypatch.setattr(util.ui, "timer", timer)

    result = util.schedule_bg(coroutine, name="save_test")

    assert result is task
    create_lazy.assert_called_once_with(coroutine, name="save_test")
    timer.assert_not_called()


def test_schedule_bg_returns_timer_when_loop_is_not_ready(monkeypatch) -> None:
    coroutine = object()
    timer_handle = object()
    create_lazy = Mock()
    timer = Mock(return_value=timer_handle)

    monkeypatch.setattr(util.core, "loop", None, raising=False)
    monkeypatch.setattr(util.background_tasks, "create_lazy", create_lazy)
    monkeypatch.setattr(util.ui, "timer", timer)

    result = util.schedule_bg(coroutine, name="save_test")

    assert result is timer_handle
    create_lazy.assert_not_called()
    timer.assert_called_once()

    delay, callback = timer.call_args.args
    assert delay == 0.0
    assert timer.call_args.kwargs == {"once": True}

    callback()
    create_lazy.assert_called_once_with(coroutine, name="save_test")


def test_schedule_bg_returns_timer_when_create_lazy_asserts(monkeypatch) -> None:
    coroutine = object()
    timer_handle = object()
    create_lazy = Mock(side_effect=AssertionError)
    timer = Mock(return_value=timer_handle)

    monkeypatch.setattr(util.core, "loop", object(), raising=False)
    monkeypatch.setattr(util.background_tasks, "create_lazy", create_lazy)
    monkeypatch.setattr(util.ui, "timer", timer)

    result = util.schedule_bg(coroutine, name="save_test")

    assert result is timer_handle
    create_lazy.assert_called_once_with(coroutine, name="save_test")
    timer.assert_called_once()


def test_cancel_task_safely_cancels_timer_like_handle() -> None:
    class _TimerHandle:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    handle = _TimerHandle()

    util.cancel_task_safely(handle)

    assert handle.cancelled is True
