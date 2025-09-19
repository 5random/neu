from __future__ import annotations
from typing import Optional, Any
from nicegui import ui, background_tasks, core


def schedule_bg(coroutine: Any, name: Optional[str] = None):
    """Safely schedule a coroutine on NiceGUI's event loop.

    If the NiceGUI loop is not yet available (core.loop is None), defer scheduling
    using ui.timer so it runs once the loop is ready. Returns the created task or None
    when scheduling is deferred.
    """
    task_name = name or 'bg_task'
    try:
        if core.loop is None:
            ui.timer(0.0, lambda: background_tasks.create_lazy(coroutine, name=task_name), once=True)
            return None
        return background_tasks.create_lazy(coroutine, name=task_name)
    except AssertionError:
        # In case create/create_lazy asserts due to loop not being ready, defer via timer
        ui.timer(0.0, lambda: background_tasks.create_lazy(coroutine, name=task_name), once=True)
        return None


def cancel_task_safely(task: Any) -> None:
    """Attempt to cancel an asyncio.Task or NiceGUI TaskProxy without raising.

    Accepts None and objects without done()/cancel() gracefully.
    """
    if not task:
        return
    try:
        if hasattr(task, 'done'):
            if not task.done():
                task.cancel()
        else:
            task.cancel()
    except Exception:
        # Best-effort; ignore any cancellation errors
        pass
