from __future__ import annotations
import asyncio
import threading
from typing import Optional, Any, Literal, Callable
from nicegui import ui, background_tasks, core, app, Client
from nicegui.version import __version__

NotifyKind = Literal['positive', 'negative', 'warning', 'info', 'ongoing']
NotifyPosition = Literal[
    'top-left',
    'top-right',
    'bottom-left',
    'bottom-right',
    'top',
    'bottom',
    'left',
    'right',
    'center',
]

_DELETED_PARENT_SLOT_ERROR_FRAGMENTS = (
    'parent slot of the element has been deleted',
    'parent element this slot belongs to has been deleted',
)

# --- Tab helpers: dynamic title & favicon per client/all clients ---
def set_tab(title: str | None = None, icon_url: str | None = None, client: Optional[Client] = None) -> None:
    """Set browser tab title and/or favicon on the given or current client.

    Safe no-op if no client context is available.
    """
    # If a client is provided, use it; otherwise, run in the current client context
    c = client
    js_parts: list[str] = []
    if title is not None:
        try:
            if c is not None and hasattr(c, 'title'):
                c.title = title
            elif getattr(ui.context, 'client', None) is not None:
                ui.context.client.title = title
        except Exception:
            pass
        js_parts.append(f'document.title = {title!r};')
    if icon_url is not None:
        js_parts.append(
            (
                "const head = document.head || document.getElementsByTagName('head')[0];\n"
                "for (const rel of ['icon','shortcut icon','apple-touch-icon']) {\n"
                "  let link = document.querySelector(`link[rel=\"${rel}\"]`);\n"
                "  if (!link) { link = document.createElement('link'); link.rel = rel; head.appendChild(link); }\n"
                f"  link.href = {icon_url!r};\n"
                "}\n"
            )
        )
    if js_parts:
        code = '\n'.join(js_parts)
        try:
            if c is not None and hasattr(c, 'run_javascript'):
                c.run_javascript(code)
            else:
                # Executes on the current client's context (if any)
                ui.run_javascript(code)
        except Exception:
            pass


def set_tab_all(title: str | None = None, icon_url: str | None = None) -> None:
    """Broadcast title and/or favicon update to all connected clients."""
    clients_dict = getattr(app, 'clients', {})
    try:
        clients = list(getattr(clients_dict, 'values', lambda: [])())
    except Exception:
        clients = []
    for c in clients:
        set_tab(title, icon_url, client=c)


def set_favicon_default_all() -> None:
    """Restore the default favicon for all clients if configured in app.storage.general."""
    try:
        default_icon = get_default_favicon_url()
        if default_icon:
            set_tab_all(icon_url=str(default_icon))
    except Exception:
        pass


def get_default_favicon_url() -> str:
    """Return the configured default favicon URL or NiceGUI's built-in fallback."""
    try:
        default_icon = app.storage.general.get('cvd.default_favicon')
        if default_icon:
            return str(default_icon)
    except Exception:
        pass
    return f'/_nicegui/{__version__}/static/favicon.ico'


def _svg_data_url(svg: str) -> str:
    # Escape URL-fragment markers so inline hex colors survive as data URLs.
    return 'data:image/svg+xml;utf8,' + svg.replace('#', '%23')


def favicon_check_circle_green() -> str:
    """Green check_circle style SVG data URL."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 24 24'>"
        "<circle cx='12' cy='12' r='10' fill='#22c55e'/>"
        "<path d='M9 12.5l2 2 4-4' stroke='white' stroke-width='2' fill='none' stroke-linecap='round' stroke-linejoin='round'/>"
        "</svg>"
    )
    return _svg_data_url(svg)


def favicon_highlight_off_red() -> str:
    """Red highlight_off style SVG data URL."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 24 24'>"
        "<circle cx='12' cy='12' r='10' fill='#ef4444'/>"
        "<path d='M8 8l8 8M16 8l-8 8' stroke='white' stroke-width='2' stroke-linecap='round'/>"
        "</svg>"
    )
    return _svg_data_url(svg)


def favicon_sensors_off_orange() -> str:
    """Orange sensors_off style SVG data URL."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 24 24'>"
        "<circle cx='12' cy='12' r='10' fill='#f59e0b'/>"
        "<path d='M8 8a5.65 5.65 0 0 1 8 8' stroke='white' stroke-width='1.8' fill='none' stroke-linecap='round'/>"
        "<path d='M6 4l12 16' stroke='white' stroke-width='2' stroke-linecap='round'/>"
        "</svg>"
    )
    return _svg_data_url(svg)


def favicon_radio_button_checked_neutral() -> str:
    """Neutral radio_button_checked style SVG data URL."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 24 24'>"
        "<circle cx='12' cy='12' r='10' fill='#f8fafc' stroke='#cbd5e1' stroke-width='1.5'/>"
        "<circle cx='12' cy='12' r='4.25' fill='#ffffff' stroke='#e2e8f0' stroke-width='1'/>"
        "</svg>"
    )
    return _svg_data_url(svg)


class _ScheduledTaskHandle:
    """Lightweight proxy for tasks scheduled onto the NiceGUI loop from another thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._task: Any = None
        self._timer: Any = None
        self._cancelled = False
        self._failed = False
        self._close_callback: Callable[[], None] | None = None

    def set_close_callback(self, close_callback: Callable[[], None]) -> None:
        with self._lock:
            self._close_callback = close_callback

    def attach(self, task: Any) -> None:
        task_to_cancel = None
        with self._lock:
            if self._cancelled:
                task_to_cancel = task
            else:
                self._task = task
                self._timer = None
                self._failed = False
                return
        cancel_task_safely(task_to_cancel)

    def attach_timer(self, timer: Any) -> None:
        timer_to_cancel = None
        with self._lock:
            if self._cancelled:
                timer_to_cancel = timer
            else:
                self._timer = timer
                self._failed = False
                return
        cancel_task_safely(timer_to_cancel)

    def clear_timer(self, timer: Any | None = None) -> None:
        with self._lock:
            if timer is None or self._timer is timer:
                self._timer = None

    def mark_failed(self) -> None:
        with self._lock:
            self._failed = True
            self._timer = None

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        task_to_cancel = None
        timer_to_cancel = None
        close_callback = None
        with self._lock:
            self._cancelled = True
            task_to_cancel = self._task
            timer_to_cancel = self._timer
            close_callback = self._close_callback
            self._task = None
            self._timer = None
        cancel_task_safely(task_to_cancel)
        cancel_task_safely(timer_to_cancel)
        if task_to_cancel is None and callable(close_callback):
            close_callback()

    def done(self) -> bool:
        with self._lock:
            task = self._task
            timer = self._timer
            cancelled = self._cancelled
            failed = self._failed
        if task is not None and hasattr(task, 'done'):
            try:
                return bool(task.done())
            except Exception:
                return False
        if timer is not None:
            return False
        return cancelled or failed


def _schedule_bg_via_timer(coroutine: Any, *, task_name: str) -> _ScheduledTaskHandle:
    """Defer coroutine scheduling until a NiceGUI timer callback can create the task."""
    handle = _ScheduledTaskHandle()

    def _close_coroutine() -> None:
        try:
            coroutine.close()
        except Exception:
            pass

    handle.set_close_callback(_close_coroutine)
    timer: Any | None = None

    def _create_task_from_timer() -> None:
        handle.clear_timer(timer)
        if handle.is_cancelled():
            _close_coroutine()
            return
        try:
            task = background_tasks.create_lazy(coroutine, name=task_name)
        except Exception:
            handle.mark_failed()
            _close_coroutine()
            raise
        handle.attach(task)

    timer = ui.timer(0.0, _create_task_from_timer, once=True)
    handle.attach_timer(timer)
    return handle


def schedule_bg(coroutine: Any, name: Optional[str] = None) -> Optional[Any]:
    """Safely schedule a coroutine on NiceGUI's event loop.

    If the NiceGUI loop is not yet available (core.loop is None), defer scheduling
    using ui.timer so it runs once the loop is ready. Returns the created background
    task when scheduled immediately, or a `_ScheduledTaskHandle` when scheduling
    is deferred.
    """
    task_name = name or 'bg_task'
    try:
        if core.loop is None:
            return _schedule_bg_via_timer(coroutine, task_name=task_name)

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is core.loop:
            return background_tasks.create_lazy(coroutine, name=task_name)

        handle = _ScheduledTaskHandle()

        def _close_coroutine() -> None:
            try:
                coroutine.close()
            except Exception:
                pass

        handle.set_close_callback(_close_coroutine)

        def _schedule_timer_retry() -> None:
            timer: Any | None = None

            def _create_task_from_timer() -> None:
                handle.clear_timer(timer)
                if handle.is_cancelled():
                    _close_coroutine()
                    return
                try:
                    task = background_tasks.create_lazy(coroutine, name=task_name)
                except AssertionError:
                    handle.mark_failed()
                    _close_coroutine()
                    return
                except Exception:
                    handle.mark_failed()
                    _close_coroutine()
                    raise
                handle.attach(task)

            timer = ui.timer(0.0, _create_task_from_timer, once=True)
            handle.attach_timer(timer)

        def _create_task_threadsafe() -> None:
            if handle.is_cancelled():
                _close_coroutine()
                return
            try:
                task = background_tasks.create_lazy(coroutine, name=task_name)
            except AssertionError:
                _schedule_timer_retry()
                return
            except Exception:
                handle.mark_failed()
                _close_coroutine()
                raise
            handle.attach(task)

        core.loop.call_soon_threadsafe(_create_task_threadsafe)
        return handle
    except (AssertionError, RuntimeError):
        # In case scheduling asserts due to loop not being ready, defer via timer
        return _schedule_bg_via_timer(coroutine, task_name=task_name)


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


def register_client_disconnect_handler(
    client: Any,
    handler: Callable[[], None],
    *,
    logger: Optional[Any] = None,
    attr_name: Optional[str] = None,
) -> bool:
    """Register a disconnect handler when the client supports it.

    Returns True when the handler was registered successfully. Optional client
    attributes are only written after successful registration.
    """
    if client is None:
        return False

    on_disconnect = getattr(client, 'on_disconnect', None)
    if not callable(on_disconnect):
        if logger is not None:
            logger.warning('Client does not support on_disconnect; cleanup may not run automatically')
        return False

    try:
        on_disconnect(handler)
    except Exception:
        if logger is not None:
            logger.debug('Failed to register disconnect handler', exc_info=True)
        return False

    if attr_name:
        try:
            setattr(client, attr_name, handler)
        except Exception:
            if logger is not None:
                logger.debug('Failed to store disconnect handler on client', exc_info=True)

    return True


def notify_user(
    message: str,
    *,
    kind: NotifyKind = 'info',
    position: NotifyPosition = 'bottom-right',
) -> None:
    """Show a user-facing toast with a consistent default placement."""
    ui.notify(message, type=kind, position=position)


def is_deleted_parent_slot_error(exc: BaseException) -> bool:
    """Return True for the known NiceGUI parent-slot deletion timer errors."""
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc).lower()
    return any(fragment in message for fragment in _DELETED_PARENT_SLOT_ERROR_FRAGMENTS)

def safe_ui_operation(
    operation: Any,
    error_msg: str = "Operation failed",
    success_msg: Optional[str] = None,
    logger: Optional[Any] = None
) -> None:
    """
    Executes a UI operation safely, handling exceptions and notifying the user.
    
    Args:
        operation: Callable to execute.
        error_msg: Message to show/log on error.
        success_msg: Optional message to show on success.
        logger: Optional logger to record errors.
    """
    try:
        operation()
        if success_msg:
            notify_user(success_msg, kind='positive')
    except Exception as e:
        if logger:
            logger.error(f"{error_msg}: {e}")
        notify_user(f"{error_msg}: {e}", kind='negative')
